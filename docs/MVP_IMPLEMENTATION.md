# MVP Implementation Notes

Status and details of the GeoNexus Reference Stack MVP.

## What was implemented

| Component   | Files                                                                  | Notes |
|-------------|------------------------------------------------------------------------|-------|
| GeoCard schema | `schemas/geocard.schema.json`                                       | JSON Schema Draft 2020-12, `geocard_version: "0.1"`, required fields + 13 optional sections, strict per-section validation. |
| GeoCard SDK | `src/geonexus/geocard/{model,builder,loader,validator}.py`             | Pydantic v2 model, fluent builder, YAML/JSON load/save, schema validation. |
| ContractValidator | `src/geonexus/geocard/validator.py`                              | CRS (pyproj, string fallback), bbox intersection (with reprojection), temporal overlap, band subset, resolution. Warnings for unverifiable checks; no pseudo-scientific similarity. |
| GeoMCP protocol | `src/geonexus/geomcp/{protocol,models}.py`                        | JSON-RPC 2.0: `geo.capabilities`, `geo.describe`, `geo.execute`, `geo.health`; reserved + application error codes. |
| GeoMCP server | `src/geonexus/geomcp/server.py`                                     | FastAPI transport: `POST /geomcp`, `GET /health`, `GET /capabilities`; tools/resources/cards registration; contract-gated execute. |
| GeoMCP client | `src/geonexus/geomcp/client.py`                                    | httpx client: request ids, timeout, error mapping, response validation. |
| Local GeoNode | `src/geonexus/geonode/{node,registry,runtime}.py`                  | GeoCard registry, Skill registry, local runtime, thread/blocking serving. |
| GeoSkill | `src/geonexus/geonode/skill.py`                                      | name/description/input/output schemas/handler/GeoCard; `SkillContext`. |
| CLI | `src/geonexus/cli.py`                                                | `card validate/inspect`, `node start`, `skill list`, `demo amazon-ndvi`, `version`, `init`. |
| Amazon NDVI demo | `examples/amazon_ndvi/{skill.py,run_demo.py,geocard.yaml,README.md}` | Synthetic red/NIR scenes, NDVI + change GeoTIFFs, stats, full HTTP loop, contract-gate demonstration. |
| Examples | `examples/local_node.py`                                             | Minimal embedded node. |
| Tests | `tests/test_{geocard,validator,geomcp,geonode}.py`                 | 15+ tests covering every acceptance criterion at unit/integration level. |
| Docs | `README.md`, `docs/*.md`                                             | Architecture, GeoCard, GeoMCP, GeoNode, quickstart, this file. |

## Post-MVP progress (V0.2 → V1.0)

