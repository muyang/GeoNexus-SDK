# GGIHS — Global Geospatial Intelligence Health Service (V1.0+)

GGIHS is the **observation plane** of the GeoNexus network: it aggregates
the **catalog and health** of the whole federation across one or more
GeoCard Registries. It stores nothing — it reads and merges.

## Endpoints

| Endpoint | Content |
|----------|---------|
| `GET /` | **dashboard** — single-file HTML page (vanilla JS, no external deps) rendering summary cards, the node table with health badges and the catalog; auto-refreshes every 5s |
| `GET /health` | service health + aggregated summary counts |
| `GET /summary` | nodes / cards / skills counts + healthy rollup |
| `GET /nodes` | merged per-node view (cards, skills, healthy) |
| `GET /catalog` | deduplicated catalog (cards + skills, each with owning node + health) |

## Run it

```bash
# Aggregate one or more registries (repeat --registry):
geonexus ggihs start --registry http://127.0.0.1:8790 \
                     --registry http://127.0.0.1:8791 \
                     --port 8800 --live-probe

# Open the dashboard in a browser:
open http://127.0.0.1:8800/

curl http://127.0.0.1:8800/summary
curl http://127.0.0.1:8800/catalog
```

`--live-probe` probes each node's GeoMCP `/health` directly instead of
relying on registry-reported health.

## Python API

```python
from geonexus.ggihs import GGIHSService

service = GGIHSService(["http://127.0.0.1:8790", "http://127.0.0.1:8791"])
summary = service.summary()   # {"nodes": 2, "nodes_healthy": 1, "cards": 4, ...}
catalog = service.catalog()   # {"cards": [...], "skills": [...], "counts": {...}}
nodes = service.nodes()       # {node_url: {cards, skills, healthy}}
```

## Demo

```bash
geonexus demo ggihs
```

Topology: registry A + node A (data), registry B (**federated peer of A**,
synced) + node B, GGIHS aggregating both. Reference run:

```
[3/6] B synced: +1 cards from A
[5/6] summary: nodes=2 (healthy=2) cards=2 skills=1
      catalog cards: ['asset-a', 'asset-b']   # asset-a visible via B's sync
[6/6] node A stopped
      summary: nodes=2 healthy=1 unhealthy=1
      asset-a still cataloged, healthy=False
```

## Resilience

A registry that is unreachable is recorded in `errors` (and
`summary.registries_error`) — the remaining registries are still served.

## Relationship to the roadmap

- The health data comes from the registry's `/nodes` view
  (`--health-probe`, see `docs/FEDERATION.md`) and — with `--live-probe` —
  direct node `/health` probes.
- Future: GGIHS dashboards, alerting, and aggregated contract search across
  all registries.
