# API Stability Policy (v1.0)

As of the **1.0.0** release the following is the public, stable API of the
GeoNexus SDK. Anything not listed here is internal and may change without
notice (`_`-prefixed names, private modules, import paths under
`geonexus.*.server` internals).

## Stable public API

| Module | Exported names |
|--------|----------------|
| `geonexus.geocard` | `GeoCard`, `GeoCardBuilder`, `GeoCardError`, `GeoCardValidationError`, `ContractValidator`, `ContractResult`, `SchemaReport`, `validate_card_schema`, `load_geocard`, `load_geocard_dict`, `load_many`, section models (`SpatialSection`, `TemporalSection`, `Band`, `IOField`, `Capability`, `AccessSection`, `ProvenanceSection`, `LicenseField`, `ComplianceSection`, `TrustSection`, `RuntimeSection`, `RenderingSection`) |
| `geonexus.geomcp` | `GeoMCPServer`, `GeoMCPClient`, `GeoMCPClientError`, `GeoMCPDispatcher`, `GeoMCPProtocolError`, `PROTOCOL_VERSION`, `GeoMCPRequest`, `GeoMCPResponse`, `GeoMCPError`, `ExecuteParams`, `DescribeParams`, `SpatialContext`, `TemporalContext`, `build_request`, `build_response`, `make_request_id`, `parse_request`, error-code constants (`PARSE_ERROR` … `EXECUTION_FAILED`) |
| `geonexus.geonode` | `GeoNode`, `RunningServer`, `GeoNodeError`, `DuplicateEntryError`, `EntryNotFoundError`, `GeoCardRegistry`, `SkillRegistry`, `LocalRuntime`, `Skill`, `SkillContext` |
| `geonexus.registry` | `RegistryServer`, `RegistryClient`, `RegistryClientError`, `RegistryStore`, `RegistryFederator`, `RegistryEntry`, `RegistrySearchResult`, `SkillDescriptor`, `SkillEntry`, status constants (`STATUS_PENDING`, `STATUS_APPROVED`, `STATUS_REJECTED`, `REVIEW_STATUSES`); client `approve` / `reject` / `register(status=)` / `list_cards(status=)`; server `/cards/{id}/approve|reject` |
| `geonexus.federation` | `FederatedGeoMCPClient`, `FederatedExecutionError` |
| `geonexus.agent` | `Goal`, `GoalStep`, `Plan`, `PlanStep`, `GeoAgentPlanner`, `PlanExecutor`, `run_goal`, `ExecutionError`, `LLMConfig`, `LLMGoalPlanner`, `plan_from_text`, `plan_from_text_with_registry`, `PlanReflector`, `ReflectiveExecutor`, `ReflectionAdvice`, `ReflectionError`, `evaluate_plan`, action constants (`ACTION_RETRY`, `ACTION_REPLACE`, `ACTION_SKIP`, `ACTION_ABORT`), `DEFAULT_MAX_REFLECTIONS` |
| `geonexus.ggihs` | `GGIHSService` |
| `geonexus.resource` | `ComputeCapability`, `ResourceMatch`, `ResourceError`, `match_resource`, `resolve_compute_capabilities`, `parse_memory` |
| `geonexus.metadata` | `inspect_raster`, `raster_stats`, `raster_to_geocard` |
| `geonexus.web` | `WebConfig`, `JWTConfig`, `BearerAuth`, `APIKeyAuth`, `AuthError`, `create_token`, `decode_token`, `create_web_router`, `create_web_app`, `run_web`, `TaskManager`, `Task`, `TaskNotFoundError`, `TaskNotCancellableError`, `LoginRequest`, `LoginResponse`, `ExecuteRequest`, `GoalRequest`, `TaskResponse`, state constants (`QUEUED`, `RUNNING`, `DONE`, `FAILED`, `CANCELLED`) |
| `geonexus.adapters` | `import_stac_item`, `fetch_stac_item`, `stac_item_to_geocard`, `geocard_to_stac_item`, `geocard_to_stac_catalog`, `save_stac_item`, `fetch_ogc_collection`, `fetch_ogc_feature`, `fetch_ogc_process`, `list_ogc_collections`, `list_ogc_processes`, `ogc_collection_to_geocard`, `ogc_feature_to_geocard`, `ogc_process_to_geocard`, `fetch_ogc_coverage_metadata`, `fetch_coverage_range`, `parse_coveragejson`, `coverage_to_geotiff`, `OgcProcessExecutor`, `make_ogc_process_skill`, `make_ogc_process_handler`, `register_ogc_process_skill`, `list_ogc_record_collections`, `fetch_ogc_record`, `list_ogc_records`, `ogc_record_collection_to_geocard`, `ogc_record_to_geocard`, `list_ogc_tilesets`, `fetch_ogc_tileset`, `ogc_tileset_to_geocard`, `list_ogc_styles`, `fetch_ogc_style`, `ogc_style_to_geocard`, `ogc_visualization_to_geocards`, `fetch_wms_capabilities`, `list_wms_layers`, `wms_layer_to_geocard`, `fetch_wmts_capabilities`, `list_wmts_layers`, `wmts_layer_to_geocard`, and their error types (`StacAdapterError`, `OgcAdapterError`, `OgcCoverageError`, `OgcProcessExecutionError`, `OgcLegacyError`) |
| `geonexus.mcp_adapter` | `create_mcp_server`, `create_streamable_http_app`, `run_stdio`, `run_http` |
| `geonexus.mcp_client` | `MCPToolClient`, `MCPTool`, `MCPClientError` |
| CLI | `geonexus version`, `card validate|inspect|import-stac|import-ogc|import-ogc-process|import-ogc-coverage|import-ogc-records|import-ogc-tiles|import-ogc-legacy|export-stac`, `node start`, `registry start|register|search|sync|skill`, `skill list`, `inspector`, `agent run|ask`, `mcp run|serve|import`, `ggihs start`, `demo *`, `init` |

## Versioning & compatibility

- **Semantic Versioning.** `MAJOR.MINOR.PATCH`. Breaking changes only in a
  MAJOR release.
- **Backward compatibility.** Cards written against the 0.1 schema remain
  valid (schema `geocard_version` accepts both `0.1` and `1.0`). GeoMCP
  requests built per this protocol remain valid; the protocol version is
  advertised in `geo.capabilities`.
- **Deprecation policy.** A public name may be deprecated in any release;
  removal happens no earlier than the **next MAJOR** release. Deprecations
  are announced in `CHANGELOG.md` and logged with `DeprecationWarning` when
  practical.
- **Protocol stability.** The GeoMCP method set (`geo.capabilities`,
  `geo.describe`, `geo.execute`, `geo.health`), the JSON-RPC 2.0 envelope and
  the error code registry are frozen for the 1.x line; additions go through
  capability negotiation (`geo.capabilities` → `methods`).

## What is NOT stable

- `geonexus.geonode.node.RunningServer` internals, anything prefixed `_`,
  implementation modules not listed above, and the exact wire format of
  non-protocol HTTP endpoints of internal services (registry/ggihs internal
  JSON shapes may evolve within 1.x with additive changes only where
  practical).
