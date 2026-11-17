# Changelog

All notable changes to GeoNexus (the reference stack) are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/) and the
project uses Semantic Versioning.

## [1.1.2] - 2026-11-17

### Added

- Opt-in `geonexus.admission`: versioned node/resource announcements, capacity
  declarations, deterministic readiness ratings, digest-bound independent
  evidence, manual review, revocation and task eligibility. Official and
  third-party nodes use the same gates.
- `GeoNode.declaration()` / `announce()`, JSON Schemas, optional HTTP governance
  adapter, `geonode-admission` CLI and governed federation demo.
- Offline remediation toolkit 0.1 (`geonode-repair`): snapshot diagnosis,
  sample-driven binding drafts, sample validation and agent task export.
- Archived ENSO/NINO3.4 assessment, 36-resource inventory and 17-page visual PDF.

### Boundaries

- Ratings and sample checks do not grant authorization or scientific validation.
- The toolkit does not execute remote jobs, install code, modify nodes or approve
  resources. Platform/RuoYi production integration remains a host task.
- The readiness-v1 limitations concerning placeholder licenses, snapshot
  versions and unbound catalog items are documented, not silently hidden.
- No PyPI publication is performed as part of the GitHub release.

## [Unreleased]

### Added

- **Case template (v1.0, since revised to v1.1 — see Changed) + a runnable worked example** — how a research article
  becomes a platform-usable case, and one end-to-end instance.
  - `docs/case-template.md`: the field set (identity / spatio-temporal extent /
    composition / parameter contract / deliverables / dataset slots), the
    seven-step conversion workflow, and an acceptance checklist. The two rules
    that matter most: **definitions are `fixed` parameters, situations are
    `reusable`** (turning a definition into a knob lets someone compute a
    different metric under the same name), and **synthetic data must be
    labelled inside the file**, not only in prose.
  - `templates/case-template/`: a fillable skeleton (`case.card.yaml`,
    `recipe.yaml`, `datasets.md`); placeholders must disappear before a case is
    real.
  - `examples/flightway_wetland/`: the worked example — a migratory-bird /
    wetland study (EAAF priority wetlands, *Scientific Reports* 2026,
    DOI 10.1038/s41598-025-31727-2) turned into a case that really runs.
    (It was first built on synthetic inputs; it has since been rebuilt on the
    paper's own published site-level data and method — see **Changed**.)
  - The example doubles as a demo of both batch-2 rules: the 1% rule is a
    `scope: fixed` parameter (it is the metric's definition), and the case's own
    sensitivity is `public` while it cites a `restricted` precise-site dataset —
    so the composite is `restricted` for anyone who can resolve it and
    **invisible** for anyone who cannot.
  - `docs/cases/flightway-wetland.md`: the fact ledger separating what the
    article states (with page references) from what this case inferred and what
    is synthetic — plus the gap list (site boundaries vs points, flyway network
    vs single AOI, human/expert steps having no node type, run id not yet
    injected by the platform).
  - `tests/test_flightway_case.py` checks the contract and runs the real chain.
    (16 of its original assertions pinned the synthetic chain; the file has
    since grown to 18 tests covering the rebuild — see **Changed**.)

- **GeoCard 1.1 sections: `composition` / `params` / `recipe_ref`** — a card can
  now describe a *whole solution* without containing one.
  - `composition.components[]` (`{id, role, step?, required?, alias?}`) lists the
    assets a composite case is made of, as **references only**: reusing a case
    moves no data, and each component keeps its own identity, access and licence.
    Duplicate `(id, role)` pairs are rejected — a duplicate reference would run a
    step twice.
  - `params.params[]` is the **parameter contract** (`ParamSpec`: name, type,
    required, default, enum, minimum/maximum, scope, target). Without it a forked
    case is inert: nobody knows which fields are safe to vary. `scope: fixed`
    publishes a *result* rather than a *method*.
  - `recipe_ref` points at the executable recipe (`recipe://ns/name@semver`),
    carrying the `data_snapshot` the solution was authored against.
  - Additive within 1.x: cards without the new sections stay valid, and the 0.1
    schema is still accepted. Conformance vectors grow with the sections
    (`tests/conformance/geocard_vectors.py`), and cross-field rules that JSON
    Schema cannot express (duplicate params, `minimum > maximum`, blank enum
    entries) are covered in `tests/test_geocard.py`.
  - `docs/GEOCARD.md` documents all three sections.

- **Report contracts (v1.1, `geonexus.gaag`)**: a conclusion becomes a citable
  asset. `report_card_from_run(plan, ...)` builds a `type: knowledge` card that
  carries `composition` / `params` / `recipe_ref` and provenance pointing at the
  run; `scan_report` captures the artefact's identity (format, sha256, title,
  sections) — **never a quality score**; `report_contract` + `validate_report`
  require the source four (run / recipe / params / data snapshot);
  `register_report_contract` refuses to write a report whose source is
  incomplete, because otherwise everyone downstream would cite it as verified.
  A `report` contract type joins `CONTRACT_SCHEMAS` (8 types), and
  `detect_asset_type` now answers `report` for `.html` *before* the generic
  `text` rule.

- **Visibility inheritance (v1.1, `geonexus.geocard.visibility`)**: a composite
  card is not a way around access control.
  - `effective_visibility(card, lookup)` = min over the card's own level and its
    **content-bearing** components (`GATING_ROLES`: data / model / knowledge /
    workflow). Skills and compute do not gate: they are code and resources, leak
    no content, and often exist only as a name — gating on them would make every
    case invisible.
  - Anything unresolvable ⇒ the whole composite is invisible, and callers answer
    **404, never 403**: 403 would admit that something exists which you cannot
    see. Unknown values degrade to `restricted`, never to `public`.
  - `required: false` components do not participate (the author already declared
    the case runs without them), and `gating_only=False` gives the stricter
    audit view.
  - The module decides *visibility*, not *authority* — who may see what stays
    with the platform's identity layer.

- **`geonexus.recipe` (v1.1)** — the executable half of a knowledge product:
  a **Recipe** says *how to run*, a GeoCard says *what it is*, and a `TaskGraph`
  is *one run*. No layer is a representation of another.
  - `Recipe` / `RecipeStep` / `RecipeOutput` with an address that carries
    identity (`recipe://<ns>/<name>@<semver>`), full DAG validation (unique ids,
    known dependencies, acyclicity, entrypoint, deliverables that point at real
    outputs, `step://` inputs that resolve).
  - `materialize(recipe, params, run_id=...)` → `RunPlan`: parameter contract
    first (`field`/`code` on every failure), then `${params.x}` substitution, then
    `task_id = "<run_id>:<step_id>"` so a replay is idempotent and two runs never
    collide. `provenance` on every task records recipe version, run id, resolved
    params and data snapshot.
  - `deliverables(plan)` returns the run's conclusions with roles — deliberately
    **no URLs**: where an artefact lives is the run environment's decision.
  - `RecipeRegistry`: file-backed catalogue reusing the card registry's review
    vocabulary, with `resolve()` picking the newest **approved** version
    (`version_key` avoids the `1.10.0 < 1.9.0` trap) and `fork()` as the
    executable definition of reuse — new address, your parameters, source
    untouched, fork returns as `pending`.
  - `geo.plan` (GeoMCP, **additive**, **read-only**): materialises a recipe over
    the wire without executing it. `geo.capabilities.recipe_planning` declares
    availability; `geo.describe` lists recipe summaries; errors `2005
    RECIPE_NOT_FOUND` / `2006 RECIPE_PARAM_INVALID` (with machine-readable
    `field`/`code`). Also exported through the MCP bridge as `geo_plan`.
  - **Catalogue with review**: the shared registry now serves recipes too
    (`POST/GET/DELETE /recipes`, `/recipes/{address}/approve|reject`,
    `/recipes/fork`) with the same `pending → approved/rejected` vocabulary as
    cards. Recipes persist in their own file (`<persist>.recipes.json`) so a bad
    file cannot take the card catalogue with it. Approving an unversioned
    address is accepted only when it matches exactly one version; with several
    it is `409` rather than guessing — approval is a governance act.
  - `geonexus.review` holds that vocabulary for both layers (a shared module,
    not one borrowing the other's words).
  - **A catalogue is shared, so a node's copy may be stale.** `geo.plan`
    resolution reloads the local catalogue file when another process changed it
    (`RecipeRegistry.reload_if_changed`) and, on a miss, asks the registry the
    node is bound to. Only **approved** versions are ever resolved: an earlier
    fallback chain could hand back a `pending` recipe, which made the approval
    gate decorative — the regression is pinned by a test.
  - CLI: `recipe validate|inspect|plan|register|search|fork` (`plan` exits `2` on
    a parameter-contract violation). Catalogue commands take **either**
    `--catalogue PATH` (offline/single machine) **or** `--url URL` (a running
    registry) — writing a file and talking to a service are different things, and
    passing both or neither is an error rather than a guess. `registry start
    --recipes` and `node start --recipes` bind a catalogue (the node flag is what
    enables `geo.plan`).
  - `scripts/acceptance_recipe_e2e.py`: the batch-1 acceptance chain driven
    against **real** processes — Python registry + GeoNode (`geo.plan`) + SDK Web
    BFF + the Node platform BFF: fork → change params → approve → run →
    download the report. 36 checks, non-zero exit on the first unmet expectation.
  - `docs/RECIPE.md`, `examples/sdg_15_3_1/recipe.yaml`, `tests/test_recipe.py`,
    `tests/test_geomcp_plan.py`, `tests/test_registry_recipes.py`.

- **`geonexus.model` (v1.2, Phase 3)** — model invocation contract: a
  `type: model` GeoCard finally has an execution path.
  - `invoke_model(card, inputs, ...)` runs **compute gate → provider →
    I/O contract check**; `match_resource` is evaluated *before* the call, so
    an incompatible model is refused **with reasons** and nothing is sent.
  - `InferenceProvider` (synchronous HTTP) and `BatchProvider`
    (submit → poll → collect, with `submit`/`poll`/`collect` usable
    separately for long jobs).
  - Providers are selected from `card.access.protocol`; an unknown protocol
    (e.g. `grpc`) is an explicit error rather than a silent fallback.
  - `ModelOutcome` carries `error_code` / `retryable` / `reasons` /
    `compute_node`; failures are returned, not raised.
  - `ModelBackend` exposes model invocation as a `geotask`
    `ExecutionBackend`, so model tasks share the state machine, leases,
    retries and observability of every other task.
  - GeoMCP gains one **additive** method, `geo.invoke`; it is advertised in
    `geo.capabilities.model_invocation` and is a write method for
    `X-API-Key` purposes. Nodes that do not implement it answer with the
    standard `METHOD_NOT_FOUND` (-32601).
  - `docs/MODEL.md`, `tests/test_model.py` (52 tests).

- **`geonexus.geotask.scheduler` (v1.2, Phase 4)** — capacity accounting and
  admission control for task execution.
  - `Scheduler` / `Reservation` / `Occupancy` / `AdmissionResult`: admission
    by `cpu_cores` / `memory_mb` / `gpu_count` (+`gpu_kind`) reservations.
  - Three deliberately separate verdicts: **does not fit at all** →
    `no_compatible_compute` rejection with reasons; **no capacity right now**
    → `blocked` (re-enters the ready set once capacity is released);
    **fits and free** → reserve and run. Treating "does not fit" as queueing
    would queue forever, disguising a deployment error as resource pressure.
  - Priority ordering plus **per-owner rotation** so one submitter cannot
    starve another; backfill so a small task can use a large task's leftover
    capacity.
  - Retry backoff (`RetryPolicy.backoff_for`) defers re-admission and yields
    capacity to other tasks meanwhile; failure isolation at three boundaries
    (`submit`, `poll`, handler) so one bad task cannot take down a run.
  - `TaskGraph` learns the `blocked → queued` return path (the state machine
    edge always existed); `mark_blocked()` records the reason in the
    `state_changed` event and `task.message`.
  - Observability: `Scheduler.stats()`, `TaskRunner.scheduling()`,
    `RunReport.blocked` / `rejected` / `occupancy`.
  - `tests/test_geotask_scheduler.py` (49 tests), including an independent
    probe that measures real concurrency inside the handler and asserts the
    scheduler's accounting matches it exactly.

- **Docs consistency gate (v1.2, Phase 5)** — `scripts/check_docs.py` (run in
  CI) verifies that every public name listed in `docs/API_STABILITY.md` is
  really exported, that every `geonexus.*` dotted name in the docs resolves,
  that every `geo.<method>` is registered in `METHODS`, and that every doc
  link and in-repo path reference exists. `tests/test_docs_consistency.py`
  proves the checker can fail (one fake of each kind).
  New: `docs/MIGRATION.md` (old `TaskManager` / `federation` users).

### Added

- **GeoMCP V2: `federated_sources` cross-registry resolution** (plan W5, final
  slice). References are `<registry_url>#<card_id>`; resolution is one-hop,
  deduplicated, approved-only. Resolved cards join contract gating, their
  provenance (source/card/owner/registry) lands in `result.federated` — also on
  delegated results — and their owners feed the pushdown owner table, so
  `push` can cross registry domains. Every resolution failure is **2009
  FEDERATION_UNRESOLVED** (malformed / unreachable / unknown / unapproved /
  bad schema); silent ignoring is forbidden. Contract gating now also runs
  when a request carries *only* federated cards (the gate used to be keyed on
  `geocards` alone — a real hole this slice closed). 7 tests incl. two-registry
  fixtures and a federation×pushdown delegation case; conformance +1 vector.

### Fixed

- **`market install` used a non-wheel temp filename** — pip validates wheel
  filename structure (`name-ver-pytag-abi-plat.whl`), so downloads now keep the
  URL's basename (sanitised) inside a temp dir.
- **Managed installs (`--target`) pass `--no-deps`** — the managed directory
  holds only the plugin body; dependencies (like `geonexus-sdk`) come from the
  node environment, otherwise a second conflicting SDK copy lands in the target.

### Added

- **GeoMCP V2: pushdown engine in `geo.execute`** (plan W5, slice 2).
  `pushdown_spec.strategy` ∈ local|migrate(alias)|push|auto. `auto` compares
  `data_bytes/bandwidth_bps` (pull data here) against `remote_queue_s` (run at
  the data owner) and picks the cheaper side; missing estimates fall back to
  `local` with a `missing-estimates` reason — the server never invents numbers
  to make a decision. `push` resolves the owner through the bound registry and
  reuses one-hop delegation; unresolvable owners raise the new **2008
  PUSHDOWN_UNRESOLVED** (auto never does — it degrades with a reason). The
  decision, both estimates and reasons land in the result under `pushdown`,
  because "why did this run remotely" must always have an answer. Spec §1a and
  conformance vector `execute-pushdown-bad-strategy` (2004) pin the semantics.
  9 tests incl. a real two-node delegation case.

### Added

- **Public product market, registry side** (plan W2): the registry now holds
  `plugin` entries — `(name, version)` identity like recipes, `install_ref`
  (wheel URL or pip requirement), `sha256`, `plane`, and the same review
  statuses as cards/recipes. Publishing always lands **pending**; HTTP routes
  mirror the cards convention (no status = approved-only, `all` = everything).
  `geonexus market publish/search/install/uninstall`: URL refs **require**
  sha256 at publish (422 otherwise) and install verifies the downloaded bytes
  **before** pip ever runs — market poisoning gets a closed door, not a warn.
  `RegistryServer.start_in_thread` added (the R1 gap, closed). 13 tests.

### Added

- **Opt-in CORS for browser clients** — `GEONODE_CORS_ORIGINS` (comma list).
  Off by default: an unauthenticated node silently opening `geo.execute` to
  every browser on the internet is an incident, not a convenience. The
  standalone client (`client/`) uses a same-origin dev proxy instead.
- **`client/` — the lightweight GeoNexus client** (plan W3, M1 skeleton).
  Vite+Vue3 SPA: connect to a node, browse skills from `geo.capabilities`,
  parameter forms generated from `input_schema`, `geo.execute`, inline GeoJSON
  results plotted on the map, path artifacts fetched through the node's
  `/artifact` exit. Map engines are **renderer plugins** (built-in MapLibre 2D
  + Cesium 3D via dynamic import); browser e2e (headless Chrome, real clicks)
  proves connect → execute → plot, screenshot in `client/screenshots/`.
- **demo plugin gained `demo-sample-points`** — returns inline synthetic
  GeoJSON so the execute→map round trip is demonstrable end to end.

### Added

- **GeoMCP V2: `contract_binding` is now enforced in `geo.execute`** (plan W5,
  first slice). A declared GAAG binding must pass the ContractGate double check
  (semantic similarity + contract consistency) before execution; failing to
  verify — no GAAG registry on the node, unknown contract, or a failed gate —
  returns the new error **2007 `CONTRACT_BINDING_REJECTED`** with the gate
  result as data. An authorization claim is never silently ignored.
  `geo.capabilities` advertises `v2.contract_binding` so clients negotiate
  instead of guessing; conformance vector `execute-contract-binding-rejected`
  and `docs/GEOMCP.md` §1a pin the additive semantics (frozen 1.x surface
  untouched). `GeoNode(..., gaag_registry=...)` wires a GAAGRegistry in.

### Added

- **Plugin loading via pip entry-points** (plan W2, first half). Nodes discover
  the `geonexus.plugins` entry-point group at startup; a plugin is either a
  `register(node)` callable or a declarative object with `cards()`/`skills()`.
  Loading is isolated per plugin — a broken third-party package never takes the
  node down, and `/health` now reports `plugins: [{name, version, ok, error?}]`
  so operators can see what did *not* load. Version comes from distribution
  metadata first (py3.9 EntryPoint has no `.dist`, so the plugin's own
  `version` attribute is the fallback). Example plugin package
  `examples/plugin_demo_ndvi` (1 skill + 1 card) proves the packaging contract.

- **`geonexus register` — lightweight third-party asset registration** (plan W1).
  A shared, read-only probe core (`geonexus.register`): OGC API (features /
  coverages / tiles / processes), STAC, WMS capabilities, GeoNode health, and
  file sniffing (GeoTIFF magic + rasterio bounds, CSV lat/lon columns, GeoJSON
  coordinates, safetensors header) — never executing probed weights. Missing
  facts become warnings, not invented defaults (no bbox ⇒ no `spatial`, the
  card stays a sidebar asset). `build_card` maps kind → GeoCard type/protocol,
  `validate_card` runs the real schema, `submit` targets the registry
  (`status=pending` by default) or the platform, and the CLI defaults to
  `--to dry` so nothing publishes unless asked. Wizard (`-i`) asks only the
  human judgements: name, licence, sensitivity, provider.

### Added

- **`GET /artifact` on GeoNodes** — a controlled exit for outputs written on the
  node machine (`workdir` + `GEONODE_ARTIFACT_ROOTS` whitelist, X-API-Key enforced
  when the node has keys). Without it, deliverables produced by a *remote* node
  are unreachable from the platform, and "查看报告" dead-ends in a 404.

### Fixed

- **GeoMCP transport no longer 500s when a skill returns non-JSON values.**
  A handler returning `pathlib.Path` (or a `set`) crashed `json.dumps` inside
  `JSONResponse` — invisible when running in-process, fatal over HTTP; it bit a
  real Windows node running the EAAF case (`WindowsPath is not JSON serializable`).
  `geomcp/server.py` now normalizes responses through `_json_safe` (Path→str,
  set→sorted list, recursive), covered by a regression test; the case skills were
  also fixed to return string paths, since the wire format should not rely on
  the transport's mercy.

### Changed

- **`examples/flightway_wetland` rebuilt on the paper's own data and method**
  (breaking: recipe `1.0.0 → 2.0.0`, deliverables changed). The worked example
  previously demonstrated a synthetic NDWI water-change pipeline with eight
  invented sites — which is **not** what the cited study does. It now
  reproduces Crosby et al. 2026 (*Scientific Reports* 16:1916,
  DOI 10.1038/s41598-025-31727-2) as published:
  - **Data**: `extract_supp_table3.py` parses Supplementary Table 3 into
    `data/rfi/*.csv` — the 147 priority wetlands with their real names, real
    central coordinates, PC1 scores, the species reaching 1%, Ramsar/flyway
    designations and the paper's bold *"overlaps a protected area"* marker.
    Parsing is validated against every published figure (147 sites; per-country
    counts; coastal 91 / inland 56; the paper's own χ² = 8.33 / 32.39 / 72.53).
    `data/rfi/PROVENANCE.md` records the DOI, the CC-BY licence, the source
    PDF checksums and the fields this case infers rather than reads.
  - **Method**: five steps matching the paper's workflow (framework review →
    site data → PC1 selection with the regional calibration of 10 for PRC and
    Mongolia / 1 elsewhere plus the three named expert exceptions →
    characterisation → report). The count-level data behind PC1 is
    licence-restricted and not public, so the case says so instead of
    recomputing it, and keeps a `--counts` import path (fields per
    Supplementary Table 1). The fourth step — expert boundary delineation — is
    honestly left as a human step rather than faked with a skill.
  - **Honest reporting**: two places where the paper's own published material
    disagrees are reported rather than smoothed over — the supplementary bold
    marker covers 117 sites while Table 4 counts 108 protected, and a strict
    application of the stated threshold yields 145 sites against the published
    147 (two PRC sites score below 10 yet appear in the list). Both numbers
    appear in the report, with the deltas listed per site.
  - **Extension**: the synthetic NDWI demo moves to
    `extensions/habitat_change/`, whose README states in its first sentence
    that remote-sensing habitat-change is one of the paper's *future*
    directions, not the paper's method.
  - `export_geojson.py` now emits the 147 real site features (plus per-country
    subsets and a legend) instead of vectorised synthetic rasters.
  - `tests/test_flightway_case.py` (18 tests) pins the dataset against the
    published tables, the two documented discrepancies, the reproduction of the
    paper's χ² values, and the visibility inheritance from the restricted
    counts component.

- `docs/case-template.md` (v1.1) gained a section on **reproducing a published
  study**: data split into public results / restricted raw / our inferences,
  what to do when two published sources disagree, proving the method ran by
  recomputing a published statistic, and keeping the paper's own "future work"
  in `extensions/`.

### Changed

- `TaskGraph.ready()` now also returns `blocked` nodes whose dependencies are
  satisfied, so a task blocked on resources is retried once capacity frees.
  `mark_blocked()` is idempotent and carries a human-readable reason.
- `RunReport` gained `blocked`, `rejected` and `occupancy`; `TaskRunner`
  accepts an optional `scheduler=`. **Without a scheduler the previous
  behaviour is unchanged** (unlimited capacity).
- A `poll()` exception from an `ExecutionBackend` now fails only that task
  (`executor_lost`, retryable) instead of propagating out of `run()`.
- `API_STABILITY.md` documents `geonexus.geotask`, `geonexus.model` and the
  additive nature of `geo.invoke`.


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

- **Resource matching (v1.1)** — `geonexus.resource` coordinates compute
  resources: `ComputeCapability` (what a `type: compute` node provides),
  `match_resource(model_card, capabilities)` (checks `runtime.cpu` /
  `runtime.memory` / `runtime.gpu` requirements against nodes, with
  per-requirement reasons), `resolve_compute_capabilities(registry_url)`
  (discovers `type: compute` cards), `parse_memory` (`"4Gi"` → GiB).
  Enables routing a model to a node that satisfies its runtime — the
  "coordinate data, model and compute" step of the GeoCard workflow.
  `docs/RESOURCE.md` guide; 16 tests.

- **Data registration & review workflow (v1.1)** — upload a file → generate
  a GeoCard → submit for review → approve → discoverable:
  - `geonexus.metadata`: `inspect_raster` (CRS/bounds/resolution/bands from
    any rasterio-readable raster), `raster_stats` (nodata-aware band stats),
    `raster_to_geocard` (auto-builds a GeoCard with spatial/bands/access
    pointing at the file). 8 tests.
  - Registry review state machine: `RegistryEntry.status ∈ {pending,
    approved, rejected}` (default `approved` — existing registrations stay
    compatible); store `set_status/approve/reject` + `list_entries(status)`;
    `search`/`list` return only approved by default (`status="all"` or an
    explicit state for admin/review views); server
    `GET /cards?status=`, `POST /cards/{id}/approve|reject`;
    client `register(status=)` / `approve` / `reject` / `list_cards(status=)`.
    9 tests.

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
