"""GeoMCP protocol layer (transport-independent).

Implements the JSON-RPC 2.0 semantics of the GeoMCP protocol:

- request/response envelope construction and parsing
- error handling with JSON-RPC error codes
- method dispatch for ``geo.capabilities``, ``geo.describe``,
  ``geo.execute`` and ``geo.health``

The protocol layer does **not** depend on HTTP; transports (FastAPI, CLI,
in-process) plug into :class:`GeoMCPDispatcher`.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from .models import GeoMCPRequest

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Error codes (JSON-RPC 2.0 reserved + GeoNexus extension space)
# --------------------------------------------------------------------------- #
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# GeoNexus application error codes (positive extension space, per JSON-RPC).
CONTRACT_NOT_SATISFIED = 2000
SKILL_NOT_FOUND = 2001
GEOCARD_NOT_FOUND = 2002
EXECUTION_FAILED = 2003
INVALID_ARGUMENT = 2004

METHODS = ("geo.capabilities", "geo.describe", "geo.execute", "geo.health")


class GeoMCPProtocolError(Exception):
    """Protocol-level error carrying a JSON-RPC error code."""

    def __init__(
        self,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_error(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


def make_request_id() -> str:
    """Generate a unique JSON-RPC request id."""
    return uuid.uuid4().hex


def build_request(
    method: str,
    params: dict[str, Any] | None = None,
    id: Any | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC request envelope (for clients)."""
    return {
        "jsonrpc": "2.0",
        "id": id if id is not None else make_request_id(),
        "method": method,
        "params": params or {},
    }


def build_response(
    id: Any | None,
    result: Any | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC response envelope (for servers)."""
    response: dict[str, Any] = {"jsonrpc": "2.0", "id": id}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    return response


def parse_request(payload: dict[str, Any]) -> GeoMCPRequest:
    """Parse and validate a JSON-RPC request envelope.

    Raises :class:`GeoMCPProtocolError` (INVALID_REQUEST) for malformed
    envelopes.
    """
    if not isinstance(payload, dict):
        raise GeoMCPProtocolError(INVALID_REQUEST, "Request must be a JSON object")
    jsonrpc = payload.get("jsonrpc")
    if jsonrpc != "2.0":
        raise GeoMCPProtocolError(INVALID_REQUEST, "jsonrpc must be '2.0'")
    if "method" not in payload or not isinstance(payload["method"], str):
        raise GeoMCPProtocolError(INVALID_REQUEST, "method must be a string")
    params = payload.get("params")
    if params is not None and not isinstance(params, dict):
        raise GeoMCPProtocolError(INVALID_REQUEST, "params must be an object")
    try:
        return GeoMCPRequest(
            jsonrpc=jsonrpc,
            id=payload.get("id"),
            method=payload["method"],
            params=params,
        )
    except Exception as exc:  # pragma: no cover - defensive
        raise GeoMCPProtocolError(INVALID_REQUEST, f"Invalid request: {exc}") from exc


Handler = Callable[[dict[str, Any]], dict[str, Any]]


class GeoMCPDispatcher:
    """JSON-RPC method dispatcher for GeoMCP.

    Decouples protocol semantics from transport. Handlers receive the raw
    ``params`` dictionary and return a result dictionary.
    """

    def __init__(
        self,
        capabilities_fn: Handler | None = None,
        describe_fn: Handler | None = None,
        execute_fn: Handler | None = None,
        health_fn: Handler | None = None,
    ) -> None:
        self._handlers: dict[str, Handler] = {}
        if capabilities_fn:
            self._handlers["geo.capabilities"] = capabilities_fn
        if describe_fn:
            self._handlers["geo.describe"] = describe_fn
        if execute_fn:
            self._handlers["geo.execute"] = execute_fn
        if health_fn:
            self._handlers["geo.health"] = health_fn

    def register(self, method: str, handler: Handler) -> None:
        """Register (or replace) a handler for a GeoMCP method."""
        if method not in METHODS:
            raise ValueError(f"Unknown GeoMCP method: {method}")
        self._handlers[method] = handler

    def methods(self) -> list[str]:
        """Return the list of supported method names."""
        return list(self._handlers.keys())

    def dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a raw JSON-RPC payload to the appropriate handler.

        Always returns a JSON-RPC response envelope (never raises for
        malformed input).
        """
        try:
            request = parse_request(payload)
        except GeoMCPProtocolError as exc:
            logger.debug("Invalid GeoMCP request: %s", exc.message)
            return build_response(
                payload.get("id") if isinstance(payload, dict) else None,
                error=exc.to_error(),
            )
        handler = self._handlers.get(request.method)
        if handler is None:
            return build_response(
                request.id,
                error={
                    "code": METHOD_NOT_FOUND,
                    "message": f"Method not found: {request.method}",
                    "data": {"supported": self.methods()},
                },
            )
        try:
            result = handler(request.params or {})
            return build_response(request.id, result=result)
        except GeoMCPProtocolError as exc:
            # Application-level refusals (e.g. contract not satisfied) are a
            # normal part of the protocol flow; the error is returned to the
            # caller in the response envelope.
            logger.info("GeoMCP %s refused: %s", request.method, exc.message)
            return build_response(request.id, error=exc.to_error())
        except Exception as exc:  # noqa: BLE001 - protocol boundary
            logger.exception("GeoMCP %s raised an internal error", request.method)
            return build_response(
                request.id,
                error={
                    "code": INTERNAL_ERROR,
                    "message": f"Internal error: {exc}",
                },
            )
