# GeoNexus Reference Stack MVP

## Release 1.1.2

This release adds opt-in governed node announcements, explainable node/resource
readiness ratings, independent evidence, manual admission and the offline
remediation toolkit 0.1. The toolkit diagnoses saved snapshots, drafts binding
changes from caller-supplied samples, validates samples and exports agent tasks;
it does not modify live nodes or grant approval. See the
[release notes](docs/RELEASE_1.1.2.md),
[admission design](docs/node-admission-rating.md), and
[remediation guide](docs/node-remediation-toolkit.md).

Download the wheel, source package, remediation toolkit ZIP and visual report
from the [v1.1.2 GitHub release](https://github.com/muyang/GeoNexus-SDK/releases/tag/v1.1.2).

Historical reports retain their original evaluation dates, policy limitations
and unknown evidence. Publishing this release does not deploy Platform/RuoYi
integration or publish the package to PyPI.

## Previous development snapshot: 1.1.1.dev20261015

This development snapshot includes `geonexus.geocard.contracts`, the official
MCP adapter's `http_guarded` integration option, and `geonexus.registration`
(`georegister`). It is the SDK used by the reviewed Platform / GeoHarness
runtime, not a new production-stability claim. See
[the release notes](docs/RELEASE_1.1.1.dev20261015.md),
[registration and dependency boundaries](docs/ONBOARDING.md), and
[the v0.2 contracts](docs/V02_CONTRACTS.md).

Install this checkout with `pip install .` for the lightweight core, or
`pip install '.[all]'` for the full reference runtime. Development tests use
`pip install '.[dev,all]'`. Publishing source to GitHub does not publish a
package to PyPI.

GeoNexus v0.2 additive contracts and official MCP compatibility: [docs/V02_CONTRACTS.md](docs/V02_CONTRACTS.md).


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
client. The optional official MCP adapter exposes these capabilities as MCP
tools; the native GeoMCP JSON-RPC API and the MCP transport remain distinct.

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
cd /Users/mac/Repos/GeoNexus/core
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

# GeoTask ops: inspect tasks and events, reconcile after a restart
# (submit/cancel belong to the process that owns the task — see docs/GEOTASK.md)
.venv/bin/geonexus task list      --state geotask.json
.venv/bin/geonexus task events    <task_id> --since 3 --state geotask.json
.venv/bin/geonexus task reconcile --state geotask.json

# Invoke a model GeoCard (compute gate first: no compatible node -> refused with reasons)
#   over the protocol:  geo.invoke   (see docs/MODEL.md)
#   from Python:        geonexus.model.invoke_model(card, inputs, capabilities=caps)

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

# Run the tests
.venv/bin/pytest
```

See `docs/QUICKSTART.md` for the full 10-minute walkthrough, including
starting a live node and calling it with `curl`.

## Repository layout

```
core/
├── schemas/geocard.schema.json   # Official GeoCard JSON Schema (draft 2020-12)
├── src/geonexus/
│   ├── geocard/                  # model, builder, loader, validator (contract)
│   ├── geomcp/                   # protocol, models, server, client
│   ├── geonode/                  # node, registry, runtime, skill
│   ├── registry/                 # shared Registry: cards+skills, persistence+auth
│   ├── federation/               # pushdown execution (V0.3)
│   ├── agent/                    # GeoAgent: planner + pipeline DAG (ready-set
│   │                             #   parallel execution) + LLM translator
│   ├── adapters/                 # STAC + OGC API read/write adapters (V0.4/V1.0)
│   ├── kg/                       # generic knowledge-graph primitives + storage
│   ├── geotask/                  # unified task model: state machine, DAG
│   │                             #   ready-set parallelism, seq'd events, replay,
│   │                             #   capacity/priority/fairness scheduling
│   ├── model/                    # model invocation: compute gate → provider
│   │                             #   (HTTP inference / batch submit-poll-collect)
│   ├── cafe/                     # CAFE: the only pushdown engine
│   │                             #   central (routing/leases) + local/remote workers
│   ├── security/                 # zero-trust gateway + legal ontology
│   ├── gaag/                     # GAAG contract registry + contract types
│   ├── mcp_adapter.py            # official MCP SDK bridge: stdio + HTTP (V0.5/V1.0)
│   └── cli.py                    # `geonexus` command line interface
├── examples/
│   ├── local_node.py             # minimal embedded node example
│   ├── amazon_ndvi/              # end-to-end NDVI demo (synthetic data)
│   ├── federated/                # registry + data node + pushdown demo
│   ├── federation_deep/          # node delegation + health-aware routing (V1.0+)
│   ├── ggihs/                    # cross-registry health/catalog aggregation (V1.0+)
│   ├── stac_real/                # real Sentinel-2 NDVI demo (V0.4)
│   ├── stac_write/               # GeoCard -> STAC Item round trip (V1.0+)
│   ├── agent/                    # pipeline DAG demo (V1.0)
│   ├── ogc_process/              # OGC write-side bridge demo (V1.0)
│   ├── ogc_coverage/             # OGC Coverages raster demo (V1.0)
│   └── ogc_pipeline/             # OGC skills in GeoAgent pipelines (V1.0)
├── tests/                        # pytest suite
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
- `docs/RELEASE.md` — release process checklist.
- `docs/QUICKSTART.md` — run the entire demo in less than 10 minutes.
- `docs/MVP_IMPLEMENTATION.md` — what was built, acceptance checks, limitations.
- `docs/ROADMAP.md` — 下一步路线图：GeoTask 统一任务模型、CAFE 唯一下推化、
  模型可调用闭环与算力调度（含现状实测评估与分阶段验收判据）。
- `docs/CAFE.md` — 唯一的下推引擎：两种 worker 的分工、三阶段路由、租约与故障转移。
- `docs/MODEL.md` — **模型调用契约**：算力门禁前置、两种 provider 的线上约定、
  `geo.invoke` 的加法式扩展、`ModelBackend` 如何接进任务契约。
- `docs/MIGRATION.md` — 迁移指南：老 `TaskManager` 用户、`federation` 用户。
- `docs/GEOTASK.md` — **任务契约**（已冻结、核心已实现）：数据模型、状态机、
  事件契约、失败语义与错误码、持久化与重启对账、执行后端协议、迁移映射；
  §10 为实现进度。契约由 `tests/test_geotask_contract.py` 机器校验。
- `CHANGELOG.md` — release history.

文档与实现的一致性由 `scripts/check_docs.py` 机器校验（CI 里跑）：`API_STABILITY.md`
表里的公开名必须能被 `getattr` 到、全文 `geonexus.*` 点号名可解析、`geo.<method>`
必须在 `METHODS` 里注册、相对链接与仓库内路径引用真实存在。

## License

Apache License 2.0. See `LICENSE`.

### Governed node federation (incremental, opt-in)

An offline [remediation toolkit](docs/node-remediation-toolkit.md) converts saved discovery/assessment evidence into repair tasks, sample-driven binding drafts and sample checks. Run `python -m geonexus.remediation --help`; it does not modify live nodes, approve resources or claim scientific validity.

`geonexus.admission` adds versioned node/resource announcements, explainable readiness ratings, independent evidence, manual node/resource review and digest-bound orchestration eligibility. Third-party and official providers use the same gates; registration and rating never grant execution rights. Existing GeoCard/GeoMCP/MCP paths remain compatible. See [节点准入与评级](docs/node-admission-rating.md) and run `python examples/governed_node_demo.py --output /tmp/geonexus-admission-demo`. Platform/RuoYi integration is an explicit host task; the SDK reference service is not a deployed platform backend.

Read the [real-node assessment and 17-page visual report](docs/reports/2026-11-16-enso-node-rating/README.md) for measured results, resource-level blockers, and current readiness-policy limitations.
