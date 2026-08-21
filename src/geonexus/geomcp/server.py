"""GeoMCP server.

:class:`GeoMCPServer` provides the GeoMCP capability surface (tools,
resources, GeoCards) on top of the transport-independent
:class:`~geonexus.geomcp.protocol.GeoMCPDispatcher`. A FastAPI HTTP transport
is provided by :meth:`GeoMCPServer.create_app`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..geocard.model import GeoCard
from ..geocard.validator import ContractValidator
from .models import DescribeParams, ExecuteParams
from .protocol import (
    CONTRACT_NOT_SATISFIED,
    EXECUTION_FAILED,
    GEOCARD_NOT_FOUND,
    INVALID_PARAMS,
    SKILL_NOT_FOUND,
    GeoMCPDispatcher,
    GeoMCPProtocolError,
)

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "1.0.0"

# Internal marker preventing delegation loops (never reaches skill handlers).
DELEGATE_MARKER = "__geonode_delegate"


class ExecutionEngine(Protocol):
    """Interface implemented by anything that can execute a GeoMCP request.

    The Local GeoNode runtime implements this protocol; a custom engine can
    be provided to the server for standalone use.
    """

    def execute(self, params: ExecuteParams) -> dict[str, Any]: ...


class _Tool:
    """A registered GeoMCP tool."""

    def __init__(
        self,
        name: str,
        handler: Any,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.handler = handler
        self.description = description or ""
        self.input_schema = input_schema or {}
        self.output_schema = output_schema or {}

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


class GeoMCPServer:
    """GeoMCP capability server.

    Args:
        name: Node / server display name.
        engine: Optional :class:`ExecutionEngine` that runs ``geo.execute``.
        geocard_registry: Optional registry of GeoCards (must expose
            ``get(id)``, ``list()``). Defaults to an internal dict-backed
            registry.
        skill_registry: Optional registry of skills (must expose
            ``get(name)``, ``list()``, ``has(name)``).
        contract_validator: Contract checker used to gate ``geo.execute``.
        registry_url: Optional shared GeoCard Registry endpoint. When set,
            requests referencing GeoCards this node does not own are
            **delegated** to the owning node (V1.0+ node-level pushdown).
        forward_timeout: Timeout for delegated execution requests.
    """

    def __init__(
        self,
        name: str = "geonexus-node",
        engine: ExecutionEngine | None = None,
        geocard_registry: Any | None = None,
        skill_registry: Any | None = None,
        contract_validator: ContractValidator | None = None,
        registry_url: str | None = None,
        forward_timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.engine = engine
        self.registry_url = registry_url
        self.forward_timeout = forward_timeout
        self.geocard_registry = geocard_registry
        self.skill_registry = skill_registry
        self.contract_validator = contract_validator or ContractValidator()

        self._tools: dict[str, _Tool] = {}
        self._resources: dict[str, dict[str, Any]] = {}
        self._cards: dict[str, GeoCard] = {}

        self._dispatcher = GeoMCPDispatcher(
            capabilities_fn=self._handle_capabilities,
            describe_fn=self._handle_describe,
            execute_fn=self._handle_execute,
            health_fn=self._handle_health,
        )

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register_tool(
        self,
        name: str,
        handler: Any,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        """Register a tool that can be advertised and executed."""
        self._tools[name] = _Tool(name, handler, description, input_schema, output_schema)
        logger.debug("Registered GeoMCP tool '%s'", name)

    def register_resource(self, name: str, **attributes: Any) -> None:
        """Register a resource (a named, addressable piece of data/service)."""
        self._resources[name] = {"name": name, **attributes}
        logger.debug("Registered GeoMCP resource '%s'", name)

    def register_geocard(self, card: GeoCard) -> None:
        """Register a GeoCard with this server (and its registry when set)."""
        self._cards[card.id] = card
        if self.geocard_registry is not None:
            self.geocard_registry.register(card)
        logger.debug("Registered GeoCard '%s'", card.id)

    # ------------------------------------------------------------------ #
    # Protocol handlers
    # ------------------------------------------------------------------ #
    def _handle_capabilities(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.capabilities()

    def _handle_health(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.health()

    def _handle_describe(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.describe(DescribeParams(**params))

    def _handle_execute(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            execute_params = ExecuteParams(**params)
        except Exception as exc:
            raise GeoMCPProtocolError(INVALID_PARAMS, f"Invalid geo.execute params: {exc}") from exc
        return self.execute(execute_params)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def capabilities(self) -> dict[str, Any]:
        """Advertise the server's protocol surface."""
        cards = (
            [c.to_dict() for c in self.geocard_registry.list_cards()]
            if self.geocard_registry is not None
            else [c.to_dict() for c in self._cards.values()]
        )
        skills = (
            [s.describe() for s in self.skill_registry.list_skills()]
            if self.skill_registry is not None
            else []
        )
        return {
            "protocol": "geomcp",
            "version": PROTOCOL_VERSION,
            "node": self.name,
            "methods": self._dispatcher.methods(),
            "tools": [t.describe() for t in self._tools.values()],
            "resources": list(self._resources.keys()),
            "skills": skills,
            "geocards": [c["id"] for c in cards],
            "geocard_ids": [c["id"] for c in cards],
        }

    def describe(self, params: DescribeParams | None = None) -> dict[str, Any]:
        """Describe registered GeoCards and/or skills."""
        params = params or DescribeParams()
        if self.geocard_registry is not None:
            cards = self.geocard_registry.list_cards()
        else:
            cards = list(self._cards.values())
        if params.geocards is not None:
            wanted = set(params.geocards)
            cards = [c for c in cards if c.id in wanted]
            missing = sorted(wanted - {c.id for c in cards})
            if missing:
                raise GeoMCPProtocolError(
                    GEOCARD_NOT_FOUND,
                    f"GeoCard(s) not found: {missing}",
                    {"missing": missing},
                )
        if self.skill_registry is not None:
            skills = self.skill_registry.list_skills()
        else:
            skills = []
        if params.skills is not None:
            wanted = set(params.skills)
            skills = [s for s in skills if s.name in wanted]
            missing = sorted(wanted - {s.name for s in skills})
            if missing:
                raise GeoMCPProtocolError(
                    SKILL_NOT_FOUND,
                    f"Skill(s) not found: {missing}",
                    {"missing": missing},
                )
        return {
            "geocards": [c.to_dict() for c in cards],
            "skills": [s.describe() for s in skills],
        }

    def execute(self, params: ExecuteParams) -> dict[str, Any]:
        """Execute a skill via the engine, gated by contract validation.

        When this node is bound to a shared registry (``registry_url``) and
        the request references GeoCards this node does not own, the request
        is **delegated** to the owning node (V1.0+ node-level pushdown) and
        the owner's result is relayed back. A single internal marker
        (``__geonode_delegate``) prevents delegation loops.
        """
        # 0. Delegation: unknown cards + registry bound + not already delegated.
        if params.geocards and self.registry_url and not params.params.get(DELEGATE_MARKER):
            unknown = [c for c in params.geocards if self._resolve_card(c) is None]
            if unknown:
                logger.info(
                    "Delegating geo.execute (cards %s) to owning node via %s",
                    unknown,
                    self.registry_url,
                )
                return self._delegate(params, unknown)

        # 1. Resolve the GeoCards referenced by the request.
        resolved_cards: list[GeoCard] = []
        if params.geocards:
            for card_id in params.geocards:
                card = self._resolve_card(card_id)
                if card is None:
                    raise GeoMCPProtocolError(
                        GEOCARD_NOT_FOUND,
                        f"GeoCard not found: {card_id}",
                        {"missing": card_id},
                    )
                resolved_cards.append(card)

        # 2. Contract satisfaction gating (registry / discovery -> contract).
        if params.geocards:
            spatial = params.spatial
            temporal = params.temporal
            for card in resolved_cards:
                result = self.contract_validator.check(
                    card,
                    bbox=spatial.bbox if spatial else None,
                    crs=spatial.crs if spatial else None,
                    start=temporal.start if temporal else None,
                    end=temporal.end if temporal else None,
                )
                if not result.satisfied:
                    raise GeoMCPProtocolError(
                        CONTRACT_NOT_SATISFIED,
                        f"GeoCard '{card.id}' does not satisfy the request contract",
                        {
                            "geocard": card.id,
                            "reasons": result.reasons,
                            "warnings": result.warnings,
                        },
                    )

        # 3. Execute via the engine (Local GeoNode runtime) or a registered tool.
        #    The delegation marker is internal and must not reach handlers.
        clean_params = params.model_copy(deep=True)
        clean_params.params.pop(DELEGATE_MARKER, None)
        if self.engine is not None:
            return self.engine.execute(clean_params)
        tool = self._tools.get(clean_params.skill)
        if tool is not None:
            return self._run_tool(tool, clean_params)
        raise GeoMCPProtocolError(
            SKILL_NOT_FOUND,
            f"Skill not found: {clean_params.skill}",
            {"available": list(self._tools.keys())},
        )

    def health(self) -> dict[str, Any]:
        """Health check payload."""
        return {
            "status": "ok",
            "node": self.name,
            "protocol": "geomcp",
            "version": PROTOCOL_VERSION,
            "time": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def set_registry(self, registry_url: str | None) -> None:
        """Bind (or unbind) this server to a shared GeoCard Registry.

        When bound, requests referencing unknown GeoCards are delegated to
        their owning node.
        """
        self.registry_url = registry_url
        logger.info("GeoMCP server '%s' registry binding: %s", self.name, registry_url)

    def _delegate(self, params: ExecuteParams, unknown: list[str]) -> dict[str, Any]:
        """Forward a request to the node that owns the first unknown card.

        One-hop semantics: the forwarded request carries the delegation
        marker, so the owner never re-delegates. The owner's result is
        relayed unchanged.
        """
        from ..registry import RegistryClient, RegistryClientError

        registry_url = self.registry_url
        assert registry_url is not None  # delegation is only entered when bound
        owners: dict[str, str] = {}
        try:
            with RegistryClient(registry_url) as registry:
                for card_id in unknown:
                    entry = registry.get(card_id)
                    if isinstance(entry, dict) and entry.get("node_url"):
                        owners[card_id] = entry["node_url"]
        except RegistryClientError as exc:
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Delegation failed: registry lookup error: {exc}",
            ) from exc
        missing = [c for c in unknown if c not in owners]
        if missing:
            raise GeoMCPProtocolError(
                GEOCARD_NOT_FOUND,
                f"GeoCard(s) not found at registry {registry_url}: {missing}",
                {"missing": missing},
            )
        owner = owners[unknown[0]]
        forward_params = dict(params.params)
        forward_params[DELEGATE_MARKER] = True
        try:
            from .client import GeoMCPClient, GeoMCPClientError

            with GeoMCPClient(owner, timeout=self.forward_timeout) as client:
                return client.execute(
                    skill=params.skill,
                    geocards=params.geocards,
                    spatial=(
                        params.spatial.model_dump(exclude_none=True) if params.spatial else None
                    ),
                    temporal=(
                        params.temporal.model_dump(exclude_none=True) if params.temporal else None
                    ),
                    params=forward_params,
                    request_id=params.request_id,
                )
        except GeoMCPClientError as exc:
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Delegation to {owner} failed: {exc}",
                {"owner": owner, "code": exc.code},
            ) from exc

    def _resolve_card(self, card_id: str) -> GeoCard | None:
        if self.geocard_registry is not None:
            return self.geocard_registry.get(card_id)
        return self._cards.get(card_id)

    def _run_tool(self, tool: _Tool, params: ExecuteParams) -> dict[str, Any]:
        try:
            result = tool.handler(params.params)
        except GeoMCPProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001 - tool boundary
            logger.exception("Tool '%s' failed", tool.name)
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Tool '{tool.name}' failed: {exc}",
            ) from exc
        if not isinstance(result, dict):
            raise GeoMCPProtocolError(
                EXECUTION_FAILED,
                f"Tool '{tool.name}' must return a dict, got {type(result).__name__}",
            )
        return {
            "status": "ok",
            "skill": params.skill,
            "outputs": result,
            "geocards": params.geocards,
            "executed_by": "tool",
            "request_id": params.request_id,
        }

    # ------------------------------------------------------------------ #
    # HTTP transport
    # ------------------------------------------------------------------ #
    def create_app(self) -> FastAPI:
        """Create a FastAPI application exposing the GeoMCP HTTP transport.

        Endpoints:
          - ``POST /geomcp``        — JSON-RPC dispatch
          - ``GET  /health``        — health check
          - ``GET  /capabilities``  — protocol surface
        """
        app = FastAPI(title=f"GeoMCP server: {self.name}", version=PROTOCOL_VERSION)

        @app.post("/geomcp")
        async def geomcp_endpoint(request: Request) -> JSONResponse:
            try:
                payload = await request.json()
            except Exception:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": "Parse error: body must be valid JSON",
                        },
                    },
                    status_code=400,
                )
            response = self._dispatcher.dispatch(payload)
            status = 200
            if response.get("error") and response["error"].get("code") in (
                -32700,
                -32600,
            ):
                status = 400
            return JSONResponse(response, status_code=status)

        @app.get("/health")
        async def health_endpoint() -> dict[str, Any]:
            return self.health()

        @app.get("/capabilities")
        async def capabilities_endpoint() -> dict[str, Any]:
            return self.capabilities()

        return app

    def run(self, host: str = "127.0.0.1", port: int = 8787, log_level: str = "info") -> None:
        """Run the FastAPI transport with uvicorn (blocking)."""
        import uvicorn

        uvicorn.run(self.create_app(), host=host, port=port, log_level=log_level)
