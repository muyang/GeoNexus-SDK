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


PLUGIN_ENTRY_POINT_GROUP = "geonexus.plugins"


def discover_plugin_entry_points() -> list[Any]:
    """pip entry-points 发现（py3.9 与 3.10+ 的 API 形状不同，都兼容）。"""
    import importlib.metadata as im

    eps = im.entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group=PLUGIN_ENTRY_POINT_GROUP))
    # Python < 3.10：entry_points() 返回 dict 形状，按组名取
    legacy: dict[str, Any] = eps  # type: ignore[assignment]
    return list(legacy.get(PLUGIN_ENTRY_POINT_GROUP, []))


def load_plugins(node: GeoNode, entry_points: list[Any] | None = None) -> list[dict[str, Any]]:
    """加载第三方插件，**逐个隔离**：坏插件只坏自己，不拖垮节点，也不静默消失。

    插件对象两种形态都收：
      · 带 ``register(node)`` 的可调用对象（完全自由）；
      · 带 ``cards()`` / ``skills()`` 的声明式对象（推荐，加载器代注册）。
    状态进 ``node.plugin_status`` 与 health 面，运维一眼能看到谁没装上。
    """
    status: list[dict[str, Any]] = []
    for ep in entry_points if entry_points is not None else discover_plugin_entry_points():
        name = getattr(ep, "name", "unknown")
        version = ""
        try:
            obj = ep.load()
            # 版本以发行版元数据为准（pip 报告的就是它）；py3.9 的 EntryPoint
            # 没有 .dist，拿不到时退回插件对象自报的 version
            dist = getattr(ep, "dist", None)
            version = str(getattr(dist, "version", "") or getattr(obj, "version", "") or "")
            register = getattr(obj, "register", None)
            if callable(register):
                register(node)
            else:
                for card in (getattr(obj, "cards", None) or (lambda: []))():
                    node.register_geocard(card)
                for skill in (getattr(obj, "skills", None) or (lambda: []))():
                    node.register_skill_object(skill)
            status.append({"name": name, "version": version, "ok": True})
        except Exception as exc:  # noqa: BLE001 - 第三方代码质量不是节点的责任
            status.append({"name": name, "version": version, "ok": False, "error": str(exc)})
    return status


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
        recipe_registry: Any | None = None,
        gaag_registry: Any | None = None,
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
        #: 配方目录（V1.1）。绑定后 ``geo.plan`` 才能物化方案；未绑定时该方法
        #: 明确返回 ``recipe_not_found`` 并说明原因，而不是给出一张空图。
        self.recipe_registry = recipe_registry
        self.server = GeoMCPServer(
            name=name,
            engine=self.runtime,
            geocard_registry=self.geocard_registry,
            skill_registry=self.skill_registry,
            registry_url=registry_url,
            recipe_registry=recipe_registry,
            artifact_roots=[self.workdir],
            gaag_registry=gaag_registry,
        )
        # 插件（pip entry-points）是第三方能力的正门：启动即发现，坏了只坏自己
        self.plugin_status = load_plugins(self)
        self.server.plugin_status = self.plugin_status

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
        api_key: str | None = None,
    ) -> GeoNode:
        """Register all of this node's GeoCards and GeoSkills at a registry.

        Args:
            registry_url: Base URL of the GeoCard Registry service.
            endpoint: The URL clients should use to reach this node.
                Defaults to ``http://{host}:{port}``; pass the actual port
                explicitly when the node runs on an ephemeral port.
            api_key: ``X-API-Key`` for a registry started with ``--api-key``.
                Registering is a write, and the registry enforces the key on
                writes — without this, advertising against an authenticated
                registry fails with 401. Note this must be the *registry's*
                key, which is not necessarily the node's own key.

        Cards and skills are descriptions only — the registry never receives
        data or code.
        """
        from ..registry import RegistryClient, RegistryClientError

        endpoint = endpoint or f"http://{self.host}:{self.port}"
        with RegistryClient(registry_url, api_key=api_key) as registry:
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
