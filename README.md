# GeoNexus Reference Stack MVP

**GeoNexus** is an open, secure and inclusive federated geospatial intelligence
infrastructure. It is **not** another Google Earth Engine, it is not a global
data warehouse, and it is not a full GIS platform.

The core loop this MVP demonstrates:

```
GeoCard
   ↓
Registry / Discovery
   ↓
Contract validation
   ↓
GeoMCP
   ↓
Local GeoNode
   ↓
GeoSkill
   ↓
Result
```

## What is GeoCard?

GeoCard is the **machine-readable identity, capability and contract
description for a geospatial asset**. It is inspired by Hugging Face Model
Cards, Data Cards, STAC metadata and ISO/TC 211 / OGC API concepts, but it is
specifically designed for **AI-agent-readable** geospatial assets (Data,
Model, Skill, Agent, Workflow, Knowledge, Compute). A GeoCard is validated
against the official JSON Schema in `schemas/geocard.schema.json`, and its
**ContractValidator** checks whether a card actually satisfies a request
(CRS, bbox, temporal window, bands, resolution).

## What is GeoMCP?

GeoMCP is the **geospatial interaction protocol** of GeoNexus — an
MCP-compatible geospatial extension layer, implemented for the MVP as a
lightweight **JSON-RPC 2.0** protocol (`geo.capabilities`, `geo.describe`,
`geo.execute`, `geo.health`) with an HTTP transport (FastAPI) and a Python
client. It leaves room for an official MCP adapter later; it does not try to
recreate the entire MCP ecosystem.

## What is GeoNode?

GeoNode is a **sovereign cloud-native geospatial capability node**. The Local
GeoNode runs on your machine and provides a GeoCard registry, a Skill
registry, a GeoMCP server, a local execution runtime and a health endpoint.
The architecture deliberately keeps GeoMCP and GeoCard transport/contract
agnostic so that **GeoNode Federation** can be added later without rewriting
them — and the V0.3 federation layer is already included: a shared
**GeoCard Registry** for discovery plus **pushdown execution** that routes
requests to the node owning the data (`geonexus demo federated`).

## How they relate

1. A **GeoCard** describes a geospatial asset and its executable contract.
2. **Discovery / Contract validation** decides whether the asset satisfies a
   request.
3. **GeoMCP** carries the request to the node that owns the asset.
4. The **GeoNode** executes the request locally.
5. A **GeoSkill** is the reusable capability that performs the computation.
6. The **Result** (e.g. an NDVI raster + statistics) returns to the caller.

Data sovereignty is fundamental: *data stays where it is, computation moves
to the data*. GeoAgent will later become the orchestration layer.

## Quick start

### Install from PyPI (stable release)

```bash
pip install geonexus-sdk          # import package: import geonexus
pip install "geonexus-sdk[mcp]"   # + Model Context Protocol bridge support
```

### Install from source (development)

```bash
cd /path/to/GeoNexus-SDK
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Validate the demo card
.venv/bin/geonexus card validate examples/amazon_ndvi/geocard.yaml

# Run the whole Amazon NDVI demo (synthetic data)
.venv/bin/geonexus demo amazon-ndvi

# Run the federated demo (registry + data node + pushdown execution)
.venv/bin/geonexus demo federated

# Run the real-data demo (REAL Sentinel-2 NDVI via Planetary Computer STAC;
# needs network access; degrades gracefully when real pixels are blocked)
.venv/bin/geonexus demo stac-real

# Run the GeoAgent demo (V1.0: 3-step pipeline DAG + pushdown execution)
.venv/bin/geonexus demo agent

# Natural-language goal via an OpenAI-compatible LLM (needs GEONEXUS_LLM_API_KEY)
# .venv/bin/geonexus agent ask "分析亚马逊雨林 2015 与 2025 的植被变化"

# Run the MCP bridges (GeoMCP over Model Context Protocol)
.venv/bin/geonexus mcp run --bare        # stdio (local AI assistants)
.venv/bin/geonexus mcp serve --port 9000 # Streamable HTTP (remote clients)

# Persistent + authenticated registry (V1.0)
.venv/bin/geonexus registry start --persist registry.json --api-key topsecret

# Import real OGC API assets as GeoCards (standards interop, V1.0)
.venv/bin/geonexus card import-ogc https://demo.pygeoapi.io/master --collection lakes

# OGC write-side bridge: geo.execute drives a real OGC API - Processes process
.venv/bin/geonexus demo ogc-process --remote https://demo.pygeoapi.io/master --process hello-world

# OGC Coverages: raster retrieval (CoverageJSON -> NDVI -> GeoTIFF, offline mock)
.venv/bin/geonexus demo ogc-coverage

# GeoAgent pipeline mixing local skills + remote OGC process skills
.venv/bin/geonexus demo ogc-pipeline

# GeoNode-to-GeoNode federation: relay delegation + health-aware routing
.venv/bin/geonexus demo federation-deep

# Registry federation + GGIHS: cross-registry catalog/health aggregation
.venv/bin/geonexus demo ggihs

# STAC write side: publish GeoCards as STAC Items (round trip)
.venv/bin/geonexus demo stac-write

# Run the tests (460 tests)
.venv/bin/pytest
```

