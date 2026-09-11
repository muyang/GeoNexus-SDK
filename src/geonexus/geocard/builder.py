"""Fluent GeoCard builder.

Provides a chainable API for constructing :class:`~geonexus.geocard.model.GeoCard`
objects:

    card = (
        GeoCardBuilder(id="x", type="data", name="X", description="...")
        .spatial(bbox=[...], crs="EPSG:4326", resolution=10)
        .band("B04", dtype="uint16")
        .capability("ndvi")
        .build()
    )
"""

from __future__ import annotations

from typing import Any

from .model import (
    AccessSection,
    Band,
    Capability,
    ComplianceSection,
    GeoCard,
    InterfaceSection,
    IOField,
    LicenseField,
    ProvenanceSection,
    RenderingSection,
    RuntimeSection,
    SpatialSection,
    TemporalSection,
    TrustSection,
)


class GeoCardBuilder:
    """Chainable constructor for GeoCard objects."""

    def __init__(
        self,
        id: str,
        type: str,
        name: str,
        description: str,
        geocard_version: str = "1.0",
    ) -> None:
        self._card = GeoCard(
            geocard_version=geocard_version,
            id=id,
            type=type,
            name=name,
            description=description,
        )

    # -- top-level ------------------------------------------------------------ #
    def tag(self, *tags: str) -> GeoCardBuilder:
        """Add free-form discovery tags."""
        self._card.tags.extend(tags)
        return self

    # -- sections ------------------------------------------------------------- #
    def spatial(
        self,
        bbox: list[float] | None = None,
        crs: str | None = None,
        resolution: float | None = None,
        geometry: str | None = None,
    ) -> GeoCardBuilder:
        """Declare the spatial extent / characteristics of the asset."""
        self._card.spatial = SpatialSection(
            bbox=bbox, crs=crs, resolution=resolution, geometry=geometry
        )
        return self

    def temporal(
        self,
        start: str | None = None,
        end: str | None = None,
        interval: str | None = None,
    ) -> GeoCardBuilder:
        """Declare the temporal coverage of the asset."""
        self._card.temporal = TemporalSection(start=start, end=end, interval=interval)
        return self

    def band(
        self,
        name: str,
        dtype: str | None = None,
        units: str | None = None,
        description: str | None = None,
    ) -> GeoCardBuilder:
        """Add a spectral / thematic band."""
        self._card.bands.append(Band(name=name, dtype=dtype, units=units, description=description))
        return self

    def input(
        self,
        name: str,
        type: str,
        description: str | None = None,
        required: bool = False,
    ) -> GeoCardBuilder:
        """Add a declared input (skills, models, workflows, compute)."""
        self._card.inputs.append(
            IOField(name=name, type=type, description=description, required=required)
        )
        return self

    def output(
        self,
        name: str,
        type: str,
        description: str | None = None,
    ) -> GeoCardBuilder:
        """Add a declared output."""
        self._card.outputs.append(IOField(name=name, type=type, description=description))
        return self

    def capability(self, name: str, description: str | None = None) -> GeoCardBuilder:
        """Declare a capability provided by the asset."""
        self._card.capabilities.append(Capability(name=name, description=description))
        return self

    def access(
        self,
        protocol: str | None = None,
        endpoint: str | None = None,
        auth: str | None = None,
        format: str | None = None,
    ) -> GeoCardBuilder:
        """Declare how to reach the asset."""
        self._card.access = AccessSection(
            protocol=protocol, endpoint=endpoint, auth=auth, format=format
        )
        return self

    def provenance(
        self,
        provider: str | None = None,
        source: str | None = None,
        lineage: str | None = None,
    ) -> GeoCardBuilder:
        """Declare where the asset came from."""
        self._card.provenance = ProvenanceSection(provider=provider, source=source, lineage=lineage)
        return self

    def license(self, name: str | None = None, url: str | None = None) -> GeoCardBuilder:
        """Declare the license (structured form)."""
        self._card.license = LicenseField(name=name, url=url)
        return self

    def compliance(
        self,
        sovereignty: str | None = None,
        restrictions: list[str] | None = None,
        sensitivity: str | None = None,
    ) -> GeoCardBuilder:
        """Declare sovereignty and compliance constraints."""
        self._card.compliance = ComplianceSection(
            sovereignty=sovereignty,
            restrictions=restrictions or [],
            sensitivity=sensitivity,
        )
        return self

    def trust(self, verified: bool = False, score: float | None = None) -> GeoCardBuilder:
        """Declare trust metadata (informational in the MVP)."""
        self._card.trust = TrustSection(verified=verified, score=score)
        return self

    def runtime(
        self,
        cpu: Any | None = None,
        memory: str | None = None,
        gpu: str | None = None,
    ) -> GeoCardBuilder:
        """Declare runtime requirements."""
        self._card.runtime = RuntimeSection(cpu=cpu, memory=memory, gpu=gpu)
        return self

    def interface(self, type: str | None = None, version: str | None = None) -> GeoCardBuilder:
        """Declare interface versioning information."""
        self._card.interface = InterfaceSection(type=type, version=version)
        return self

    def rendering(
        self,
        min: float | None = None,
        max: float | None = None,
        colormap: str | None = None,
        opacity: float | None = None,
    ) -> GeoCardBuilder:
        """Declare visualisation hints."""
        self._card.rendering = RenderingSection(
            min=min, max=max, colormap=colormap, opacity=opacity
        )
        return self

    # -- finalise ------------------------------------------------------------- #
    def build(self) -> GeoCard:
        """Return the constructed GeoCard (no schema validation performed here;
        call ``card.validate()`` to check against the official schema)."""
        return self._card
