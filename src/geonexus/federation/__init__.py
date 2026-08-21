"""GeoNexus Federation (V0.3) — discovery and pushdown execution.

The federated client asks a shared GeoCard Registry which node owns a card,
then routes ``geo.execute`` requests **to the node that owns the data**
(pushdown: computation moves to the data, not the other way round).
"""

from .client import FederatedExecutionError, FederatedGeoMCPClient

__all__ = ["FederatedGeoMCPClient", "FederatedExecutionError"]