See `docs/QUICKSTART.md` for the full 10-minute walkthrough, including
starting a live node and calling it with `curl`.

## Run the services locally

The three long-running services are separate processes with separate ports, so
you can start only what you need:

```bash
# 1. Shared GeoCard Registry — discovery layer          (default port 8790)
.venv/bin/geonexus registry start --port 8790 --persist registry.json --health-probe

# 2. Local GeoNode over GeoMCP — the execution plane    (default port 8787)
.venv/bin/geonexus node start --port 8787

# 3. Web BFF — JWT auth, async tasks, SSE progress      (default port 8900)
.venv/bin/geonexus web start --port 8900 \
  --registry http://127.0.0.1:8790 \
  --node     http://127.0.0.1:8787 \
  --user     admin=admin
```

`--health-probe` makes `GET /nodes` actively probe each node instead of
reporting `healthy: null` (unknown). Note that a node only appears there once
something of its is registered, so a fresh registry reports `count: 0` — either
pass `--registry http://127.0.0.1:8790` to `node start` to advertise its demo
cards, or register skills/cards explicitly:

```bash
.venv/bin/geonexus registry skill register ndvi-analysis \
  --url http://127.0.0.1:8790 --node http://127.0.0.1:8787
.venv/bin/geonexus registry register examples/amazon_ndvi/geocard.yaml \
  --url http://127.0.0.1:8790 --node http://127.0.0.1:8787
```

> ⚠️ **`--user` is required for anything behind auth.** `WebConfig.users` has no
> default and there is no environment fallback, so without it `POST
> /api/auth/login` can only answer `Invalid credentials` and every
> JWT-protected route is unreachable. The flag is repeatable and splits on the
> first `=`, so passwords may contain `=`. When it is omitted the command prints
> a warning saying so.

The Web BFF exposes 16 routes under `/api` (`/docs` has the OpenAPI page):

| Group | Routes |
|-------|--------|
| Auth | `POST /api/auth/login` |
| Read (JWT) | `GET /api/{health,cards,skills,nodes,tasks}` |
| Execute (JWT) | `POST /api/execute`, `POST /api/goals` |
| Tasks (JWT) | `GET /api/tasks/{id}`, `POST /api/tasks/{id}/cancel`, `GET /api/tasks/{id}/stream` (SSE) |
| Data registration (JWT) | `GET /api/datasets/pending`, `POST /api/datasets/upload`, `POST /api/datasets/{id}/submit`, `POST /api/datasets/{card_id}/{approve,reject}` |

`POST /api/execute` and `POST /api/goals` return `202` with a `task_id`
immediately — a GeoNode call can take minutes, so results are fetched from
`/api/tasks/{id}` or streamed from `/api/tasks/{id}/stream`.

A round trip, end to end:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8900/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' | python -c 'import json,sys;print(json.load(sys.stdin)["token"])')

