"""GeoCard — machine-readable identity, capability and contract description
for geospatial assets."""

from .builder import GeoCardBuilder
from .loader import load_geocard, load_geocard_dict, load_many
from .model import (
    AccessSection,
    Band,
    Capability,
    ComplianceSection,
    GeoCard,
    GeoCardError,
    GeoCardValidationError,
    IOField,
    LicenseField,
    ProvenanceSection,
    RenderingSection,
    RuntimeSection,
    SpatialSection,
    TemporalSection,
    TrustSection,
)
from .validator import (
    ContractResult,
    ContractValidator,
    SchemaReport,
    validate_card_schema,
)

__all__ = [
    "GeoCard",
    "GeoCardBuilder",
    "GeoCardError",
    "GeoCardValidationError",
    "ContractValidator",
    "ContractResult",
    "SchemaReport",
    "validate_card_schema",
    "load_geocard",
    "load_geocard_dict",
    "load_many",
    "SpatialSection",
    "TemporalSection",
    "Band",
    "IOField",
    "Capability",
    "AccessSection",
    "ProvenanceSection",
    "LicenseField",
    "ComplianceSection",
    "TrustSection",
    "RuntimeSection",
    "RenderingSection",
]
