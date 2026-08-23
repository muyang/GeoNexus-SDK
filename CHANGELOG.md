# Changelog

All notable changes to GeoNexus (the reference stack) are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/) and the
project uses Semantic Versioning.

## [Unreleased]

### Added

- **`geonexus.web` (v1.1)** — Web backend-for-frontend layer:
  - JWT auth (`JWTConfig` HS256/RS256, stateless, distributed-friendly;
    `BearerAuth` / `APIKeyAuth` FastAPI dependencies).
  - Async `TaskManager` (background thread pool, `queued → running →
    done | failed | cancelled`, cooperative cancel, progress updates,
    pluggable persistence callback).
  - REST router (`create_web_router` / `create_web_app`): `/api/auth/login`,
    `/api/health`, `/api/cards`, `/api/skills`, `/api/nodes`,
    `/api/execute` (202 async), `/api/goals` (202 async), `/api/tasks`,
    `/api/tasks/{id}`, `/api/tasks/{id}/stream` (SSE),
    `/api/tasks/{id}/cancel`.
  - Node-level auth: `GeoMCPServer(api_keys=...)` gates `geo.execute` with
    `X-API-Key` (read methods stay open); `GeoMCPClient(api_key=...)` and
    `FederatedGeoMCPClient(api_key=...)` forward the header;
    `GeoMCPServer(forward_api_key=...)` for node-to-node delegation
    credentials (per-node key model).
  - CLI `geonexus web start` (`--registry`, `--node`, `--node-api-key`,
    `--cors-origin`, `--jwt-secret`).
  - `docs/WEB.md` (auth model, endpoints, deployment topologies, security).
  - Dependencies: `PyJWT>=2.8`, `cryptography>=42.0`.

- **Reflective GeoAgent (v1.1)** — LLM-assisted repair on the deterministic
  executor:
  - `plan_from_text_with_registry()` — registry-grounded goal translation
    (LLM can only pick skills that actually exist at the registry).
  - `PlanReflector` — LLM diagnosis of a failed plan step (multi-turn
    context: goal + error + completed-step summaries) producing a repair
    action: `retry` / `replace` / `skip` / `abort`.
  - `ReflectiveExecutor` — runs plans like `PlanExecutor`, reflects on
    failures and retries per advice, bounded by `max_reflections`; each
    step records its `reflections` history.
  - `evaluate_plan()` — LLM self-assessment of a finished plan
    (`satisfied` / `score` / `notes`).
  - `PlanStep.reflections` field + `skipped` status; `docs/AGENT.md`
    updated with the reflective-execution guide.

- **Web × Agent confluence (v1.1)** — `POST /api/goals` upgraded to the full
  reflective stack: registry-grounded planning → `ReflectiveExecutor`
  (LLM repairs failed steps) → `evaluate_plan` self-assessment. The task
  result carries `reflective` and `evaluation` fields; `"reflective": false`
  opts back into plain deterministic execution. `docs/WEB.md` documents the
  flow.

- **MCP client bridge (v1.1)** — `geonexus.mcp_client` imports tools from
  external MCP servers (stdio or Streamable HTTP) as GeoSkills:
  `MCPToolClient.stdio(...)` / `.http(...)` / `.list_tools()` /
  `.to_skills()` / `.register_into()`; `MCPTool.to_skill()` wraps a tool
  with a forwarding handler; `Skill.mcp_source` origin metadata;
  `GeoNode.register_skill_objects()` batch registration; CLI
  `geonexus mcp import`. Together with the server-side adapter this makes
  GeoNexus both an MCP host and client (verified end-to-end against our own
  `mcp run` server). `docs/MCP.md` client-side guide.

