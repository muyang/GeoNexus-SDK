"""Local GeoNode.

GeoNode is a sovereign cloud-native geospatial capability node. The Local
GeoNode runs on your machine and bundles:

- a :class:`GeoCardRegistry` (discovery layer)
- a :class:`SkillRegistry` (capability layer)
- a :class:`LocalRuntime` (execution boundary)
- a :class:`GeoMCPServer` (interaction protocol)

The architecture keeps GeoMCP and GeoCard transport/contract agnostic so that
GeoNode Federation can be added later without rewriting them.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from ..geocard.model import GeoCard
from ..geomcp.models import DescribeParams, ExecuteParams
from ..geomcp.server import GeoMCPServer
from .registry import GeoCardRegistry, SkillRegistry
from .runtime import LocalRuntime
from .skill import Skill

logger = logging.getLogger(__name__)


class RunningServer:
    """Handle for a GeoNode started in a background thread."""

    def __init__(self, uvicorn_server: Any, thread: threading.Thread) -> None:
        self._server = uvicorn_server
        self._thread = thread

    def wait_until_ready(self, timeout: float = 15.0) -> RunningServer:
        """Block until the HTTP server is accepting connections."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            if not self._thread.is_alive():
                raise RuntimeError("GeoNode server thread exited before becoming ready")
            time.sleep(0.05)
        raise TimeoutError(f"GeoNode server did not become ready within {timeout}s")

    @property
    def port(self) -> int:
        """The actual listening port (useful when port 0 / ephemeral was used)."""
        try:
            return self._server.servers[0].sockets[0].getsockname()[1]
        except (IndexError, AttributeError):
            raise RuntimeError("GeoNode server socket not available") from None

    def stop(self, timeout: float = 10.0) -> None:
        """Ask the server to shut down and join its thread."""
        self._server.should_exit = True
        self._thread.join(timeout=timeout)


