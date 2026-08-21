"""HTTP transport for the shared GeoCard Registry (FastAPI)."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .models import RegistryEntry, RegistrySearchResult, SkillDescriptor, SkillEntry
from .store import (
    RegistryEntryConflict,
    RegistryEntryNotFound,
    RegistryStore,
    SkillEntryConflict,
    SkillEntryNotFound,
)

logger = logging.getLogger(__name__)

SERVICE_VERSION = "1.0.0"


class RegistryServer:
    """The shared GeoCard Registry service.

    Endpoints:
      - ``POST   /cards``      register a card (body: ``{card, node_url}``)
      - ``GET    /cards``      list cards
      - ``GET    /cards/{id}`` get one card entry
      - ``DELETE /cards/{id}`` unregister a card
      - ``GET    /search``     search with contract pre-filtering
      - ``POST   /skills`` / ``GET /skills`` / ``GET /skills/{name}`` /
        ``DELETE /skills/{name}``  (V0.5 skill registry)
      - ``GET    /nodes``      node view: aggregated cards/skills per node
                               with optional health probing (V1.0+)
      - ``GET    /health``     health check

    Persistence (V1.0): pass ``persist_path`` to survive restarts.
    Auth (V1.0): when ``api_keys`` is non-empty, write endpoints require an
    ``X-API-Key`` header; read endpoints stay open for discovery.
    Health (V1.0+): when ``health_probe`` is enabled, ``/nodes`` lazily probes
    each node's ``/health`` endpoint (short timeout, brief cache) and marks
    ``healthy`` accordingly; discovery can exclude unhealthy nodes.
    """

    def __init__(
        self,
        name: str = "geonexus-registry",
        persist_path: str | os.PathLike | None = None,
        api_keys: set[str] | None = None,
        health_probe: bool = False,
        health_timeout: float = 2.0,
        health_cache_ttl: float = 5.0,
        peers: list[str] | None = None,
    ) -> None:
        self.name = name
        self.store = RegistryStore(persist_path=persist_path)
        self.api_keys = set(api_keys or ())
        self.health_probe = health_probe
        self.health_timeout = health_timeout
        self.health_cache_ttl = health_cache_ttl
        self._health_cache: dict[str, tuple[float, bool]] = {}
        self.peers = list(peers or ())

    def add_peer(self, peer_url: str) -> None:
        """Configure a peer registry for pull-based sync."""
        if peer_url not in self.peers:
            self.peers.append(peer_url)
            logger.info("Registry '%s' added peer %s", self.name, peer_url)

    def sync_peers(self, update_existing: bool = True) -> dict[str, Any]:
        """Pull cards/skills from all configured peers (idempotent)."""
        if not self.peers:
            return {"peers": 0, "message": "no peers configured"}
        from .federation import RegistryFederator

        federator = RegistryFederator(self.store, self.peers)
        report = federator.sync(update_existing=update_existing)
        report["registry"] = self.name
        return report

    def _require_key(self, request: Request) -> None:
        if not self.api_keys:
            return
        supplied = request.headers.get("X-API-Key")
        if supplied not in self.api_keys:
            raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")

    # ------------------------------------------------------------------ #
    # Business API
    # ------------------------------------------------------------------ #
    def register(self, card: dict[str, Any], node_url: str) -> dict[str, Any]:
        from ..geocard.model import GeoCard
        from ..geocard.validator import validate_card_schema

        report = validate_card_schema(card)
        if not report.valid:
            raise HTTPException(status_code=422, detail={"schema_errors": report.errors})
        try:
            entry = RegistryEntry(card=GeoCard.model_validate(card), node_url=node_url)
            self.store.register(entry)
        except RegistryEntryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "registered", "id": entry.card.id, "entry": entry.to_dict()}

    def unregister(self, card_id: str) -> dict[str, Any]:
        try:
            self.store.unregister(card_id)
        except RegistryEntryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "removed", "id": card_id}

    def get(self, card_id: str) -> dict[str, Any]:
        entry = self.store.get(card_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Card not registered: {card_id}")
        return entry.to_dict()

    def list(self) -> dict[str, Any]:
        entries = [e.to_dict() for e in self.store.list_entries()]
        return {"count": len(entries), "cards": entries}

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        def _opt(key: str) -> Any | None:
            value = params.get(key)
            return value if value not in (None, "") else None

        bbox = None
        raw_bbox = _opt("bbox")
        if raw_bbox is not None:
            if isinstance(raw_bbox, str):
                bbox = [float(x) for x in raw_bbox.split(",")]
            else:
                bbox = [float(x) for x in raw_bbox]
        required_bands = None
        raw_bands = _opt("required_bands")
        if raw_bands is not None:
            if isinstance(raw_bands, str):
                required_bands = [b for b in raw_bands.split(",") if b]
            else:
                required_bands = list(raw_bands)
        resolution = _opt("resolution")
        results: list[RegistrySearchResult] = self.store.search(
            capability=_opt("capability"),
            type=_opt("type"),
            tags=None,
            bbox=bbox,
            crs=_opt("crs"),
            start=_opt("start"),
            end=_opt("end"),
            required_bands=required_bands,
            required_resolution=float(resolution) if resolution is not None else None,
            contract_gate=True,
        )
        return {
            "count": len(results),
            "results": [r.to_dict() for r in results],
        }

    # ------------------------------------------------------------------ #
    # Skills (V0.5)
    # ------------------------------------------------------------------ #
    def register_skill(self, skill: dict[str, Any], node_url: str) -> dict[str, Any]:
        try:
            entry = SkillEntry(skill=SkillDescriptor(**skill), node_url=node_url)
            self.store.register_skill(entry)
        except SkillEntryConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - validation boundary
            raise HTTPException(status_code=422, detail=f"Invalid skill payload: {exc}") from exc
        return {"status": "registered", "name": entry.skill.name, "entry": entry.to_dict()}

    def unregister_skill(self, name: str) -> dict[str, Any]:
        try:
            self.store.unregister_skill(name)
        except SkillEntryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "removed", "name": name}

    def get_skill(self, name: str) -> dict[str, Any]:
        entry = self.store.get_skill(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Skill not registered: {name}")
        return entry.to_dict()

    def list_skills(self) -> dict[str, Any]:
        entries = [e.to_dict() for e in self.store.list_skills()]
        return {"count": len(entries), "skills": entries}

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "geonexus-registry",
            "name": self.name,
            "version": SERVICE_VERSION,
            "cards": self.store.count(),
            "time": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------ #
    # Nodes (V1.0+): aggregated view + optional health probing
    # ------------------------------------------------------------------ #
    def nodes(self) -> dict[str, Any]:
        """Aggregate registered cards/skills per node.

        When ``health_probe`` is enabled, each node's ``/health`` endpoint is
        probed (short timeout, cached for ``health_cache_ttl`` seconds) and
        ``healthy`` reflects the result; otherwise ``healthy`` is None
        (unknown).
        """
        cards = self.store.list_entries()
        skills = self.store.list_skills()
        by_node: dict[str, dict[str, Any]] = {}
        for card_entry in cards:
            info = by_node.setdefault(card_entry.node_url, {"cards": [], "skills": []})
            info["cards"].append(card_entry.card.id)
        for skill_entry in skills:
            info = by_node.setdefault(skill_entry.node_url, {"cards": [], "skills": []})
            info["skills"].append(skill_entry.skill.name)
        for node_url, info in by_node.items():
            info["cards"] = sorted(set(info["cards"]))
            info["skills"] = sorted(set(info["skills"]))
            info["healthy"] = self._probe_node(node_url) if self.health_probe else None
        return {"count": len(by_node), "nodes": by_node}

    def _probe_node(self, node_url: str) -> bool:
        """Probe a node's /health endpoint with a short timeout (cached)."""
        now = time.monotonic()
        cached = self._health_cache.get(node_url)
        if cached is not None and now - cached[0] < self.health_cache_ttl:
            return cached[1]
        healthy = False
        try:
            import httpx

            response = httpx.get(f"{node_url.rstrip('/')}/health", timeout=self.health_timeout)
            data = response.json() if response.status_code == 200 else {}
            healthy = response.status_code == 200 and data.get("status") == "ok"
        except Exception as exc:  # noqa: BLE001 - probe is best-effort
            logger.debug("Health probe failed for %s: %s", node_url, exc)
        self._health_cache[node_url] = (now, healthy)
        return healthy

    # ------------------------------------------------------------------ #
    # HTTP transport
    # ------------------------------------------------------------------ #
    def create_app(self) -> FastAPI:
        app = FastAPI(title=f"GeoCard Registry: {self.name}", version=SERVICE_VERSION)

        @app.post("/cards")
        async def register_card(request: Request) -> JSONResponse:
            self._require_key(request)
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Body must be valid JSON") from None
            if not isinstance(body, dict) or "card" not in body:
                raise HTTPException(status_code=422, detail="Body must contain 'card'")
            node_url = body.get("node_url") or "unknown"
            result = self.register(body["card"], node_url)
            return JSONResponse(result, status_code=201)

        @app.get("/cards")
        async def list_cards() -> dict[str, Any]:
            return self.list()

        @app.get("/cards/{card_id}")
        async def get_card(card_id: str) -> dict[str, Any]:
            return self.get(card_id)

        @app.delete("/cards/{card_id}")
        async def delete_card(card_id: str, request: Request) -> dict[str, Any]:
            self._require_key(request)
            return self.unregister(card_id)

        @app.get("/search")
        async def search_cards(
            capability: str | None = None,
            type: str | None = None,
            bbox: str | None = None,
            crs: str | None = None,
            start: str | None = None,
            end: str | None = None,
            required_bands: str | None = None,
            resolution: float | None = None,
        ) -> dict[str, Any]:
            return self.search(
                {
                    "capability": capability,
                    "type": type,
                    "bbox": bbox,
                    "crs": crs,
                    "start": start,
                    "end": end,
                    "required_bands": required_bands,
                    "resolution": resolution,
                }
            )

        @app.post("/skills")
        async def register_skill_endpoint(request: Request) -> JSONResponse:
            self._require_key(request)
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Body must be valid JSON") from None
            if not isinstance(body, dict) or "skill" not in body:
                raise HTTPException(status_code=422, detail="Body must contain 'skill'")
            node_url = body.get("node_url") or "unknown"
            result = self.register_skill(body["skill"], node_url)
            return JSONResponse(result, status_code=201)

        @app.get("/skills")
        async def list_skills_endpoint() -> dict[str, Any]:
            return self.list_skills()

        @app.get("/skills/{name}")
        async def get_skill_endpoint(name: str) -> dict[str, Any]:
            return self.get_skill(name)

        @app.delete("/skills/{name}")
        async def delete_skill_endpoint(name: str, request: Request) -> dict[str, Any]:
            self._require_key(request)
            return self.unregister_skill(name)

        @app.get("/health")
        async def health() -> dict[str, Any]:
            return self.health()

        @app.get("/nodes")
        async def nodes_endpoint() -> dict[str, Any]:
            return self.nodes()

        @app.post("/sync")
        async def sync_endpoint(request: Request) -> dict[str, Any]:
            self._require_key(request)
            return self.sync_peers()

        return app

    def run(self, host: str = "127.0.0.1", port: int = 8790, log_level: str = "info") -> None:
        import uvicorn

        uvicorn.run(self.create_app(), host=host, port=port, log_level=log_level)