- **OGC coverage expansion (v1.1)** — three more OGC families as adapters:
  - **OGC API - Records** (`ogc_records.py`): catalogue discovery —
    `list_ogc_records` / `fetch_ogc_record` / `ogc_record_to_geocard`
    (themes → capabilities, keywords → tags, data links → access).
  - **OGC API - Tiles / Maps / Styles** (`ogc_tiles.py`): visualization
    plane — `list_ogc_tilesets` / `ogc_tileset_to_geocard` (`tiles`
    capability, `{z}/{y}/{x}` endpoint), `list_ogc_styles` /
    `ogc_style_to_geocard` (`styling` capability),
    `ogc_visualization_to_geocards`.
  - **WMS / WMTS** (`ogc_wms.py`): legacy services — GetCapabilities
    parsing (xmltodict or dependency-free light parser),
    `list_wms_layers` / `wms_layer_to_geocard` (GetMap),
    `list_wmts_layers` / `wmts_layer_to_geocard` (GetTile).
  - `docs/OGC.md` V1.1 section; 18 new tests (records 11 + tiles/styles/WMS
    +7).
  - CLI: `geonexus card import-ogc-records [--record --collection --bbox
    --query --limit]`, `import-ogc-tiles [--tileset --style]`,
    `import-ogc-legacy [--service wms|wmts]`; 3 CLI tests.

## [1.0.0] - 2026-08-21

### Added

- **GeoCard 1.0** — schema `geocard_version` now accepts both `0.1` and
  `1.0` (legacy cards stay valid); the SDK emits `1.0` by default.
- **GeoMCP 1.0** — protocol version advertised as `1.0.0`; method set and
  error code registry frozen for the 1.x line.
- **Conformance suites** (`tests/conformance/`): GeoCard validation vectors
  (6 valid / 14 invalid cases) and GeoMCP JSON-RPC vectors (11 request
  cases + 4 invalid envelopes), run independently of the SDK.
- **STAC write side** — `geocard_to_stac_item` / `geocard_to_stac_catalog` /
  `save_stac_item`, CLI `geonexus card export-stac`, `geonexus:…` extension
  fields, `demo stac-write` (`docs/STAC.md`).
- **GGIHS dashboard** — `GET /` single-file HTML dashboard on the GGIHS
  service (`/summary`, `/nodes`, `/catalog` aggregation).
- **Registry federation** — pull-based peer sync (`RegistryFederator`,
  `registry start --peer`, `registry sync`, `POST /sync`).
- **GeoNode-to-GeoNode federation** — node-level delegation with
  `__geonode_delegate` loop guard, health-aware routing (`GET /nodes`,
  `--health-probe`), `demo federation-deep`.
- **Governance docs** — `docs/API_STABILITY.md` (frozen public API +
  deprecation policy), `docs/RELEASE.md` (release process), this changelog.

### Changed

- Package, GeoCard schema and GeoMCP protocol versioned to 1.0.0.
- `docs/GEOCARD.md` and `docs/GEOMCP.md` promoted to formal specifications
  (versioning, extension policy, per-method schemas, error code registry,
  transport bindings, conformance statements).

### Fixed

- IOField `required` no longer emitted when `False` (schema-valid output for
  `outputs` sections).
- Registry `extent.spatial.crs` handling for string-vs-list CRS (pygeoapi
  interop); job results discovery via qualified OGC `rel` URIs.

### Released

- **v1.0.0 published to PyPI as `geonexus-sdk`** (sdist + wheel; the bare
  `geonexus` name is occupied by an unrelated package, the import package
  stays `geonexus`). Rehearsed on Test PyPI (`geonexus==1.0.0`) and verified
  from a clean venv: `pip install geonexus-sdk` → `import geonexus` →
  `geonexus version` reports 1.0.0. See `docs/RELEASE.md`.

## [0.1.0] - 2026-08-19

### Added

- GeoCard schema 0.1 + Python SDK (model/builder/loader/validator,
  ContractValidator).
- GeoMCP 0.1.0 — JSON-RPC 2.0 protocol, FastAPI server, httpx client.
- Local GeoNode (GeoCard registry, Skill registry, local runtime),
  GeoSkill abstraction, `geonexus` CLI.
- Amazon NDVI demo (synthetic data), real Sentinel-2 STAC demo,
  federated/pipeline/agent/OGC demos.
- Shared GeoCard Registry with persistence + API-key auth; GeoAgent planner
  (DAG pipelines, LLM goal translation); official MCP SDK bridge
  (stdio + Streamable HTTP); STAC/OGC API adapters (read).
- CI (pytest + ruff + mypy + build on 3.11/3.12), wheel packaging with the
  GeoCard schema shipped as package data.
