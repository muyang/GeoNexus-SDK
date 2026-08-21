"""GeoMCP — the GeoNexus geospatial interaction protocol (JSON-RPC 2.0)."""

from .client import GeoMCPClient, GeoMCPClientError
from .models import (
    DescribeParams,
    ExecuteParams,
    GeoMCPError,
    GeoMCPRequest,
    GeoMCPResponse,
    SpatialContext,
    TemporalContext,
)
from .protocol import (
    CONTRACT_NOT_SATISFIED,
    EXECUTION_FAILED,
    GEOCARD_NOT_FOUND,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SKILL_NOT_FOUND,
    GeoMCPDispatcher,
    GeoMCPProtocolError,
    build_request,
    build_response,
    make_request_id,
    parse_request,
)
from .server import PROTOCOL_VERSION, GeoMCPServer

__all__ = [
    "GeoMCPClient",
    "GeoMCPClientError",
    "GeoMCPServer",
    "GeoMCPDispatcher",
    "GeoMCPProtocolError",
    "PROTOCOL_VERSION",
    "GeoMCPRequest",
    "GeoMCPResponse",
    "GeoMCPError",
    "ExecuteParams",
    "DescribeParams",
    "SpatialContext",
    "TemporalContext",
    "build_request",
    "build_response",
    "make_request_id",
    "parse_request",
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    "CONTRACT_NOT_SATISFIED",
    "SKILL_NOT_FOUND",
    "GEOCARD_NOT_FOUND",
    "EXECUTION_FAILED",
]
