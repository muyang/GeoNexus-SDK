"""GeoMCP client.

:class:`GeoMCPClient` talks to a GeoMCP server over HTTP (FastAPI transport)
using the JSON-RPC 2.0 protocol defined in :mod:`geonexus.geomcp.protocol`.

It handles request ids, error responses, timeouts and response validation.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .models import SpatialContext, TemporalContext
from .protocol import build_request, make_request_id

logger = logging.getLogger(__name__)


class GeoMCPClientError(Exception):
    """Raised for transport failures and protocol error responses."""

    def __init__(
        self,
        message: str,
        code: int | None = None,
        data: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data

    def __str__(self) -> str:
        if self.code is not None:
            return f"[{self.code}] {self.message}"
        return self.message


class GeoMCPClient:
    """Synchronous HTTP client for a GeoMCP server.

    Args:
        base_url: Server root URL, e.g. ``http://127.0.0.1:8787``.
        timeout: Request timeout in seconds.
        api_key: Optional ``X-API-Key`` sent on every request. Required when
            the server enforces node authentication (``geo.execute``).
        client: Optional pre-configured ``httpx.Client`` (advanced use).
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        api_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self._client = client or httpx.Client(base_url=self.base_url, timeout=timeout)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def execute(
        self,
        skill: str,
        geocards: list[str] | None = None,
        spatial: dict[str, Any] | SpatialContext | None = None,
        temporal: dict[str, Any] | TemporalContext | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a skill on the server (``geo.execute``)."""
        spatial_dict = (
            spatial
            if isinstance(spatial, dict)
            else (spatial.model_dump() if spatial is not None else None)
        )
        temporal_dict = (
            temporal
            if isinstance(temporal, dict)
            else (temporal.model_dump() if temporal is not None else None)
        )
        return self._call(
            "geo.execute",
            {
                "skill": skill,
                "geocards": geocards or [],
                "spatial": spatial_dict,
                "temporal": temporal_dict,
                "params": params or {},
                "request_id": request_id,
            },
            request_id=request_id,
        )

    def describe(
        self,
        geocards: list[str] | None = None,
        skills: list[str] | None = None,
    ) -> dict[str, Any]:
        """Describe registered GeoCards and skills (``geo.describe``)."""
        params: dict[str, Any] = {}
        if geocards is not None:
            params["geocards"] = geocards
        if skills is not None:
            params["skills"] = skills
        return self._call("geo.describe", params)

    def capabilities(self) -> dict[str, Any]:
        """Fetch the server's capability surface (``geo.capabilities``)."""
        return self._call("geo.capabilities", {})

    def health(self) -> dict[str, Any]:
        """Call the GeoMCP health method (``geo.health``)."""
        return self._call("geo.health", {})

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #
    def _call(
        self,
        method: str,
        params: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or make_request_id()
        payload = build_request(method, params, id=request_id)
        headers = {"X-API-Key": self.api_key} if self.api_key else None
        try:
            response = self._client.post("/geomcp", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise GeoMCPClientError(
                f"GeoMCP request timed out after {self.timeout}s (method={method})"
            ) from exc
        except httpx.HTTPError as exc:
            raise GeoMCPClientError(f"GeoMCP transport error (method={method}): {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise GeoMCPClientError(
                f"GeoMCP response is not JSON (status={response.status_code})"
            ) from exc

        # Validate the JSON-RPC envelope.
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0":
            raise GeoMCPClientError(
                f"Invalid JSON-RPC response envelope (status={response.status_code})"
            )
        if data.get("id") != request_id:
            raise GeoMCPClientError(
                f"JSON-RPC id mismatch: expected {request_id!r}, got {data.get('id')!r}"
            )
        if "error" in data and data["error"] is not None:
            err = data["error"]
            raise GeoMCPClientError(
                err.get("message", "GeoMCP error"),
                code=err.get("code"),
                data=err.get("data"),
            )
        if "result" not in data:
            raise GeoMCPClientError("JSON-RPC response has neither result nor error")
        result = data["result"]
        if not isinstance(result, dict):
            raise GeoMCPClientError(f"GeoMCP result must be an object, got {type(result).__name__}")
        return result

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> GeoMCPClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
