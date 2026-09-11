# GeoNode

> GeoNode is a **sovereign cloud-native geospatial capability node**.

For the MVP, the **Local GeoNode** runs on your machine. It is the execution
boundary of GeoNexus: it owns assets (via GeoCards), owns capabilities (via
GeoSkills), exposes them through GeoMCP, and executes work *on the node that
holds the data*.

## Local GeoNode

```python
from geonexus.geonode import GeoNode

node = GeoNode(name="local-node", host="127.0.0.1", port=8787)

node.register_geocard(card)
node.register_skill(name="ndvi-analysis", handler=ndvi_function)

node.run()  # blocking; serves POST /geomcp, GET /health, GET /capabilities
```

Components bundled in one process:

| Component          | Responsibility                                        |
|--------------------|-------------------------------------------------------|
| `GeoCardRegistry`  | Discovery layer — register / get / list / search cards |
| `SkillRegistry`    | Capability layer — register / get / list skills        |
| `LocalRuntime`     | Execution boundary — resolve skill, validate inputs, call handler |
| `GeoMCPServer`     | Interaction protocol — JSON-RPC 2.0 + FastAPI transport |

Start it from the CLI (with the bundled demo assets):

```bash
geonexus node start            # 127.0.0.1:8787
curl http://127.0.0.1:8787/health
```

## Node assets

A node can host cards of any GeoCard type:

| Type       | Meaning                                                      |
|------------|--------------------------------------------------------------|
| `data`     | Datasets stored on (or reachable from) this node.            |
| `model`    | Trained models runnable on this node.                        |
| `skill`    | Reusable capabilities (this MVP implements these).           |
| `agent`    | Orchestration agents (future).                               |
| `workflow` | Composed sequences of capabilities (future).                 |
| `knowledge`| Curated region/domain knowledge.                             |
| `compute`  | Compute services the node offers.                            |

The MVP executes skills; the other asset kinds are represented by their
cards so the registry and protocol are already type-complete.

## GeoSkill

A skill bundles:

- `name`, `description`
- `input_schema`, `output_schema` (JSON-schema-like)
- `handler(params, context) -> dict`
- optional `geocard` describing the skill itself

```python
from geonexus.geonode import Skill

skill = Skill(
    name="ndvi-analysis",
    description="Compute NDVI from red/NIR rasters",
    input_schema={"required": ["red", "nir"]},
    output_schema={},
    handler=ndvi_analysis_handler,
)
```

The runtime hands the handler a `SkillContext` with the request id, spatial /
temporal context, the resolved GeoCards, and a workdir — so skills never need
global state and always know what they are operating on.

## Contract gating

`geo.execute` requests that reference GeoCards are checked against the
cards' contracts before any computation. Requests outside the contract
(e.g. a bbox that does not intersect the card's extent) are refused with
GeoMCP error `2000 CONTRACT_NOT_SATISFIED`.

## Future: GeoNode Federation

The MVP keeps GeoMCP and GeoCard transport/contract agnostic so federation
can be added without rewriting either:

- **V0.3** — two GeoNodes, federated execution, a pushdown protocol
  (requests are routed to the node that owns the data; computation moves to
  the data).
- **V1.0** — GeoNode Federation and a GeoNexus Registry: nodes advertise
  their cards to a registry; clients discover the owning node, then execute
  directly against it.

Nothing in the current GeoCard registry, GeoMCP protocol or skill runtime
assumes a single node, which is exactly the property federation needs.
