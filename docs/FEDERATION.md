# GeoNode Federation (V0.3 → V1.0+)

This document describes the federation layer built on the MVP: a **shared
GeoCard Registry** for discovery and **pushdown execution** that routes
requests to the node owning the data, plus **node-level delegation** and
**health-aware routing** (V1.0+).

## The principle

```
data stays where it is,
computation moves to the data.
```

The MVP proved the single-node loop (GeoCard → contract → GeoMCP → GeoNode →
GeoSkill → result). Federation extends it with one new component — a
**registry** — while keeping GeoMCP and GeoCard untouched.

## Architecture

```
              +----------------------+
              |  GeoCard Registry    |   cards + owning node endpoint only
              |  (discovery service) |   (never stores data)
              +----------+-----------+
                         ^
            advertise    |            search (contract pre-filter)
            (cards + URL)|            (capability/bbox/temporal/bands)
                         |            |
   +---------------------+-----+      v      +----------------------------+
   | GeoNode A (data node)     |            | Client / FederatedGeoMCPClient |
   | owns sentinel-2-amazon    |<-----------| resolve: who owns card X?     |
   | owns ndvi-analysis skill  |  pushdown  | route geo.execute to owner    |
   +---------------------------+  geo.execute ------------------------------+
```

1. **Advertise** — a node registers its GeoCards at the registry, each bound
   to the node's endpoint (`GeoNode.advertise(registry_url)` or
   `geonexus node start --registry URL`). Only *descriptions* leave the node;
   data and computation stay put.
2. **Discover** — a client asks the registry who owns a card
   (`GET /cards/{id}`) or searches with contract constraints
   (`GET /search?capability=ndvi&bbox=...&bands=...`). The registry applies
   the GeoCard `ContractValidator` as a **coarse pre-filter**.
3. **Pushdown** — `FederatedGeoMCPClient.execute(...)` resolves each card to
   its owning node and sends `geo.execute` **to that node**. When cards span
   multiple nodes, the request fans out per node. The owning node re-checks
   the contract **authoritatively** before running the skill.

## Components

| Component | Location | Role |
|-----------|----------|------|
| `RegistryServer` | `src/geonexus/registry/server.py` | HTTP service: `POST/GET/DELETE /cards`, `GET /search`, `GET /health`, plus skill endpoints `POST/GET/DELETE /skills` (V0.5) |
| `RegistryStore` | `src/geonexus/registry/store.py` | In-memory card store with contract-gated search + skill store |
| `RegistryClient` | `src/geonexus/registry/client.py` | HTTP client (register/unregister/get/list/search; skill register/get/list/search) |
| `FederatedGeoMCPClient` | `src/geonexus/federation/client.py` | Discovery + pushdown execution, multi-node fan-out, `execute_skill` skill-first routing |
| `GeoNode.advertise` | `src/geonexus/geonode/node.py` | Publish cards **and skills** (V0.5) to a registry |

## GeoSkill registry (V0.5)

