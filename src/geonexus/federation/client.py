"""Federated GeoMCP client: discovery via registry, execution via pushdown."""

from __future__ import annotations

import logging
from typing import Any

from ..geomcp import GeoMCPClient, GeoMCPClientError
from ..registry import RegistryClient, RegistryClientError

logger = logging.getLogger(__name__)


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
        self._nodes: dict[str, GeoMCPClient] = {}

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
        client = self._node_client(node_url)
        try:
            return client.execute(
                skill=skill,
                params=params,
                spatial=spatial,
                temporal=temporal,
                request_id=request_id,
            )
        except GeoMCPClientError as exc:
            raise FederatedExecutionError(
                f"Node {node_url} refused skill execution: {exc}",
                {"node": node_url, "skill": skill, "code": exc.code},
            ) from exc

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
            client = self._node_client(node_url)
            try:
                result = client.execute(
                    skill=skill,
                    geocards=node_cards,
                    spatial=spatial,
                    temporal=temporal,
                    params=params,
                    request_id=request_id,
                )
            except GeoMCPClientError as exc:
                raise FederatedExecutionError(
                    f"Node {node_url} refused execution: {exc}",
                    {"node": node_url, "cards": node_cards, "code": exc.code},
                ) from exc
            results.append({"node": node_url, "cards": node_cards, "result": result})

        if len(results) == 1:
            return results[0]["result"]
        return {"status": "ok", "results": results}

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
    def _node_client(self, node_url: str) -> GeoMCPClient:
        if node_url not in self._nodes:
            self._nodes[node_url] = GeoMCPClient(
                node_url, timeout=self.timeout, api_key=self.api_key
            )
        return self._nodes[node_url]

    def close(self) -> None:
        self._registry.close()
        for client in self._nodes.values():
            client.close()

    def __enter__(self) -> FederatedGeoMCPClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
