"""Shared GeoCard Registry — discovery layer of the GeoNexus network.

A registry is a **card-only** service: it stores GeoCards plus the endpoint
of the node that owns each asset. Data itself never moves to the registry
(data stays where it is; only descriptions are shared).
"""

from .client import RegistryClient, RegistryClientError
from .federation import RegistryFederator
from .models import RegistryEntry, RegistrySearchResult, SkillDescriptor, SkillEntry
from .server import RegistryServer
from .store import RegistryStore

__all__ = [
    "RegistryClient",
    "RegistryClientError",
    "RegistryEntry",
    "RegistrySearchResult",
    "SkillDescriptor",
    "SkillEntry",
    "RegistryServer",
    "RegistryStore",
    "RegistryFederator",
]