| Version | Delivered | Status |
|---------|-----------|--------|
| V0.2 | Shared GeoCard Registry (`src/geonexus/registry/`), GeoMCP Inspector (`geonexus inspector`), ruff/mypy/coverage baseline | ✅ implemented & tested |
| V0.3 | Federation: `GeoNode.advertise`, `FederatedGeoMCPClient` (discovery + pushdown), `geonexus demo federated`, `docs/FEDERATION.md` | ✅ implemented & tested |
| V0.4 | STAC adapter (`src/geonexus/adapters/`), `geonexus card import-stac`, **real Sentinel-2 NDVI** demo (`geonexus demo stac-real`) | ✅ implemented & tested (real pixels on 2026-08-19 T21NWA, NDVI mean 0.342) |
| V0.5 | **GeoSkill shared registry** (skills advertise/discover; `execute_skill` skill-first routing), **GeoAgent planner** (`src/geonexus/agent/`, `agent run`, `demo agent`), **official MCP SDK bridge** (stdio, `geonexus mcp run`) | ✅ implemented & tested |
| V1.0 | **Pipeline goals** (DAG/skill chaining, `${stepN.outputs.key}` templates, topological execution), **LLM-assisted goal translator** (OpenAI-compatible, `geonexus agent ask`), **MCP Streamable HTTP** (`geonexus mcp serve`), **Registry persistence + API-key auth** (`registry start --persist --api-key`) | ✅ implemented & tested |
| V1.0 (standards) | **OGC API adapter** (`src/geonexus/adapters/ogc.py`): Features collections/items and Processes → GeoCards (`card import-ogc`, `card import-ogc-process`), **write-side bridge** (`ogc_exec.py`: GeoMCP `geo.execute` → OGC Processes execution with job polling, `node start --ogc-process URL::ID`, `demo ogc-process`), **Coverages adapter** (`ogc_coverage.py`: metadata → bands GeoCard, CoverageJSON → numpy → GeoTIFF, `card import-ogc-coverage`, `demo ogc-coverage`), **OGC skills in GeoAgent pipelines** (`demo ogc-pipeline`), verified against live pygeoapi endpoints, `docs/OGC.md` | ✅ implemented & tested |
| V1.0+ (federation) | **GeoNode-to-GeoNode federation**: node-level **delegation** (`GeoNode(registry_url=...)` / `node start --registry`; unknown-card requests forwarded to the owning node, `__geonode_delegate` loop guard), **health-aware routing** (registry `GET /nodes` + `--health-probe`, `FederatedGeoMCPClient` skips unhealthy nodes), **Registry federation** (pull sync via peers, `registry start --peer` / `registry sync`, idempotent upsert), **GGIHS** (`src/geonexus/ggihs/`: cross-registry `/summary` `/nodes` `/catalog` aggregation + **web dashboard at `/`**, `geonexus ggihs start`), demos `federation-deep` + `ggihs`, `docs/FEDERATION.md` + `docs/GGIHS.md` | ✅ implemented & tested |
| V1.0+ (standards write) | **STAC write side** (`src/geonexus/adapters/stac_write.py`): GeoCard → STAC Item/Catalog (`geonexus card export-stac`), `geonexus:...` extension fields, export→import round trip, `demo stac-write`, `docs/STAC.md` | ✅ implemented & tested |
| V1.0+ | GGIHS alerting, aggregated cross-registry contract search, per-node identities | ⏳ future |

Engineering quality: **v1.0.0** — `ruff check` clean, `ruff format --check`
clean, `mypy src/geonexus` clean (37 files), coverage **80%** (gate 75%),
**137 tests** (unit + integration + conformance), CI in
`.github/workflows/ci.yml` (pytest + ruff + mypy on 3.10/3.11/3.12, `python
-m build` wheel/sdist verification), wheel install verified — the GeoCard
schema ships as package data so validation works from any directory.
Release engineering: `CHANGELOG.md`, `docs/RELEASE.md`,
`docs/API_STABILITY.md` (frozen public API + deprecation policy),
`docs/API.md`, conformance suites in `tests/conformance/` (GeoCard vectors
+ GeoMCP JSON-RPC vectors), `scripts/publish.sh` (build + optional PyPI
upload).
MCP bridge depends on the official `mcp` SDK (`pip install "mcp>=1.2"`).
LLM planner needs an OpenAI-compatible endpoint (`GEONEXUS_LLM_API_KEY` +
optional `GEONEXUS_LLM_BASE_URL` / `GEONEXUS_LLM_MODEL`).

## Dependencies

Runtime: `pydantic`, `jsonschema`, `PyYAML`, `numpy`, `rasterio`, `shapely`,
`pyproj`, `fastapi`, `uvicorn`, `httpx`. Optional: `mcp>=1.2` (MCP bridge).
Dev: `pytest`, `ruff`, `mypy`, `build`, `coverage`, `types-PyYAML`,
`types-jsonschema`.
`shapely` is declared for future vector work; nothing in the MVP hard-depends
on it (pyproj is used for CRS comparison; rasterio is used for GeoTIFF I/O).

## Acceptance checks (run at implementation time)