class GeoNode:
    """A Local GeoNode exposing GeoCard registry, Skill registry and a
    GeoMCP server over HTTP."""

    def __init__(
        self,
        name: str = "local-node",
        host: str = "127.0.0.1",
        port: int = 8787,
        workdir: str | Path | None = None,
        log_level: str = "info",
        registry_url: str | None = None,
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.log_level = log_level
        self.workdir = str(workdir) if workdir else str(Path.cwd())

        self.geocard_registry = GeoCardRegistry()
        self.skill_registry = SkillRegistry()
        self.runtime = LocalRuntime(
            self.skill_registry,
            workdir=self.workdir,
            geocard_registry=self.geocard_registry,
            node_name=self.name,
        )
        self.server = GeoMCPServer(
            name=name,
            engine=self.runtime,
            geocard_registry=self.geocard_registry,
            skill_registry=self.skill_registry,
            registry_url=registry_url,
        )

    # ------------------------------------------------------------------ #
    # Federation (V1.0+): bind a registry to enable node-level delegation
    # ------------------------------------------------------------------ #
    def set_registry(self, registry_url: str | None) -> GeoNode:
        """Bind this node to a shared GeoCard Registry.

        When bound, ``geo.execute`` requests that reference GeoCards this
        node does not own are delegated to the owning node (node-level
        pushdown: any node can be a federated entry point).
        """
        self.server.set_registry(registry_url)
        return self

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register_geocard(self, card: GeoCard) -> GeoNode:
        """Register a GeoCard with this node."""
        self.geocard_registry.register(card)
        return self

    def register_geocards(self, cards: list[GeoCard]) -> GeoNode:
        """Register several GeoCards."""
        for card in cards:
            self.register_geocard(card)
        return self

    def register_skill(
        self,
        name: str,
        handler: Any,
        description: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        geocard: GeoCard | None = None,
    ) -> GeoNode:
        """Register a skill with this node (``name`` + ``handler`` form)."""
        skill = Skill(
            name=name,
            description=description or "",
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            handler=handler,
            geocard=geocard,
        )
        return self.register_skill_object(skill)

    def register_skill_object(self, skill: Skill) -> GeoNode:
        """Register a pre-built :class:`Skill` object."""
        self.skill_registry.register(skill)
        return self

    def register_skill_objects(self, skills: list[Skill]) -> GeoNode:
        """Register many pre-built skills (e.g. imported MCP tools)."""
        for skill in skills:
            self.skill_registry.register(skill)
        return self

    # ------------------------------------------------------------------ #
    # Federation (V0.3+): advertise this node's cards and skills to a
    # shared registry
    # ------------------------------------------------------------------ #
    def advertise(
        self,
        registry_url: str,
        endpoint: str | None = None,
    ) -> GeoNode:
        """Register all of this node's GeoCards and GeoSkills at a registry.

        Args:
            registry_url: Base URL of the GeoCard Registry service.
            endpoint: The URL clients should use to reach this node.
                Defaults to ``http://{host}:{port}``; pass the actual port
                explicitly when the node runs on an ephemeral port.

        Cards and skills are descriptions only — the registry never receives
        data or code.
        """
        from ..registry import RegistryClient, RegistryClientError

        endpoint = endpoint or f"http://{self.host}:{self.port}"
        with RegistryClient(registry_url) as registry:
            for card in self.geocard_registry.list_cards():
                try:
                    registry.register(card, node_url=endpoint)
                except RegistryClientError as exc:
                    if exc.code == 409:
                        logger.info("Card '%s' already advertised at %s", card.id, registry_url)
                        continue
                    raise
            for skill in self.skill_registry.list_skills():
                capabilities = skill.geocard.capability_names() if skill.geocard is not None else []
                try:
                    registry.register_skill(
                        name=skill.name,
                        node_url=endpoint,
                        description=skill.description,
                        input_schema=skill.input_schema,
                        output_schema=skill.output_schema,
                        capabilities=capabilities,
                    )
                except RegistryClientError as exc:
                    if exc.code == 409:
                        logger.info("Skill '%s' already advertised at %s", skill.name, registry_url)
                        continue
                    raise
        logger.info(
            "Advertised %d card(s) and %d skill(s) from '%s' to registry %s (endpoint %s)",
            self.geocard_registry.count(),
            self.skill_registry.count(),
            self.name,
            registry_url,
            endpoint,
        )
        return self

    # ------------------------------------------------------------------ #
    # GeoMCP surface
    # ------------------------------------------------------------------ #
    def capabilities(self) -> dict[str, Any]:
        """Advertise the node's GeoMCP surface."""
        return self.server.capabilities()

    def describe(self, params: DescribeParams | None = None) -> dict[str, Any]:
        """Describe registered GeoCards and skills."""
        return self.server.describe(params)

    def health(self) -> dict[str, Any]:
        """Health check payload."""
        return self.server.health()

    def execute(self, params: ExecuteParams | dict[str, Any]) -> dict[str, Any]:
        """Execute a request (dict or :class:`ExecuteParams`) on this node."""
        if isinstance(params, dict):
            params = ExecuteParams(**params)
        return self.server.execute(params)

    # ------------------------------------------------------------------ #
    # Serving
    # ------------------------------------------------------------------ #
    def create_app(self):
        """Build the FastAPI application for this node."""
        return self.server.create_app()

    def run(self) -> None:
        """Run the node's GeoMCP server (blocking)."""
        logger.info("Starting GeoNode '%s' on %s:%s", self.name, self.host, self.port)
        self.server.run(host=self.host, port=self.port, log_level=self.log_level)

    def start_in_thread(self, host: str | None = None, port: int | None = None) -> RunningServer:
        """Start the node's GeoMCP server in a background thread.

        Returns a :class:`RunningServer` handle; call
        ``handle.wait_until_ready()`` before issuing requests.
        """
        import uvicorn

        host = host or self.host
        port = port if port is not None else self.port
        config = uvicorn.Config(
            self.create_app(),
            host=host,
            port=port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name=f"geonode-{self.name}", daemon=True)
        thread.start()
        return RunningServer(server, thread)
