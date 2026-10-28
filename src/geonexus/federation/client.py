"""Federated GeoMCP client: discovery via registry, execution via CAFE pushdown.

## 执行部分已经交给 CAFE

这个类原来自己维护一整套"到节点的 HTTP 传输 + 执行"（``_node_client``、
``GeoMCPClient`` 缓存、逐节点调用）。那是与 CAFE **重复的下推实现**，而 CAFE 那一份
没人用。现在：

* **传输与执行**：``geonexus.cafe.RemoteWorker``（HTTP/GeoMCP）+ ``CentralServer``
  的租约与失败换点；
* **保留在这里的**：注册中心发现、以及"每张卡归哪个节点"的**归属分组**——那是本类
  的对外语义（``execute`` 要按节点聚合结果），属于策略而非传输。

公开 API 与返回形状一字未改（``resolve_nodes`` / ``discover`` / ``node_health`` /
``discover_skills`` / ``search_skills`` / ``search`` / ``execute_skill`` / ``execute``）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..cafe import CentralServer, PushdownRequest, RemoteWorker, TaskStatus
from ..registry import RegistryClient, RegistryClientError

logger = logging.getLogger(__name__)


def _pushdown_request(*, request_id: Any | None = None, **payload: Any) -> PushdownRequest:
    """构造下推请求；没给 ``request_id`` 时交给 dataclass 自己的默认工厂。

    早前直接读 ``__dataclass_fields__[...].default_factory()``：那是私有 API，
    默认工厂一旦改成 ``dataclasses.MISSING`` 或在无缺省字段上取值就会运行期 TypeError。
    """
    if request_id:
        payload["task_id"] = request_id
    return PushdownRequest(**payload)


class FederatedExecutionError(Exception):
    """Raised when a federated request cannot be satisfied."""

    def __init__(self, message: str, details: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class FederatedGeoMCPClient:
    """Execute GeoMCP requests across the network.

    Args:
        registry_url: Base URL of the shared GeoCard Registry.
        timeout: Request timeout in seconds (registry and node calls).
        api_key: Optional ``X-API-Key`` forwarded on every node call. Use
            when nodes enforce authentication (server-side / BFF usage).
    """

    def __init__(
        self,
        registry_url: str,
        timeout: float = 30.0,
        api_key: str | None = None,
    ) -> None:
        self.registry_url = registry_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        self._registry = RegistryClient(self.registry_url, timeout=timeout)
        # 下推走 CAFE：一个 worker 一个远端节点，缓存复用（含连接池）
        self._central = CentralServer(server_id=f"federation:{self.registry_url}")
        self._workers: dict[str, RemoteWorker] = {}
        self._workers_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Discovery
    # ------------------------------------------------------------------ #
    def resolve_nodes(self, geocards: list[str]) -> dict[str, str]:
        """Map each card id to the URL of the node that owns it."""
        resolved: dict[str, str] = {}
        for card_id in geocards:
            try:
                entry = self._registry.get(card_id)
            except RegistryClientError as exc:
                raise FederatedExecutionError(
                    f"Registry lookup failed for '{card_id}': {exc}",
                    {"card": card_id},
                ) from exc
            if not isinstance(entry, dict) or "node_url" not in entry:
                raise FederatedExecutionError(
                    f"Card '{card_id}' is not registered at {self.registry_url}",
                    {"card": card_id},
                )
            resolved[card_id] = entry["node_url"]
        return resolved

    def discover(self) -> dict[str, Any]:
        """Return the registry view grouped by owning node, with health.

        ``nodes`` maps node_url -> card id list (unchanged shape); ``health``
        maps node_url -> True/False/None when the registry probes health.
        """
        try:
            cards = self._registry.list_cards()
        except RegistryClientError as exc:
            raise FederatedExecutionError(f"Registry unavailable: {exc}") from exc
        by_node: dict[str, list[str]] = {}
        for entry in cards:
            by_node.setdefault(entry["node_url"], []).append(entry["card"]["id"])
        health: dict[str, bool | None] = {}
        try:
            node_view = self._registry.get_nodes()
            for node_url, info in node_view.get("nodes", {}).items():
                health[node_url] = info.get("healthy")
        except RegistryClientError:
            pass  # health is best-effort
        for node_url in by_node:
            by_node[node_url] = sorted(set(by_node[node_url]))
        return {"registry": self.registry_url, "nodes": by_node, "health": health}

    def node_health(self) -> dict[str, Any]:
        """Return the registry's node view (cards/skills/health per node)."""
        try:
            return self._registry.get_nodes()
        except RegistryClientError as exc:
            raise FederatedExecutionError(f"Registry unavailable: {exc}") from exc

    def discover_skills(self) -> dict[str, Any]:
        """Return the registry's skill view grouped by offering node."""
        try:
            skills = self._registry.list_skills()
        except RegistryClientError as exc:
            raise FederatedExecutionError(f"Registry unavailable: {exc}") from exc
        by_node: dict[str, list[str]] = {}
        for entry in skills:
            by_node.setdefault(entry["node_url"], []).append(entry["skill"]["name"])
        return {"registry": self.registry_url, "skills_by_node": by_node}

    def search_skills(
        self, capability: str | None = None, name: str | None = None
    ) -> list[dict[str, Any]]:
        """Find registered skills by capability and/or exact name."""
        try:
            return self._registry.search_skills(capability=capability, name=name)
        except RegistryClientError as exc:
            raise FederatedExecutionError(f"Registry skill search failed: {exc}") from exc

    def search(
        self,
        capability: str | None = None,
        bbox: list[float] | None = None,
        crs: str | None = None,
        start: str | None = None,
        end: str | None = None,
        required_bands: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Contract-gated discovery via the registry's search endpoint."""
        try:
            return self._registry.search(
                capability=capability,
                bbox=bbox,
                crs=crs,
                start=start,
                end=end,
                required_bands=required_bands,
            )
        except RegistryClientError as exc:
            raise FederatedExecutionError(f"Registry search failed: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Pushdown execution
    # ------------------------------------------------------------------ #
    def execute_skill(
        self,
        skill: str,
        params: dict[str, Any] | None = None,
        spatial: dict[str, Any] | None = None,
        temporal: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Skill-first routing: resolve the skill's offering node and push
        the execution there (no card lookup required)."""
        try:
            entry = self._registry.get_skill(skill)
        except RegistryClientError as exc:
            raise FederatedExecutionError(
                f"Registry skill lookup failed for '{skill}': {exc}", {"skill": skill}
            ) from exc
        if not isinstance(entry, dict) or "node_url" not in entry:
            raise FederatedExecutionError(
                f"Skill '{skill}' is not registered at {self.registry_url}",
                {"skill": skill},
            )
        node_url = entry["node_url"]
        result = self._dispatch(
            node_url,
            _pushdown_request(
                skill=skill,
                params=params,
                spatial=spatial,
                temporal=temporal,
                # request_id 落到任务的 id 上：远端用它关联自己的事件流
                request_id=request_id,
            ),
        )
        return result

    def execute(
        self,
        skill: str,
        geocards: list[str] | None = None,
        spatial: dict[str, Any] | None = None,
        temporal: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        skip_unhealthy: bool = True,
    ) -> dict[str, Any]:
        """Execute a skill, routing each card to its owning node.

        When ``skip_unhealthy`` (default) and the registry probes health,
        nodes marked unhealthy are excluded from the fan-out; if that leaves
        no node for a card, a :class:`FederatedExecutionError` is raised.

        Returns a single result when one node owns all cards; otherwise a
        ``{"results": [...]}`` aggregation keyed per node.
        """
        geocards = geocards or []
        if not geocards:
            raise FederatedExecutionError("At least one geocard id is required")

        card_to_node = self.resolve_nodes(geocards)
        nodes: dict[str, list[str]] = {}
        for card_id, node_url in card_to_node.items():
            nodes.setdefault(node_url, []).append(card_id)

        if skip_unhealthy:
            nodes = self._filter_healthy(nodes)

        results: list[dict[str, Any]] = []
        for node_url, node_cards in nodes.items():
            result = self._dispatch(
                node_url,
                _pushdown_request(
                    skill=skill,
                    params=params,
                    geocards=list(node_cards),
                    spatial=spatial,
                    temporal=temporal,
                    request_id=request_id,
                ),
            )
            results.append({"node": node_url, "cards": node_cards, "result": result})

        if len(results) == 1:
            return results[0]["result"]
        return {"status": "ok", "results": results}

    def _dispatch(self, node_url: str, request: PushdownRequest) -> dict[str, Any]:
        """经 CAFE 把请求下推到指定节点，并按本类的错误契约抛异常。

        ``RemoteWorker`` 失败时返回结果而不抛（路由层要靠它换点），但 federated
        API 的对外契约是**抛** ``FederatedExecutionError``（调用方按异常处理，
        例如 agent 的计划步骤靠它判定失败），所以这里做一次转换。
        """
        # 先确保这个节点的 worker 已建好并注册进 central——dispatch_to 只认已注册的
        # worker（第一版漏了这一步，症状是 "worker not found: <node>"）
        self._worker(node_url)
        result = self._central.dispatch_to(node_url, request)
        if result.status is TaskStatus.SUCCEEDED:
            return result.result
        raise FederatedExecutionError(
            f"Node {node_url} refused execution: {result.error}",
            {
                "node": node_url,
                "skill": request.skill,
                "cards": list(request.geocards),
                "code": result.error_code,
            },
        )

    # ------------------------------------------------------------------ #
    def _filter_healthy(self, nodes: dict[str, list[str]]) -> dict[str, list[str]]:
        """Drop nodes the registry marks unhealthy (best-effort)."""
        try:
            view = self._registry.get_nodes()
        except RegistryClientError:
            return nodes
        healthy_map = {url: info.get("healthy") for url, info in view.get("nodes", {}).items()}
        filtered: dict[str, list[str]] = {}
        for node_url, node_cards in nodes.items():
            healthy = healthy_map.get(node_url)
            if healthy is None or healthy is True:
                filtered[node_url] = node_cards
            else:
                logger.warning("Skipping unhealthy node %s (cards %s)", node_url, node_cards)
        if not filtered:
            raise FederatedExecutionError(
                "All owning nodes are marked unhealthy at the registry",
                {"nodes": list(nodes)},
            )
        return filtered

    # ------------------------------------------------------------------ #
    def _worker(self, node_url: str) -> RemoteWorker:
        """取（必要时创建）指向某个节点的 CAFE worker。

        **加锁是因为并行执行**：计划执行器会同时下推多个步骤，两个线程同时首次访问
        同一节点时"先查后建"会各建一个 worker 并泄漏其中一个（每个持有 httpx 连接池）。
        锁只保护这个缓存，不串行化请求本身——``httpx.Client`` 可并发使用。
        """
        with self._workers_lock:
            worker = self._workers.get(node_url)
            if worker is None:
                worker = RemoteWorker(node_url, timeout=self.timeout, api_key=self.api_key)
                self._workers[node_url] = worker
                self._central.register_worker(worker)
            return worker

    def close(self) -> None:
        self._registry.close()
        self._central.close()  # 关闭所有远端 worker 的连接池

    def __enter__(self) -> FederatedGeoMCPClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
