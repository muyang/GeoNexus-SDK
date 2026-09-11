"""GeoCard metadata extraction from local raster files (v1.1).

Bridges the *upload a file → generate a GeoCard* step of the data
registration workflow: :func:`inspect_raster` reads a GeoTIFF (or any
rasterio-readable raster) and returns its metadata (CRS, bounds, resolution,
bands, dtype); :func:`raster_to_geocard` turns that metadata plus caller
description into a ready-to-register GeoCard whose ``access.endpoint``
points at the file — so a freshly uploaded dataset can be registered,
reviewed and then served through GeoMCP like any other asset.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .geocard import GeoCard, GeoCardBuilder
from .geocard.validator import validate_card_schema

logger = logging.getLogger(__name__)

_SUPPORTED_DRIVERS = {"GTiff", "COG", "PNG", "JPEG"}


def _require_rasterio() -> Any:
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover - env dependent
        raise ImportError(
            "Raster metadata extraction requires rasterio. "
            "Install it with `pip install rasterio`."
        ) from exc
    return rasterio


def inspect_raster(path: str | os.PathLike) -> dict[str, Any]:
    """Read a raster file and return its metadata.

    Returns:
        ``{"path", "driver", "width", "height", "count", "dtype",
        "crs", "bounds": [w, s, e, n], "resolution": [x, y],
        "bands": [{"name", "dtype", "description"}]}``
        ``crs`` is the authority string (e.g. ``EPSG:4326``) when readable.
    """
    rasterio = _require_rasterio()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Raster not found: {path}")
    with rasterio.open(p) as src:
        bounds = src.bounds
        try:
            crs = str(src.crs) if src.crs else None
        except Exception:  # noqa: BLE001 - unreadable CRS is not fatal
            crs = None
        bands = []
        for i in range(1, src.count + 1):
            desc = src.descriptions[i - 1] if i <= len(src.descriptions) else None
            bands.append(
                {
                    "name": desc or f"band{i}",
                    "dtype": str(src.dtypes[i - 1]) if i <= len(src.dtypes) else str(src.dtypes[0]),
                    "description": desc,
                }
            )
        return {
            "path": str(p.resolve()),
            "driver": src.driver,
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "dtype": src.dtypes[0] if src.dtypes else None,
            "crs": crs,
            "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
            "resolution": list(src.res) if src.res else None,
            "bands": bands,
        }


def raster_stats(path: str | os.PathLike, band: int = 1) -> dict[str, Any]:
    """Compute basic statistics of one raster band (nodata-aware).

    Returns ``{"min", "max", "mean", "median", "std", "count"}``; all values
    are ``None`` when the band has no valid pixels.
    """
    import numpy as np

    rasterio = _require_rasterio()
    with rasterio.open(path) as src:
        if band > src.count:
            raise ValueError(f"band {band} out of range (count={src.count})")
        data = src.read(band).astype(np.float32)
        nodata = src.nodata
    valid = data[np.isfinite(data)]
    if nodata is not None:
        valid = valid[valid != float(nodata)]
    if valid.size == 0:
        return {"min": None, "max": None, "mean": None, "median": None, "std": None, "count": 0}
    return {
        "min": float(np.min(valid)),
        "max": float(np.max(valid)),
        "mean": float(np.mean(valid)),
        "median": float(np.median(valid)),
        "std": float(np.std(valid)),
        "count": int(valid.size),
    }


def raster_to_geocard(
    path: str | os.PathLike,
    *,
    name: str | None = None,
    description: str | None = None,
    card_id: str | None = None,
    tags: list[str] | None = None,
    capabilities: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    provider: str = "user-upload",
    geocard_version: str = "1.0",
    validate: bool = True,
) -> GeoCard:
    """Generate a GeoCard for a raster file from its actual metadata.

    Args:
        path: The raster file.
        name: Display name (default: file stem).
        description: Free-text description (default: derived from metadata).
        card_id: Stable id (default: file stem, sanitized).
        tags: Extra discovery tags.
        capabilities: Declared capabilities (e.g. ``["ndvi"]``); the driver
            and band count are always added.
        start / end: Optional temporal coverage (ISO dates).
        provider: Provenance provider label.
        validate: Run schema validation before returning.

    Returns:
        A :class:`GeoCard` with spatial (bbox/CRS/resolution), bands and
        access (protocol ``file``, endpoint = the file path).
    """
    meta = inspect_raster(path)
    stem = Path(path).stem

    def _safe_id(value: str) -> str:
        out = "".join(c if c.isalnum() or c in "-_." else "-" for c in value)
        return out.strip("-.") or "dataset"

    builder = (
        GeoCardBuilder(
            id=card_id or _safe_id(stem),
            type="data",
            name=name or stem,
            description=description or (
                f"Uploaded raster ({meta['driver']}, {meta['width']}x{meta['height']}, "
                f"{meta['count']} band(s), dtype {meta['dtype']})"
            ),
            geocard_version=geocard_version,
        )
        .tag("uploaded", meta["driver"] or "raster", *(tags or []))
        .spatial(
            bbox=meta["bounds"],
            crs=meta["crs"],
            resolution=meta["resolution"][0] if meta["resolution"] else None,
        )
        .access(protocol="file", endpoint=meta["path"], format="GeoTIFF")
        .provenance(
            provider=provider,
            source=meta["path"],
            lineage=f"Auto-generated GeoCard from uploaded raster {meta['path']}",
        )
    )
    if start or end:
        builder.temporal(start=start, end=end)
    for cap in capabilities or []:
        builder.capability(cap, "Declared by uploader")
    for band in meta["bands"]:
        builder.band(
            name=band["name"],
            dtype=band["dtype"],
            description=band["description"] or None,
        )

    card = builder.build()
    if validate:
        report = validate_card_schema(card.to_dict())
        if not report.valid:
            raise ValueError(f"Generated GeoCard failed schema validation: {report.errors}")
    return card
