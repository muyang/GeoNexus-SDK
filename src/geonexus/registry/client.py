"""HTTP client for the shared GeoCard Registry."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..geocard.model import GeoCard

logger = logging.getLogger(__name__)


class RegistryClientError(Exception):
    """Raised for transport failures and registry error responses."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" if self.code else self.message


class RegistryClient:
    """Synchronous HTTP client for a :class:`RegistryServer`.

    Args:
        base_url: Registry root URL.
        timeout: Request timeout in seconds.
        api_key: Optional ``X-API-Key`` sent on every request (V1.0 auth;
            the server only enforces it on write endpoints).
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
    def register(
        self,
        card: GeoCard,
        node_url: str,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Register a card owned by ``node_url``.

        ``status`` (v1.1): omit for the default ``approved`` (immediately
        discoverable), or pass ``"pending"`` to submit for review.
        """
        body: dict[str, Any] = {"card": card.to_dict(), "node_url": node_url}
        if status is not None:
            body["status"] = status
        return self._call("POST", "/cards", json=body)

    def unregister(self, card_id: str) -> dict[str, Any]:
        return self._call("DELETE", f"/cards/{card_id}")

    def get(self, card_id: str) -> dict[str, Any]:
        return self._call("GET", f"/cards/{card_id}")

    def list_cards(self, status: str | None = None) -> list[dict[str, Any]]:
        """List cards.

        ``status=None`` → approved only (server default); pass ``"pending"``
        / ``"rejected"`` for a specific review state, or ``"all"`` for every
        state (admin view).
        """
        params = {"status": status} if status is not None else None
        data = self._call("GET", "/cards", params=params)
        return data.get("cards", [])

    def approve(
        self, card_id: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        """Approve a pending card (makes it discoverable)."""
        body: dict[str, Any] = {}
        if note:
            body["note"] = note
        if by:
            body["by"] = by
        return self._call("POST", f"/cards/{card_id}/approve", json=body)

    def reject(
        self, card_id: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        """Reject a pending card (removes it from discovery)."""
        body: dict[str, Any] = {}
        if note:
            body["note"] = note
        if by:
            body["by"] = by
        return self._call("POST", f"/cards/{card_id}/reject", json=body)

    # ------------------------------------------------------------------ #
    # Recipes (V1.1)
    # ------------------------------------------------------------------ #
    def register_recipe(
        self,
        recipe: dict[str, Any],
        node_url: str = "platform",
        status: str | None = None,
        review_note: str | None = None,
        submitted_by: str | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Submit a recipe. Defaults to ``pending`` on the server side.

        Recipes are reviewed like cards, but they are more consequential: a
        recipe is what a reuser actually *runs*, so publishing one publishes a
        method, not just a description.
        """
        body: dict[str, Any] = {
            "recipe": recipe,
            "node_url": node_url,
            "replace": replace,
        }
        if status:
            body["status"] = status
        if review_note:
            body["review_note"] = review_note
        if submitted_by:
            body["submitted_by"] = submitted_by
        return self._call("POST", "/recipes", json=body)

    def list_recipes(
        self,
        status: str | None = None,
        *,
        query: str | None = None,
        namespace: str | None = None,
        tag: str | None = None,
        param: str | None = None,
        role: str | None = None,
        approved_only: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if query:
            params["q"] = query
        if namespace:
            params["namespace"] = namespace
        if tag:
            params["tag"] = tag
        if param:
            params["param"] = param
        if role:
            params["role"] = role
        if approved_only:
            params["approved_only"] = "true"
        data = self._call("GET", "/recipes", params=params)
        return data.get("recipes", [])

    def get_recipe(self, address: str, approved_only: bool = False) -> dict[str, Any]:
        """Fetch one recipe entry (address may omit the version)."""
        params = {"approved_only": "true"} if approved_only else None
        return self._call("GET", f"/recipes/{address}", params=params)

    def unregister_recipe(self, address: str) -> dict[str, Any]:
        return self._call("DELETE", f"/recipes/{address}")

    def approve_recipe(
        self, address: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        return self._call("POST", f"/recipes/{address}/approve", json={"note": note, "by": by})

    def reject_recipe(
        self, address: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        return self._call("POST", f"/recipes/{address}/reject", json={"note": note, "by": by})

    def fork_recipe(
        self,
        recipe_id: str,
        *,
        namespace: str | None = None,
        name: str | None = None,
        version: str | None = None,
        params: dict[str, Any] | None = None,
        fixed: dict[str, Any] | None = None,
        title: str | None = None,
        provider_url: str = "",
        register: bool = True,
    ) -> dict[str, Any]:
        """Fork a recipe into a new address; the source is not modified."""
        body: dict[str, Any] = {
            "recipe_id": recipe_id,
            "provider_url": provider_url,
            "register": register,
        }
        for key, value in (
            ("namespace", namespace),
            ("name", name),
            ("version", version),
            ("params", params),
            ("fixed", fixed),
            ("title", title),
        ):
            if value is not None:
                body[key] = value
        return self._call("POST", "/recipes/fork", json=body)

    # ------------------------------------------------------------------ #
    # Skills (V0.5)
    # ------------------------------------------------------------------ #
    def register_skill(
        self,
        name: str,
        node_url: str,
        description: str = "",
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a skill offered by ``node_url``."""
        skill = {
            "name": name,
            "description": description,
            "input_schema": input_schema or {},
            "output_schema": output_schema or {},
            "capabilities": capabilities or [],
        }
        return self._call("POST", "/skills", json={"skill": skill, "node_url": node_url})

    def unregister_skill(self, name: str) -> dict[str, Any]:
        return self._call("DELETE", f"/skills/{name}")

    def get_skill(self, name: str) -> dict[str, Any]:
        """Return the skill entry (includes the offering ``node_url``)."""
        return self._call("GET", f"/skills/{name}")

    def list_skills(self) -> list[dict[str, Any]]:
        data = self._call("GET", "/skills")
        return data.get("skills", [])

    def search_skills(
        self,
        capability: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter registered skills by capability and/or exact name."""
        entries = self.list_skills()
        if capability is not None:
            entries = [e for e in entries if capability in e["skill"]["capabilities"]]
        if name is not None:
            entries = [e for e in entries if e["skill"]["name"] == name]
        return entries

    # ------------------------------------------------------------------ #
    # 公共产品市场（插件包条目）
    # ------------------------------------------------------------------ #
    def publish_plugin(self, plugin: dict[str, Any]) -> dict[str, Any]:
        """Publish a plugin entry (lands as pending; approve to list it)."""
        return self._call("POST", "/plugins", json={"plugin": plugin})

    def list_plugins(self, status: str | None = None, q: str = "") -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if q:
            params["q"] = q
        return self._call("GET", "/plugins", params=params).get("plugins", [])

    def get_plugin(self, name: str, version: str) -> dict[str, Any]:
        return self._call("GET", f"/plugins/{name}/{version}").get("entry", {})

    def approve_plugin(
        self, name: str, version: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        return self._call(
            "POST", f"/plugins/{name}/{version}/approve", json={"note": note, "by": by}
        )

    def reject_plugin(
        self, name: str, version: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        return self._call(
            "POST", f"/plugins/{name}/{version}/reject", json={"note": note, "by": by}
        )

    def remove_plugin(self, name: str, version: str) -> dict[str, Any]:
        return self._call("DELETE", f"/plugins/{name}/{version}")

    def get_nodes(self) -> dict[str, Any]:
        """Fetch the node view: per-node cards/skills (+ health when enabled)."""
        return self._call("GET", "/nodes")

    def search(
        self,
        capability: str | None = None,
        type: str | None = None,
        bbox: list[float] | None = None,
        crs: str | None = None,
        start: str | None = None,
        end: str | None = None,
        required_bands: list[str] | None = None,
        required_resolution: float | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search the registry with contract pre-filtering.

        ``status`` filters by review state (v1.1): ``"pending"`` for the
        review queue, ``"approved"`` (default server-side) for discovery.
        """
        params: dict[str, Any] = {}
        if capability is not None:
            params["capability"] = capability
        if type is not None:
            params["type"] = type
        if bbox is not None:
            params["bbox"] = ",".join(str(x) for x in bbox)
        if crs is not None:
            params["crs"] = crs
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end
        if required_bands is not None:
            params["required_bands"] = ",".join(required_bands)
        if required_resolution is not None:
            params["resolution"] = required_resolution
        if status is not None:
            params["status"] = status
        data = self._call("GET", "/search", params=params)
        return data.get("results", [])

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health")

    # ------------------------------------------------------------------ #
    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            headers = dict(kwargs.pop("headers", {}) or {})
            if self.api_key:
                headers["X-API-Key"] = self.api_key
            response = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise RegistryClientError(
                f"Registry request timed out after {self.timeout}s ({method} {path})"
            ) from exc
        except httpx.HTTPError as exc:
            raise RegistryClientError(f"Registry transport error ({method} {path}): {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise RegistryClientError(
                f"Registry response is not JSON (status={response.status_code})"
            ) from exc
        if response.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise RegistryClientError(
                f"Registry error {response.status_code}: {detail or data}",
                code=response.status_code,
            )
        if not isinstance(data, dict):
            raise RegistryClientError("Registry response must be an object")
        return data

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RegistryClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
