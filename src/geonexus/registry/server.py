"""HTTP transport for the shared GeoCard Registry (FastAPI)."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..recipe import RecipeRegistry
from .models import (
    REVIEW_STATUSES,
    STATUS_APPROVED,
    STATUS_PENDING,
    RegistryEntry,
    RegistrySearchResult,
    SkillDescriptor,
    SkillEntry,
)
from .store import (
    PluginEntryConflict,
    PluginEntryNotFound,
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
      - ``POST   /recipes`` / ``GET /recipes`` / ``GET /recipes/{address:path}`` /
        ``DELETE /recipes/{address:path}``  (V1.1 recipe catalogue)
      - ``POST   /recipes/{address:path}/approve`` / ``reject`` (V1.1 review)
      - ``POST   /recipes/fork``  fork a recipe (new address, source untouched)
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
        recipe_persist_path: str | os.PathLike | None = None,
        public_submissions: bool = False,
        api_key_principals: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.name = name
        self.store = RegistryStore(persist_path=persist_path)
        # 配方与卡片分文件存：卡片目录可能已经很重，且两者的审核节奏不同。
        # 未指定时按卡片文件派生（registry.json → registry.json.recipes.json）。
        if recipe_persist_path is None and persist_path:
            recipe_persist_path = f"{persist_path}.recipes.json"
        self.recipes = RecipeRegistry(persist_path=recipe_persist_path)
        self.api_keys = set(api_keys or ())
        self.api_key_principals = dict(api_key_principals or {})
        self.api_keys.update(self.api_key_principals)
        self.public_submissions = public_submissions
        if public_submissions and (
            not self.api_key_principals or not self.api_keys.issubset(self.api_key_principals)
        ):
            raise ValueError("Public submissions require named principals and explicit scopes.")
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

    def _require_key(self, request: Request, scope: str = "registry:write") -> str:
        if not self.api_keys:
            return "local"
        supplied = request.headers.get("X-API-Key")
        if supplied not in self.api_keys:
            raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")
        principal = self.api_key_principals.get(supplied)
        if principal is not None:
            scopes = principal.get("scopes", [])
            if scope not in scopes and "registry:admin" not in scopes:
                raise HTTPException(status_code=403, detail=f"Missing scope: {scope}")
            return str(principal["subject"])
        import hashlib

        return "key:" + hashlib.sha256(supplied.encode()).hexdigest()[:12]

    # ------------------------------------------------------------------ #
    # Business API
    # ------------------------------------------------------------------ #
    def register(
        self,
        card: dict[str, Any],
        node_url: str,
        status: str | None = None,
        review_note: str | None = None,
        submitted_by: str | None = None,
    ) -> dict[str, Any]:
        from ..geocard.model import GeoCard
        from ..geocard.validator import validate_card_schema

        if self.public_submissions:
            import copy

            card = copy.deepcopy(card)
            if "trust" in card:
                card["trust"] = {"verified": False}
            status = STATUS_PENDING
        report = validate_card_schema(card)
        if not report.valid:
            raise HTTPException(status_code=422, detail={"schema_errors": report.errors})
        try:
            entry = RegistryEntry(
                card=GeoCard.model_validate(card),
                node_url=node_url,
                status=status or STATUS_APPROVED,
                review_note=review_note,
                submitted_by=submitted_by,
            )
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

    def list(self, status: str | None = STATUS_APPROVED) -> dict[str, Any]:
        """List cards. Defaults to approved only (review workflow); pass a
        specific status (e.g. ``pending``) for the review queue, or the
        literal string ``"all"`` to include every state (admin)."""
        if status == "all":
            status = None
        entries = [e.to_dict() for e in self.store.list_entries(status=status)]
        return {"count": len(entries), "cards": entries}

    def approve(
        self, card_id: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        try:
            entry = self.store.approve(card_id, note=note, by=by)
        except RegistryEntryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "approved", "id": card_id, "entry": entry.to_dict()}

    def reject(
        self, card_id: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        try:
            entry = self.store.reject(card_id, note=note, by=by)
        except RegistryEntryNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "rejected", "id": card_id, "entry": entry.to_dict()}

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
        # status: None -> store's approved-only default; "all" -> every
        # state; explicit values (pending / approved / rejected) filter.
        status = _opt("status")
        status_filter: str | None = STATUS_APPROVED
        if status == "all":
            status_filter = None
        elif status in REVIEW_STATUSES:
            status_filter = status
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
            status=status_filter,
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

    # ------------------------------------------------------------------ #
    # Recipes (V1.1) —— 方案目录：与卡片共用 pending/approved/rejected 词表
    # ------------------------------------------------------------------ #
    def register_recipe(
        self,
        recipe: dict[str, Any] | Any,
        *,
        provider_url: str = "",
        status: str | None = None,
        submitted_by: str | None = None,
        review_note: str | None = None,
        replace: bool = False,
    ) -> dict[str, Any]:
        """登记一个配方。默认 ``pending``：方案比卡片更该过审再被复用。"""
        from ..recipe import RecipeConflict, RecipeError, RecipeValidationError

        try:
            entry = self.recipes.register(
                recipe,
                provider_url=provider_url,
                status=status or STATUS_PENDING,
                submitted_by=submitted_by,
                review_note=review_note,
                replace=replace,
            )
        except RecipeConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RecipeValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors) from exc
        except RecipeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "registered", "id": entry.recipe_id, "entry": entry.to_dict()}

    def unregister_recipe(self, recipe_id: str) -> dict[str, Any]:
        from ..recipe import RecipeNotFound

        try:
            self.recipes.remove(recipe_id)
        except RecipeNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "removed", "id": recipe_id}

    def get_recipe(self, address: str, approved_only: bool = False) -> dict[str, Any]:
        entry = self.recipes.resolve(address, approved_only=approved_only)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Recipe not registered: {address}")
        return {"entry": entry.to_dict(), "summary": entry.summary()}

    def list_recipes(
        self,
        *,
        status: str | None = None,
        namespace: str | None = None,
        tag: str | None = None,
        param: str | None = None,
        role: str | None = None,
        query: str = "",
        approved_only: bool = False,
    ) -> dict[str, Any]:
        hits = self.recipes.search(
            query,
            namespace=namespace,
            status=None if status == "all" else status,
            tag=tag,
            param=param,
            output_role=role,
            approved_only=approved_only,
        )
        return {"count": len(hits), "recipes": [h.summary() for h in hits]}

    def approve_recipe(
        self, recipe_id: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        entry = self._review_recipe(recipe_id, approve=True, note=note, by=by)
        return {"status": "approved", "id": entry.recipe_id, "entry": entry.to_dict()}

    def reject_recipe(
        self, recipe_id: str, note: str | None = None, by: str | None = None
    ) -> dict[str, Any]:
        entry = self._review_recipe(recipe_id, approve=False, note=note, by=by)
        return {"status": "rejected", "id": entry.recipe_id, "entry": entry.to_dict()}

    def _review_recipe(
        self, recipe_id: str, *, approve: bool, note: str | None, by: str | None
    ) -> Any:
        """审批一条配方。无版本的地址只在**唯一**匹配时才被接受（否则 409）。"""
        from ..recipe import RecipeAmbiguous, RecipeNotFound

        try:
            return (
                self.recipes.approve(recipe_id, by=by, note=note)
                if approve
                else self.recipes.reject(recipe_id, by=by, note=note)
            )
        except RecipeAmbiguous as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RecipeNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
        """派生一份方案（默认登记为 ``pending``）。**源方案不改动。**"""
        from ..recipe import RecipeError, RecipeNotFound

        try:
            forked = self.recipes.fork(
                recipe_id,
                namespace=namespace,
                name=name,
                version=version,
                params=params,
                fixed=fixed,
                title=title,
                register=register,
                provider_url=provider_url,
            )
        except RecipeNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RecipeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        entry = self.recipes.get(forked.id)
        return {
            "status": "forked",
            "from": recipe_id,
            "id": forked.id,
            "recipe": forked.to_dict(),
            "entry": entry.to_dict() if entry is not None else None,
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "geonexus-registry",
            "name": self.name,
            "version": SERVICE_VERSION,
            "cards": self.store.count(),
            "recipes": len(self.recipes),
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
            subject = self._require_key(request, "registry:submit")
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Body must be valid JSON") from None
            if not isinstance(body, dict) or "card" not in body:
                raise HTTPException(status_code=422, detail="Body must contain 'card'")
            node_url = body.get("node_url") or "unknown"
            # Review workflow (v1.1): register with status=pending to submit
            # a card for review; approve() later makes it discoverable.
            status = body.get("status")
            if self.public_submissions and node_url == "unknown":
                raise HTTPException(status_code=422, detail="A deployment endpoint is required.")
            if self.public_submissions:
                from ..registration import safe_url, validate_profile

                try:
                    node_url = safe_url(node_url)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                report = validate_profile(body["card"])
                if not report.valid:
                    raise HTTPException(status_code=422, detail=report.to_dict())
            result = self.register(body["card"], node_url, status=status, submitted_by=subject)
            return JSONResponse(result, status_code=201)

        @app.get("/cards")
        async def list_cards(status: str | None = None) -> dict[str, Any]:
            # None -> server default (approved); "all" -> every state.
            return self.list(status=status if status is not None else STATUS_APPROVED)

        @app.post("/cards/{card_id}/approve")
        async def approve_card(request: Request, card_id: str) -> dict[str, Any]:
            subject = self._require_key(request, "registry:review")
            body = (
                await request.json()
                if request.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            return self.approve(card_id, note=(body or {}).get("note"), by=subject)

        @app.post("/cards/{card_id}/reject")
        async def reject_card(request: Request, card_id: str) -> dict[str, Any]:
            subject = self._require_key(request, "registry:review")
            body = (
                await request.json()
                if request.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            return self.reject(card_id, note=(body or {}).get("note"), by=subject)

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
            status: str | None = None,
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
                    "status": status,
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

        # ---- Recipes (V1.1) ------------------------------------------- #
        # 配方地址里有 '/' 和 '@'，所以用 ``:path`` 转换器整段接收，而不是让
        # 调用方去猜怎么转义。更具体的 ``/fork``、``/approve`` 先声明。
        @app.post("/recipes")
        async def register_recipe_endpoint(request: Request) -> JSONResponse:
            subject = self._require_key(request, "registry:submit")
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Body must be valid JSON") from None
            if not isinstance(body, dict) or "recipe" not in body:
                raise HTTPException(status_code=422, detail="Body must contain 'recipe'")
            result = self.register_recipe(
                body["recipe"],
                provider_url=body.get("provider_url") or body.get("node_url") or "",
                status=STATUS_PENDING if self.public_submissions else body.get("status"),
                submitted_by=subject,
                review_note=body.get("review_note"),
                replace=False if self.public_submissions else bool(body.get("replace")),
            )
            return JSONResponse(result, status_code=201)

        @app.get("/recipes")
        async def list_recipes_endpoint(
            status: str | None = None,
            namespace: str | None = None,
            tag: str | None = None,
            param: str | None = None,
            role: str | None = None,
            q: str | None = None,
            approved_only: bool = False,
        ) -> dict[str, Any]:
            return self.list_recipes(
                status=status,
                namespace=namespace,
                tag=tag,
                param=param,
                role=role,
                query=q or "",
                approved_only=approved_only,
            )

        @app.post("/recipes/fork")
        async def fork_recipe_endpoint(request: Request) -> JSONResponse:
            self._require_key(request)
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Body must be valid JSON") from None
            if not isinstance(body, dict) or not body.get("recipe_id"):
                raise HTTPException(status_code=422, detail="Body must contain 'recipe_id'")
            result = self.fork_recipe(
                body["recipe_id"],
                namespace=body.get("namespace"),
                name=body.get("name"),
                version=body.get("version"),
                params=body.get("params"),
                fixed=body.get("fixed"),
                title=body.get("title"),
                provider_url=body.get("provider_url") or "",
                register=body.get("register", True),
            )
            return JSONResponse(result, status_code=201)

        @app.post("/recipes/{recipe_id:path}/approve")
        async def approve_recipe_endpoint(recipe_id: str, request: Request) -> dict[str, Any]:
            subject = self._require_key(request, "registry:review")
            body = (
                await request.json()
                if request.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            return self.approve_recipe(recipe_id, note=(body or {}).get("note"), by=subject)

        @app.post("/recipes/{recipe_id:path}/reject")
        async def reject_recipe_endpoint(recipe_id: str, request: Request) -> dict[str, Any]:
            subject = self._require_key(request, "registry:review")
            body = (
                await request.json()
                if request.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            return self.reject_recipe(recipe_id, note=(body or {}).get("note"), by=subject)

        @app.get("/recipes/{recipe_id:path}")
        async def get_recipe_endpoint(
            recipe_id: str, approved_only: bool = False, version: str | None = None
        ) -> dict[str, Any]:
            # 地址可以不带版本（``recipe://ns/name``）；给 ``version`` 时拼成精确
            # 地址，这样"看 1.0 还是 1.1"由调用方明确决定，而不是靠猜。
            address = f"{recipe_id}@{version}" if version and "@" not in recipe_id else recipe_id
            return self.get_recipe(address, approved_only=approved_only)

        @app.delete("/recipes/{recipe_id:path}")
        async def delete_recipe_endpoint(recipe_id: str, request: Request) -> dict[str, Any]:
            self._require_key(request)
            return self.unregister_recipe(recipe_id)

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

        @app.post("/plugins")
        async def publish_plugin(request: Request) -> JSONResponse:
            """发布插件条目。登记 ≠ 上架：默认 pending，approve 后才可被 install。"""
            self._require_key(request, "registry:submit")
            try:
                body = await request.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Body must be valid JSON") from None
            raw = body.get("plugin") if isinstance(body, dict) else None
            if not isinstance(raw, dict):
                raise HTTPException(status_code=422, detail="Body must contain 'plugin'")
            from .models import PluginEntry as _PE

            try:
                entry = _PE.model_validate(raw)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            # URL 引荐必须带 sha256：没有校验和的下载地址是可投毒的
            if entry.install_ref.startswith(("http://", "https://")) and not entry.sha256:
                raise HTTPException(
                    status_code=422, detail="URL install_ref requires sha256 (install verifies it)"
                )
            entry.status = STATUS_PENDING  # 提交即待审，不接受自封 approved
            try:
                self.store.register_plugin(entry)
            except PluginEntryConflict as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            return JSONResponse({"status": "published", "entry": entry.to_dict()}, status_code=201)

        @app.get("/plugins")
        async def list_plugins(
            request: Request, status: str | None = None, q: str = ""
        ) -> dict[str, Any]:
            # 与 /cards 同约定：不传 = 只看 approved；"all" = 全量（含待审）
            if status and status != STATUS_APPROVED:
                self._require_key(request, "registry:review")
            st = None if status == "all" else (status or STATUS_APPROVED)
            if q:
                items = self.store.search_plugins(q, status=st)
            else:
                items = self.store.list_plugins(status=st)
            return {"plugins": [e.to_dict() for e in items], "count": len(items)}

        @app.get("/plugins/{name}/{version}")
        async def get_plugin(name: str, version: str, request: Request) -> dict[str, Any]:
            entry = self.store.get_plugin(name, version)
            if entry is None:
                raise HTTPException(
                    status_code=404, detail=f"Plugin not published: {name}@{version}"
                )
            if entry.status != STATUS_APPROVED:
                self._require_key(request, "registry:review")
            return {"entry": entry.to_dict()}

        @app.post("/plugins/{name}/{version}/approve")
        async def approve_plugin(name: str, version: str, request: Request) -> dict[str, Any]:
            self._require_key(request, "registry:review")
            body = await request.json() if request.headers.get("content-length") else {}
            try:
                entry = self.store.review_plugin(
                    name,
                    version,
                    approve=True,
                    note=(body or {}).get("note"),
                    by=(body or {}).get("by"),
                )
            except PluginEntryNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return {"status": "approved", "entry": entry.to_dict()}

        @app.post("/plugins/{name}/{version}/reject")
        async def reject_plugin(name: str, version: str, request: Request) -> dict[str, Any]:
            self._require_key(request, "registry:review")
            body = await request.json() if request.headers.get("content-length") else {}
            try:
                entry = self.store.review_plugin(
                    name,
                    version,
                    approve=False,
                    note=(body or {}).get("note"),
                    by=(body or {}).get("by"),
                )
            except PluginEntryNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return {"status": "rejected", "entry": entry.to_dict()}

        @app.delete("/plugins/{name}/{version}")
        async def remove_plugin(name: str, version: str, request: Request) -> dict[str, Any]:
            self._require_key(request, "registry:review")
            try:
                entry = self.store.remove_plugin(name, version)
            except PluginEntryNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return {"status": "removed", "entry": entry.to_dict()}

        return app

    def run(self, host: str = "127.0.0.1", port: int = 8790, log_level: str = "info") -> None:
        import uvicorn

        uvicorn.run(self.create_app(), host=host, port=port, log_level=log_level)

    def start_in_thread(self, host: str | None = None, port: int | None = None) -> Any:
        """Start the registry HTTP server in a background thread (tests/embeds)."""
        import threading
        import time

        import uvicorn

        config = uvicorn.Config(
            self.create_app(),
            host=host or "127.0.0.1",
            port=port if port is not None else 8790,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        class RunningRegistry:
            def __init__(self, srv: uvicorn.Server, thr: threading.Thread) -> None:
                self._server = srv
                self._thread = thr

            def wait_until_ready(self, timeout: float = 15.0) -> RunningRegistry:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if self._server.started:
                        return self
                    if not self._thread.is_alive():
                        raise RuntimeError("registry thread exited before ready")
                    time.sleep(0.05)
                raise TimeoutError("registry not ready in time")

            @property
            def port(self) -> int:
                return self._server.servers[0].sockets[0].getsockname()[1]

            def stop(self, timeout: float = 10.0) -> None:
                self._server.should_exit = True
                self._thread.join(timeout=timeout)

        return RunningRegistry(server, thread)
