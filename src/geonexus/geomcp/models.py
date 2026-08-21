"""GeoMCP protocol data models.

GeoMCP is the GeoNexus geospatial interaction protocol. For the MVP it is a
lightweight JSON-RPC 2.0 protocol with geospatial context. These Pydantic
models describe the request/response envelope and the geospatial context
parameters.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SpatialContext(BaseModel):
    """Geospatial context attached to a GeoMCP request."""

    bbox: list[float] = Field(
        description="Bounding box [west, south, east, north] in the given CRS."
    )
    crs: str = Field(default="EPSG:4326", description="CRS of the bbox.")
    resolution: float | None = Field(default=None, description="Requested resolution in metres.")


class TemporalContext(BaseModel):
    """Temporal context attached to a GeoMCP request."""

    start: str | None = Field(default=None, description="ISO 8601 start.")
    end: str | None = Field(default=None, description="ISO 8601 end.")
    interval: str | None = Field(default=None, description="e.g. 'P16D'.")


class ExecuteParams(BaseModel):
    """Parameters of ``geo.execute``."""

    skill: str = Field(description="Name of the skill to execute.")
    geocards: list[str] = Field(
        default_factory=list,
        description="GeoCard ids the skill should operate on (asset references).",
    )
    spatial: SpatialContext | None = None
    temporal: TemporalContext | None = None
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Skill-specific execution parameters.",
    )
    request_id: str | None = Field(
        default=None, description="Optional caller-provided correlation id."
    )


class DescribeParams(BaseModel):
    """Parameters of ``geo.describe``."""

    geocards: list[str] | None = Field(
        default=None, description="GeoCard ids to describe; None describes all."
    )
    skills: list[str] | None = Field(
        default=None, description="Skill names to describe; None describes all."
    )


class GeoMCPRequest(BaseModel):
    """A JSON-RPC 2.0 request envelope."""

    jsonrpc: str = "2.0"
    id: Any | None = Field(default=None, description="Request id (echoed).")
    method: str
    params: dict[str, Any] | None = None


class GeoMCPError(BaseModel):
    """A JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any | None = None


class GeoMCPResponse(BaseModel):
    """A JSON-RPC 2.0 response envelope."""

    jsonrpc: str = "2.0"
    id: Any | None = None
    result: Any | None = None
    error: GeoMCPError | None = None