| # | Check | Result |
|---|-------|--------|
| A | `cd /Users/mac/Repos/GeoNexus/mvp` | done |
| B | `pip install -e ".[dev]"` | passed (Python 3.12 venv) |
| C | `pytest` | **45 passed** |
| D | `geonexus card validate examples/amazon_ndvi/geocard.yaml` | OK |
| E | `geonexus node start` | starts on 127.0.0.1:8787 |
| F | `curl http://127.0.0.1:8787/health` | `{"status":"ok",...}` |
| G | `curl http://127.0.0.1:8787/capabilities` | protocol surface JSON |
| H | GeoMCP execute works | via demo + curl + client |
| I | `geonexus demo amazon-ndvi` | completes, prints stats |
| J | `ndvi_2015.tif`, `ndvi_2025.tif`, `ndvi_change.tif` generated | yes (tagged `GEONEXUS_SYNTHETIC=TRUE`) |
| K | README + docs complete | yes |
| L | `geonexus demo federated` (registry + pushdown) | completes, routed to data node |
| M | `geonexus demo stac-real` (real Sentinel-2) | real NDVI computed (windowed) |
| N | `ruff check` / `mypy` / `coverage` | clean / clean / 78%+ |
| O | `python -m build` + wheel install | sdist + wheel build; schema works from wheel |
| P | `geonexus demo agent` (pipeline) | 3-step DAG plan, executes, change raster in-plan |
| Q | `geonexus agent run` (CLI goal) | plans + executes a goal against a live registry |
| R | `geonexus mcp run` / `mcp serve` | stdio + Streamable HTTP handshakes verified |
| S | `geonexus agent ask` (LLM goal) | OpenAI-compatible translation (mock-tested; needs API key live) |
| T | `registry start --persist --api-key` | restart survives; writes gated by X-API-Key |
| U | `card import-ogc` / `import-ogc-process` | live pygeoapi collection/feature/process imported & schema-valid |
| V | `demo ogc-process` (write-side bridge) | geo.execute drives a real pygeoapi process (job_status=successful, results echoed) |
| W | `demo ogc-coverage` / `demo ogc-pipeline` | CoverageJSON → NDVI → GeoTIFF; local + remote OGC steps in one pipeline |
| X | `demo federation-deep` | relay node delegates to data owner; health flips after stop, routing refuses |
| Y | `registry sync` / `demo ggihs` | registry B pulls A's catalog; GGIHS aggregates both + health flip |
| Z | `card export-stac` / `demo stac-write` | GeoCard → STAC Item → re-import round trip |

## Known limitations

- **Real-pixel robustness.** Reading real Sentinel-2 COGs over slow/unreliable
  links can truncate tiles; the demo reads a small window at the valid-pixel
  centroid with GDAL retries, and degrades loudly to synthetic execution if
  real pixels are unavailable. It never labels synthetic data as real.
- **Registry auth is API-key level** (single shared key). No per-node
  identities, scopes or rotation — a fuller identity layer is future work.
- **LLM planner needs an endpoint.** `geonexus agent ask` requires
  `GEONEXUS_LLM_API_KEY` (or `--llm-api-key`); schema-validated output with
  one repair retry, deterministic execution regardless.
- **Contract checks are deliberately conservative.** Semantic similarity /
  IoU thresholds are left as extension points, not fake-validated.
- **`access.auth` is descriptive metadata**; the registry's X-API-Key gate is
  the only enforcement so far.
- **rasterio is required for GeoTIFF output.** If unavailable, the demo
  fails loudly with instructions instead of silently faking GeoTIFF files;
  the numpy NDVI core still works without it.

## Extension points (future roadmap)

| Version | Scope |
|---------|-------|
| V1.0+ | GeoNode-to-GeoNode federation (node interop, health-aware routing, delegation), GeoNexus Registry federation, GGIHS services, per-node identities |
| later | OGC API Features/Processes adapter, plan repair/retry, cross-node cost-aware scheduling |

V0.2–V0.4 are implemented; the remaining items are designed for but not yet
implemented.
