"""GeoNexus adapters — bridge existing geospatial standards into GeoCard.

Adapters never replace standards; they translate them. A STAC Item, an OGC
API Features collection/feature or an OGC API - Processes process becomes a
GeoCard that the GeoNexus registry, contract validator and protocol can
understand.
"""

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
from .stac import (
    StacAdapterError,
    fetch_stac_item,
    import_stac_item,
    stac_item_to_geocard,
)
from .stac_write import geocard_to_stac_catalog, geocard_to_stac_item, save_stac_item

__all__ = [
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
]