Skills are first-class registry citizens: `GeoNode.advertise` also registers
each skill (name, description, input/output schemas, capabilities from the
skill's GeoCard) bound to the offering node. Clients can then discover
skills by capability or name and route execution **skill-first** without
knowing which node provides them:

```python
entry = client.search_skills(capability="ndvi")   # -> [{skill, node_url}]
result = client.execute_skill("ndvi-analysis", params={...})  # routed to owner
```

The GeoAgent planner (`docs/AGENT.md`) builds on this: capability →
skill discovery → contract-gated card matching → pushdown plan.

## CLI

```bash
# Registry
geonexus registry start                       # 127.0.0.1:8790
geonexus registry register card.yaml --url http://127.0.0.1:8790 --node http://127.0.0.1:8787
geonexus registry search --url http://127.0.0.1:8790 --capability ndvi --bbox=-73.9,-15,-44,5 --bands B04,B08

# Skill registry (V0.5)
geonexus registry skill register ndvi-analysis --url http://127.0.0.1:8790 --node http://127.0.0.1:8787 --capability ndvi
geonexus registry skill list --url http://127.0.0.1:8790 --capability ndvi

# Node with advertisement (cards + skills)
geonexus node start --registry http://127.0.0.1:8790

# Inspect any running node
geonexus inspector http://127.0.0.1:8787

# Federated demo (registry + data node + pushdown)
geonexus demo federated
```

## Demo

```bash
.venv/bin/geonexus demo federated
```

Starts a registry and a data node, advertises the demo card, discovers it
(including a contract-gated search), then executes the NDVI skill **on the
data node** via pushdown and writes `ndvi_2015.tif`, `ndvi_2025.tif`,
`ndvi_change.tif` (synthetic data, tagged `GEONEXUS_SYNTHETIC=TRUE`).

## Node-level delegation (V1.0+)

Any node bound to a shared registry is a **federated entry point**: a
`geo.execute` request that references GeoCards the node does **not** own is
delegated to the owning node (resolved via the registry) and the owner's
result is relayed back.

```python
node = GeoNode(name="relay", registry_url="http://127.0.0.1:8790")
# geo.execute(geocards=["sentinel-2-amazon"]) now runs on the data owner
```

Bind at startup: `geonexus node start --registry URL` enables delegation
automatically. Delegation is **one hop** — forwarded requests carry an
internal `__geonode_delegate` marker that prevents loops (the marker never
reaches skill handlers).

## Health-aware routing (V1.0+)

- `geonexus registry start --health-probe` makes the registry lazily probe
  each node's `/health` (short timeout, brief cache) and report `healthy`
  per node in `GET /nodes`.
- `FederatedGeoMCPClient.execute(..., skip_unhealthy=True)` excludes nodes
  the registry marks unhealthy; if that leaves no owner, it raises
  "All owning nodes are marked unhealthy".
- `FederatedGeoMCPClient.discover()` returns `{nodes, health}`.

```bash
geonexus demo federation-deep   # relay node B delegates to data node A;
                                # after stopping A, health flips and routing refuses
```

## Registry federation (V1.0+)

Registries can **sync catalogs with each other** (pull-based): a registry
configured with peers imports their cards and skills, keeping the **owning
node endpoints** intact — so discovery through any one registry sees the
whole federation and routing/pushdown keeps working. Sync is idempotent
(duplicates are updated in place; a dead peer is recorded in `errors`).

```bash
# Registry B is a federated peer of A:
geonexus registry start --port 8791 --peer http://127.0.0.1:8790

# Trigger a pull sync on B (also POST /sync, gated by --api-key when set):
geonexus registry sync --url http://127.0.0.1:8791
```

```python
from geonexus.registry import RegistryServer

reg_b = RegistryServer(name="b", peers=["http://127.0.0.1:8790"])
report = reg_b.sync_peers()   # {"cards_added": n, "cards_updated": n, ...}
```

## Security & sovereignty notes

- The registry stores **card metadata and node endpoints only** — no rasters,
  no credentials, no data payloads.
- Contract gating happens **twice**: coarsely at the registry (cheap filter
  for discovery) and authoritatively at the owning node (the execution
  boundary) — delegation preserves this: the owner re-checks the contract.
- **Persistence (V1.0)**: `geonexus registry start --persist registry.json`
  survives restarts (atomic JSON writes on every mutation).
- **Auth (V1.0)**: `--api-key KEY` requires `X-API-Key` on write endpoints
  (register/unregister and `POST /sync`); reads stay open so discovery is
  unauthenticated.
- **GGIHS** (`docs/GGIHS.md`) aggregates health/catalog across registries;
  registry federation extends this to the whole network.
- Future work: per-node identities, pushdown of *sub-requests* (e.g.
  per-tile execution), bidirectional sync with conflict resolution.
