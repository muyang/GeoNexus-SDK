# GeoNexus Python SDK — API reference (v1.0)

The public, stable API is listed in `docs/API_STABILITY.md`; this page is a
quick module-by-module reference. Use `pydoc geonexus.<module>` for
signatures; type hints are shipped (`py.typed`).

## `geonexus` — package root

- `__version__` — package version (`1.0.0`).

## `geonexus.geocard` — GeoCard SDK

- `GeoCard(id, type, name, description, ...)` — the asset contract model;
  `to_dict() / to_yaml() / to_json() / save(path) / validate()`,
  `GeoCard.load(path)`, `GeoCard.validate_data(dict)`.
- `GeoCardBuilder(id, type, name, description)` — fluent builder with
  `.spatial() .temporal() .band() .input() .output() .capability() .access()
  .provenance() .license() .compliance() .trust() .runtime() .interface()
  .rendering() .tag() .build()`.
- `ContractValidator().check(card, bbox=…, crs=…, start=…, end=…,
  required_bands=…, required_resolution=…) -> ContractResult`
  (`satisfied / reasons / warnings`).
- `load_geocard(path)`, `load_geocard_dict(path)`, `load_many(paths)`,
  `validate_card_schema(dict) -> SchemaReport`.
- Section models: `SpatialSection`, `TemporalSection`, `Band`, `IOField`,
  `Capability`, `AccessSection`, `ProvenanceSection`, `LicenseField`,
  `ComplianceSection`, `TrustSection`, `RuntimeSection`, `InterfaceSection`,
  `RenderingSection`.
- Errors: `GeoCardError`, `GeoCardValidationError`.

## `geonexus.geomcp` — protocol + server + client

- `GeoMCPServer(name, engine, geocard_registry, skill_registry,
  contract_validator, registry_url, forward_timeout)` —
  `register_tool / register_resource / register_geocard / execute /
  describe / capabilities / health / create_app / run`; `set_registry(url)`
  enables node-level delegation.
- `GeoMCPClient(base_url, timeout)` — `execute / describe / capabilities /
  health` (JSON-RPC, request ids, timeout, error mapping).
- `GeoMCPDispatcher(capabilities_fn, describe_fn, execute_fn, health_fn)`
  — transport-independent dispatch; `dispatch(payload) -> response`.
- Models: `GeoMCPRequest`, `GeoMCPResponse`, `GeoMCPError`, `ExecuteParams`,
  `DescribeParams`, `SpatialContext`, `TemporalContext`.
- Helpers: `build_request / build_response / parse_request / make_request_id`;
  error codes `PARSE_ERROR … EXECUTION_FAILED`; `PROTOCOL_VERSION`.
- Errors: `GeoMCPProtocolError`, `GeoMCPClientError`.

## `geonexus.geonode` — Local GeoNode

- `GeoNode(name, host, port, workdir, registry_url)` —
  `register_geocard(s) / register_skill / register_skill_object /
  advertise(registry_url) / set_registry / execute / capabilities /
  describe / health / create_app / run / start_in_thread`.
- `GeoCardRegistry`, `SkillRegistry`, `LocalRuntime`, `Skill`, `SkillContext`.
- Errors: `GeoNodeError`, `DuplicateEntryError`, `EntryNotFoundError`.

## `geonexus.registry` — shared registry

- `RegistryServer(name, persist_path, api_keys, health_probe, peers)` —
  HTTP service (`/cards`, `/skills`, `/search`, `/nodes`, `/sync`,
  `/health`); `add_peer / sync_peers`.
- `RegistryClient(base_url, api_key)` — `register / unregister / get /
  list_cards / list_skills / search / search_skills / get_nodes /
  register_skill / unregister_skill`.
- `RegistryStore`, `RegistryFederator`.
- Models: `RegistryEntry`, `RegistrySearchResult`, `SkillDescriptor`,
  `SkillEntry`. Errors: `RegistryClientError`.

## `geonexus.federation` — federated execution

- `FederatedGeoMCPClient(registry_url)` — `execute / execute_skill /
  discover / discover_skills / search_skills / search / node_health`.
- `FederatedExecutionError`.

## `geonexus.agent` — GeoAgent

- `Goal` / `GoalStep` — declarative goals (capability-based or pipeline
  DAG with `${stepN.outputs.key}` templates).
- `GeoAgentPlanner(registry_url).plan(goal)`; `PlanExecutor.run(plan)`;
  `run_goal(goal, registry_url)`.
- `LLMConfig`, `LLMGoalPlanner(config).plan(goal_text, …)`,
  `plan_from_text(…)` — OpenAI-compatible goal translation.
- `Plan`, `PlanStep`, `ExecutionError`.

## `geonexus.ggihs` — cross-registry health/catalog

- `GGIHSService(registries, live_probe, probe_timeout, api_keys)` —
  `collect / summary / nodes / catalog / health / create_app / run`;
  dashboard at `/`.

## `geonexus.adapters` — standards adapters

- STAC: `import_stac_item / fetch_stac_item / stac_item_to_geocard /
  geocard_to_stac_item / geocard_to_stac_catalog / save_stac_item`.
- OGC API Features/Processes: `fetch_ogc_collection / fetch_ogc_feature /
  fetch_ogc_process / list_ogc_collections / list_ogc_processes` and the
  `ogc_*_to_geocard` mappers.
- OGC API - Coverages: `fetch_ogc_coverage_metadata / fetch_coverage_range /
  parse_coveragejson / coverage_to_geotiff`.
- OGC Processes bridge: `OgcProcessExecutor / make_ogc_process_skill /
  make_ogc_process_handler / register_ogc_process_skill`.
- Errors: `StacAdapterError`, `OgcAdapterError`, `OgcCoverageError`,
  `OgcProcessExecutionError`.

## `geonexus.mcp_adapter` — MCP bridge

- `create_mcp_server(geomcp_server)`, `create_streamable_http_app(server)`,
  `run_stdio(server)`, `run_http(server, host, port, path)`.

## CLI

`geonexus` console script: `version`, `card
validate|inspect|import-stac|import-ogc|import-ogc-process|
import-ogc-coverage|export-stac`, `node start`, `registry
start|register|search|skill|sync`, `skill list`, `inspector`, `agent
run|ask`, `mcp run|serve`, `ggihs start`, `demo …`, `init`.
