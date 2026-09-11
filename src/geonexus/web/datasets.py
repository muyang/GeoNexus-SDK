"""Data registration endpoints (v1.1): upload → GeoCard → review.

Implements the Web side of the data registration & review workflow:

- ``POST /api/datasets/upload``    — multipart file upload → draft GeoCard
  (metadata auto-extracted) + file stored under ``WebConfig.datasets_dir``
- ``POST /api/datasets/{id}/submit`` — draft → pending (registered at the
  registry, hidden from discovery)
- ``GET  /api/datasets/pending``   — review queue (admin)
- ``POST /api/datasets/{id}/approve`` / ``/reject`` — review decision
- ``GET  /api/datasets/{id}``      — draft/entry detail

Registered state lives in the shared GeoCard Registry (status pending →
approved / rejected); uploaded files stay on the BFF host under
``datasets_dir`` and are referenced by the card's ``access.endpoint``.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..metadata import raster_to_geocard
from ..registry import RegistryClient, RegistryClientError
from .auth import BearerAuth
from .router import WebConfig

logger = logging.getLogger(__name__)

# Supported file extensions (mirrors rasterio's GTiff coverage).
_SUPPORTED_EXT = {".tif", ".tiff", ".cog"}


class DatasetSubmit(BaseModel):
    name: str | None = None
    description: str | None = None
    capabilities: list[str] = []
    tags: list[str] = []
    start: str | None = None
    end: str | None = None


def create_datasets_router(config: WebConfig) -> APIRouter:
    router = APIRouter(prefix="/api/datasets")
    auth = BearerAuth(config.jwt)

    def _drafts_dir() -> Path:
        base = Path(config.datasets_dir) if config.datasets_dir else Path("uploads")
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _registry() -> RegistryClient:
        return RegistryClient(
            config.registry_url, api_key=config.registry_api_key
        )

    # ------------------------------------------------------------------ #
    @router.post("/upload")
    async def upload(
        file: UploadFile = File(...),
        name: str | None = Form(default=None),
        description: str | None = Form(default=None),
        capabilities: str | None = Form(default=None),
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        """Accept a raster file, store it, and generate its draft GeoCard."""
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in _SUPPORTED_EXT:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type {suffix!r}; expected {sorted(_SUPPORTED_EXT)}",
            )
        upload_id = uuid.uuid4().hex[:12]
        stored = _drafts_dir() / f"{upload_id}{suffix}"
        data = await file.read()
        stored.write_bytes(data)

        cap_list = [c.strip() for c in (capabilities or "").split(",") if c.strip()]
        try:
            card = raster_to_geocard(
                stored,
                name=name,
                description=description,
                card_id=f"upload-{upload_id}",
                tags=["uploaded"] + cap_list,
                capabilities=cap_list,
            )
        except Exception as exc:  # noqa: BLE001 - not a readable raster
            stored.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail=f"Cannot read raster: {exc}") from exc

        return {
            "id": upload_id,
            "status": "draft",
            "card": card.to_dict(),
            "file": str(stored),
            "next": "POST /api/datasets/{id}/submit",
        }

    # ------------------------------------------------------------------ #
    @router.post("/{dataset_id}/submit")
    def submit(
        dataset_id: str,
        body: DatasetSubmit | None = None,
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        """Register the draft card at the registry as pending (review)."""
        card_path = _drafts_dir() / f"{dataset_id}.tif"
        if not card_path.exists():
            card_path = _drafts_dir() / f"{dataset_id}.tiff"
        if not card_path.exists():
            raise HTTPException(status_code=404, detail=f"Draft not found: {dataset_id}")
        try:
            card = raster_to_geocard(
                card_path,
                name=(body.name if body else None),
                description=(body.description if body else None),
                card_id=f"upload-{dataset_id}",
                tags=["uploaded"] + (body.tags if body else []),
                capabilities=(body.capabilities if body else []),
                start=(body.start if body else None),
                end=(body.end if body else None),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=f"Cannot read raster: {exc}") from exc

        node_url = config.upload_node_url or config.default_node_url or "unknown"
        try:
            with _registry() as rc:
                result = rc.register(card, node_url=node_url, status="pending")
        except RegistryClientError as exc:
            raise HTTPException(status_code=502, detail=f"Registry error: {exc}") from exc
        return {"id": dataset_id, "status": "pending", "registered": result}

    # ------------------------------------------------------------------ #
    @router.get("/pending")
    def pending(
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        """Review queue: cards awaiting approval."""
        try:
            with _registry() as rc:
                entries = rc.list_cards(status="pending")
        except RegistryClientError as exc:
            raise HTTPException(status_code=502, detail=f"Registry error: {exc}") from exc
        return {"count": len(entries), "cards": entries}

    # ------------------------------------------------------------------ #
    @router.post("/{card_id}/approve")
    def approve(
        card_id: str,
        note: str | None = Form(default=None),
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        """Approve a pending card → discoverable."""
        try:
            with _registry() as rc:
                return rc.approve(card_id, note=note)
        except RegistryClientError as exc:
            raise HTTPException(status_code=502, detail=f"Registry error: {exc}") from exc

    @router.post("/{card_id}/reject")
    def reject(
        card_id: str,
        note: str | None = Form(default=None),
        _claims: dict[str, Any] = Depends(auth),
    ) -> dict[str, Any]:
        """Reject a pending card → stays hidden."""
        try:
            with _registry() as rc:
                return rc.reject(card_id, note=note)
        except RegistryClientError as exc:
            raise HTTPException(status_code=502, detail=f"Registry error: {exc}") from exc

    return router