TASK=$(curl -s -X POST http://127.0.0.1:8900/api/execute \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"skill":"buffer-analysis","params":{"geometry":{"type":"Point","coordinates":[100.5,13.75]},"distance":0.01}}' \
  | python -c 'import json,sys;print(json.load(sys.stdin)["task_id"])')

curl -s http://127.0.0.1:8900/api/tasks/$TASK -H "Authorization: Bearer $TOKEN"
```

## Repository layout

```
GeoNexus-SDK/
├── schemas/geocard.schema.json   # Official GeoCard JSON Schema (draft 2020-12)
├── src/geonexus/
│   ├── geocard/                  # model, builder, loader, validator (contract)
│   ├── geomcp/                   # protocol, models, server, client
│   ├── geonode/                  # node, registry, runtime, skill
│   ├── gaag/                     # GAAG contract registry (scan→register→gate)
│   ├── registry/                 # shared Registry: cards+skills, persistence+auth
│   ├── federation/               # pushdown execution (V0.3)
│   ├── cafe/                     # CAFE: computation-to-data pushdown engine
│   ├── agent/                    # GeoAgent: planner + pipeline DAG + LLM translator
│   ├── kg/                       # generic geospatial knowledge-graph primitives
│   ├── ggihs/                    # observation plane: health/catalog aggregation
│   ├── security/                 # security gateway: OPA-style policy + zero trust
│   ├── web/                      # Web BFF: JWT auth, task manager, SSE, datasets
│   ├── adapters/                 # STAC + OGC API read/write adapters (V0.4/V1.0)
│   ├── mcp_adapter.py            # official MCP SDK bridge: stdio + HTTP (V0.5/V1.0)
│   └── cli.py                    # `geonexus` command line interface
├── examples/
│   ├── local_node.py             # minimal embedded node example
│   ├── amazon_ndvi/              # end-to-end NDVI demo (synthetic data)
│   ├── federated/                # registry + data node + pushdown demo
│   ├── federation_deep/          # node delegation + health-aware routing (V1.0+)
│   ├── ggihs/                    # cross-registry health/catalog aggregation (V1.0+)
│   ├── stac/                     # STAC Item examples
│   ├── stac_real/                # real Sentinel-2 NDVI demo (V0.4)
│   ├── stac_write/               # GeoCard -> STAC Item round trip (V1.0+)
│   ├── agent/                    # pipeline DAG demo (V1.0)
│   ├── ogc_process/              # OGC write-side bridge demo (V1.0)
│   ├── ogc_coverage/             # OGC Coverages raster demo (V1.0)
│   └── ogc_pipeline/             # OGC skills in GeoAgent pipelines (V1.0)
├── tests/                        # pytest suite (460 tests)
└── docs/                         # architecture & component documentation
```

## Documentation

- `docs/ARCHITECTURE.md` — why GeoNexus is federated, why data stays at
  sovereign nodes, relationship with MCP / OGC API / STAC, "move computation
  to data".
- `docs/GEOCARD.md` — full GeoCard field specification.
- `docs/GEOMCP.md` — JSON-RPC structure, methods, geospatial context, execution model.
- `docs/GEONODE.md` — Local GeoNode, future federation, node asset types.
- `docs/FEDERATION.md` — the shared registry + pushdown + delegation + registry federation.
- `docs/GGIHS.md` — cross-registry health/catalog aggregation (V1.0+).
- `docs/AGENT.md` — the GeoAgent planner: DAG pipelines + LLM goal translation.
- `docs/MCP.md` — the official MCP SDK bridge (stdio + Streamable HTTP).
- `docs/OGC.md` — the OGC API Features/Processes adapter (standards interop).
- `docs/STAC.md` — the STAC adapter: read (import) + write (export) sides.
- `docs/API.md` — Python SDK public API reference.
- `docs/API_STABILITY.md` — frozen public API + deprecation policy (v1.0).
- `docs/GAAG.md` — GAAG contract registry: scan, embed, register, semantic search, contract gating.
- `docs/QUICKSTART.md` — run the entire demo in less than 10 minutes.
- `docs/MVP_IMPLEMENTATION.md` — what was built, acceptance checks, limitations.
- `CHANGELOG.md` — release history.

## License

Apache License 2.0. See `LICENSE`.
