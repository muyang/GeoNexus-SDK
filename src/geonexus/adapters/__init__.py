"""GeoNexus adapters — bridge existing geospatial standards into GeoCard.

Adapters never replace standards; they translate them. A STAC Item, an OGC
API Features collection/feature or an OGC API - Processes process becomes a
GeoCard that the GeoNexus registry, contract validator and protocol can
understand.
"""

from .oge_client import (
    OgeClient,
    OgeClientError,
    OgeAuthError,
    OgeExecutionError,
    OgeTokenResponse,
    OgeAppKeyResponse,
    OgeUploadResponse,
    OgeExecuteResponse,
    OgeProcessStatus,
    OgeProcessInfo,
)
from .oge_credential import (
    OgeCredential,
    OgeCredentialCache,
    OgeCredentialManager,
)
from .oge_protocol import (
    OgeProtocolMapper,
    OgeReference,
    OgeReferenceType,
    parse_oge_reference,
    oge_ref_to_geocard_id,
    geocard_id_to_oge_ref,
)
from .oge_executor import (
    OgeExecutor,
    OgeTaskPoller,
    OgeExecutionResult,
)
from .oge_card_builder import (
    OgeCardBuilder,
    build_operator_card,
    build_model_card,
)
from .oge_skill import (
    OgeSkillAdapter,
    discover_oge_skills,
)
from .ogc import (
    OgcAdapterError,
    OgcApiClient,
    fetch_ogc_collection,
    fetch_ogc_feature,
    fetch_ogc_process,
    list_ogc_collections,
    list_ogc_processes,
    ogc_collection_to_geocard,
    ogc_feature_to_geocard,
    ogc_process_to_geocard,
)
from .ogc_coverage import (
    OgcCoverageError,
    coverage_to_geotiff,
    fetch_coverage_range,
    fetch_ogc_coverage_metadata,
    ogc_coverage_metadata_to_geocard,
    parse_coveragejson,
)
from .ogc_exec import (
    OgcProcessExecutionError,
    OgcProcessExecutor,
    make_ogc_process_handler,
    make_ogc_process_skill,
    register_ogc_process_skill,
)
from .ogc_records import (
    fetch_ogc_record,
    list_ogc_record_collections,
    list_ogc_records,
    ogc_record_collection_to_geocard,
    ogc_record_to_geocard,
)
from .ogc_tiles import (
    fetch_ogc_style,
    fetch_ogc_tileset,
    list_ogc_styles,
    list_ogc_tilesets,
    ogc_style_to_geocard,
    ogc_tileset_to_geocard,
    ogc_visualization_to_geocards,
)
from .ogc_wms import (
    OgcLegacyError,
    fetch_wms_capabilities,
    fetch_wmts_capabilities,
    list_wms_layers,
    list_wmts_layers,
    wms_layer_to_geocard,
    wmts_layer_to_geocard,
)
from .stac import (
    StacAdapterError,
    fetch_stac_item,
    import_stac_item,
    stac_item_to_geocard,
)
from .stac_write import geocard_to_stac_catalog, geocard_to_stac_item, save_stac_item

__all__ = [
    # OGE (v1.1)
    "OgeClient",
    "OgeClientError",
    "OgeAuthError",
    "OgeExecutionError",
    "OgeTokenResponse",
    "OgeAppKeyResponse",
    "OgeUploadResponse",
    "OgeExecuteResponse",
    "OgeProcessStatus",
    "OgeProcessInfo",
    "OgeCredential",
    "OgeCredentialCache",
    "OgeCredentialManager",
    "OgeProtocolMapper",
    "OgeReference",
    "OgeReferenceType",
    "parse_oge_reference",
    "oge_ref_to_geocard_id",
    "geocard_id_to_oge_ref",
    "OgeExecutor",
    "OgeTaskPoller",
    "OgeExecutionResult",
    "OgeCardBuilder",
    "build_operator_card",
    "build_model_card",
    "OgeSkillAdapter",
    "discover_oge_skills",
    # STAC
    "StacAdapterError",
    "fetch_stac_item",
    "import_stac_item",
    "stac_item_to_geocard",
    "geocard_to_stac_item",
    "geocard_to_stac_catalog",
    "save_stac_item",
    "OgcAdapterError",
    "OgcApiClient",
    "ogc_collection_to_geocard",
    "ogc_feature_to_geocard",
    "ogc_process_to_geocard",
    "fetch_ogc_collection",
    "fetch_ogc_feature",
    "fetch_ogc_process",
    "list_ogc_collections",
    "list_ogc_processes",
    "OgcProcessExecutionError",
    "OgcProcessExecutor",
    "make_ogc_process_handler",
    "make_ogc_process_skill",
    "register_ogc_process_skill",
    "OgcCoverageError",
    "ogc_coverage_metadata_to_geocard",
    "parse_coveragejson",
    "fetch_ogc_coverage_metadata",
    "fetch_coverage_range",
    "coverage_to_geotiff",
    # OGC API - Records (v1.1)
    "list_ogc_record_collections",
    "fetch_ogc_record",
    "list_ogc_records",
    "ogc_record_collection_to_geocard",
    "ogc_record_to_geocard",
    # OGC API - Tiles / Maps / Styles (v1.1)
    "list_ogc_tilesets",
    "fetch_ogc_tileset",
    "ogc_tileset_to_geocard",
    "list_ogc_styles",
    "fetch_ogc_style",
    "ogc_style_to_geocard",
    "ogc_visualization_to_geocards",
    # WMS / WMTS (v1.1)
    "OgcLegacyError",
    "fetch_wms_capabilities",
    "list_wms_layers",
    "wms_layer_to_geocard",
    "fetch_wmts_capabilities",
    "list_wmts_layers",
    "wmts_layer_to_geocard",
]
