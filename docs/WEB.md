# GeoNexus Web layer (BFF) — v1.1

The `geonexus.web` module turns the SDK into a backend-for-frontend (BFF)
for browser applications: JWT auth, async tasks, SSE progress, and
server-side node credential forwarding. It is the recommended way to build
Web apps on top of GeoNexus.

```
Browser ──JWT──▶ Web backend (geonexus.web) ──X-API-Key──▶ GeoNode(s)
                        │                                     │
                        └────────── Registry ────────────────┘
```

## Quick start

```python
from geonexus.web import WebConfig, JWTConfig, create_web_app

config = WebConfig(
    registry_url="http://127.0.0.1:8790",
    jwt=JWTConfig(secret="a-32+byte-shared-secret-for-hs256"),
    users={"alice": "password"},                 # demo login store
    node_api_keys={"http://127.0.0.1:8787": "node-secret"},  # BFF → node
    default_node_url="http://127.0.0.1:8787",
)
app = create_web_app(config, cors_origins=["http://localhost:5173"])
# uvicorn geonexus_web:app --port 8900
```

Or from the CLI once wired into the entry points:

```bash
geonexus web start --registry http://127.0.0.1:8790 --port 8900
```

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/login` | — | Exchange username/password for a JWT |
| GET | `/api/health` | — | Service health |
| GET | `/api/cards?q=&bbox=` | Bearer | Registry card search / list |
| GET | `/api/skills` | Bearer | Registry skill list |
| GET | `/api/nodes` | Bearer | Registry node view |
| POST | `/api/execute` | Bearer | Run a skill on a node (async, 202) |
| POST | `/api/goals` | Bearer | LLM-plan a natural-language goal (async, 202; reflective execution + self-assessment by default) |
| GET | `/api/tasks` | Bearer | List tasks |
| GET | `/api/tasks/{id}` | Bearer | Task state (+ result when done) |
| GET | `/api/tasks/{id}/stream` | Bearer | SSE progress stream |
| POST | `/api/tasks/{id}/cancel` | Bearer | Request cancellation |

### Example: full flow

```bash
# 1. Login
TOKEN=$(curl -s -X POST localhost:8900/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"alice","password":"password"}' | jq -r .token)

# 2. Search cards
curl -s localhost:8900/api/cards?q=ndvi -H "Authorization: Bearer $TOKEN"

# 3. Run a skill (returns a task id)
curl -s -X POST localhost:8900/api/execute \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"skill":"ndvi","geocards":["amazon-ndvi-2025"],"params":{"bands":["B4","B8"]}}'
# → {"task_id":"abc123","status":"queued"}

# 4. Poll or stream
curl -sN localhost:8900/api/tasks/abc123/stream -H "Authorization: Bearer $TOKEN"
```

## Auth model (BFF + JWT)

- **Browser clients hold only a JWT.** Node credentials never leave the
  backend: `node_api_keys` maps `node_url → api_key`, forwarded as
  `X-API-Key` on GeoMCP calls by the router.
- **Stateless tokens** — no session store, so any backend instance can
  verify any token (horizontally scalable).
- **HS256** (shared secret) for a single trust domain; **RS256** (key pair)
  for cross-domain / multi-tenant setups where only the issuer signs and
  verifiers hold the public key. See `JWTConfig`.

### Node-level auth (GeoMCP)

GeoMCPServer enforces node auth symmetrically with the registry: when
`api_keys` is set, `geo.execute` requires `X-API-Key`; discovery methods
(`geo.capabilities`, `geo.describe`, `geo.health`) stay open.

```python
server = GeoMCPServer(..., api_keys={"node-secret"})
# read methods: open · geo.execute: requires X-API-Key
```

`GeoMCPClient(api_key=...)` and `FederatedGeoMCPClient(api_key=...)` forward
the header automatically. For node-to-node delegation, use
`forward_api_key` — the credential this node presents to *other* nodes
(independent of its own inbound `api_keys`), enabling per-node key models
in federated deployments.

## Async tasks

`run_goal` / `execute` are synchronous SDK calls; the Web layer runs them on
a background thread pool (`TaskManager`) and returns a `task_id` immediately.

- `queued → running → done | failed | cancelled`
- `GET /api/tasks/{id}` returns `result` only when done.
- Cooperative cancellation: workers declare a first parameter named
  `task_id` and call `task_manager.should_cancel(task_id)` /
  `update_progress(task_id, p, msg)`.
- `GET /api/tasks/{id}/stream` is SSE (`data: {json}\n\n` per state change).
- `TaskManager(persist=...)` accepts a callback for pluggable persistence
  (JSONL / Redis / Postgres) for horizontally scaled deployments.

## Reflective goals (v1.1)

`POST /api/goals` runs a natural-language request through the full v1.1
stack by default:

1. **Registry-grounded planning** — the skills that actually exist at the
   registry are discovered first, and the LLM translates the request into a
   `Goal` using only those skills (`plan_from_text_with_registry`).
2. **Reflective execution** — the plan runs via `ReflectiveExecutor`; if a
   step fails, the LLM diagnoses it (with the context of completed steps)
   and proposes `retry` / `replace` / `skip` / `abort`, bounded by
   `max_reflections` (default 3).
3. **Self-assessment** — `evaluate_plan` reviews the finished plan against
   the goal; the result is attached to the task as `evaluation`
   (`{satisfied, score, notes}`).

The task result shape:

```json
{
  "goal": {...},
  "plan": {"goal": {...}, "registry": "...", "steps": [{"status": "...", "reflections": [...]}]},
  "reflective": true,
  "evaluation": {"satisfied": true, "score": 92, "notes": "..."}
}
```

Set `"reflective": false` in the request body for plain deterministic
execution (no repair, no evaluation).

## Deployment topologies

### A. Single machine (dev)

Registry + node + web backend on one host; `default_node_url` points at the
local node. Fastest path for evaluation.

### B. Data-sovereign (recommended for production)

Each data holder runs a GeoNode (own STAC / OGC / local files) with its own
API key; a shared (federated) registry handles discovery; the Web backend
orchestrates and pushdown-executes at the data's location. Computation moves
to data — raw data never leaves the owner's domain.

### C. Edge / no backend

Browsers talk to GeoMCP directly (CORS-enabled). Convenient for internal
tools; not for public exposure — node credentials would have to ship to the
browser, which the BFF model avoids.

## Security notes

- Default `WebConfig` uses a development JWT secret; **set your own** in
  production (`JWTConfig(secret=...)` or RS256 key pair).
- The demo `users` store is for evaluation; plug OIDC/SSO at the login
  endpoint in production.
- Read endpoints (registry discovery) stay open; writes (register, execute)
  are gated. Adjust per deployment policy.
