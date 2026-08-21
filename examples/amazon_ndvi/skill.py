"""Amazon NDVI demo — GeoSkill implementation.

**IMPORTANT**: this demo uses **synthetic** raster data generated
procedurally. It is NOT real Sentinel-2 or Landsat imagery. The synthetic
nature is explicitly recorded in the GeoCard provenance and in every output
file's metadata.

The skill computes NDVI (Normalized Difference Vegetation Index):

    ndvi = (nir - red) / (nir + red)

and writes GeoTIFF outputs plus summary statistics.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Spatial coverage of the demo scene (Amazon rainforest region).
AMAZON_BBOX = [-73.9, -15.0, -44.0, 5.0]
AMAZON_CRS = "EPSG:4326"

NDVI_NODATA = -9999.0


# --------------------------------------------------------------------------- #
# Raster I/O (rasterio with a loud, honest fallback)
# --------------------------------------------------------------------------- #
class GeoSkillRuntimeError(RuntimeError):
    """Raised when a skill cannot complete its work."""


def _require_rasterio() -> Any:
    """Import rasterio or raise a clear error explaining the requirement.

    The MVP does not silently fake GeoTIFF output: if rasterio is
    unavailable, computation still works with numpy, but GeoTIFF writing
    fails loudly with instructions.
    """
    try:
        import rasterio  # noqa: PLC0415

        return rasterio
    except ImportError as exc:  # pragma: no cover - env dependent
        raise GeoSkillRuntimeError(
            "GeoTIFF output requires 'rasterio'. Install it with "
            "`pip install rasterio` (the GeoNexus venv includes it). "
            "NDVI computation without rasterio is available via "
            "compute_ndvi_arrays()."
        ) from exc


# --------------------------------------------------------------------------- #
# Synthetic data generation (clearly labelled as synthetic)
# --------------------------------------------------------------------------- #
def generate_synthetic_scene(
    year: int,
    output_dir: str | os.PathLike,
    width: int = 720,
    height: int = 480,
    seed: int | None = None,
) -> dict[str, str]:
    """Generate synthetic red/nir GeoTIFFs for a demo scene.

    Args:
        year: 2015 (healthy forest) or 2025 (degraded forest).
        output_dir: Directory for the generated rasters.
        width, height: Raster dimensions in pixels.
        seed: Random seed for reproducibility.

    Returns:
        ``{"red": path, "nir": path}`` paths of the generated GeoTIFFs.

    The data is procedural noise shaped like an Amazon scene; it must never
    be presented as real satellite data.
    """
    rasterio = _require_rasterio()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed if seed is not None else year)
    # Reflectance "sum" level (red + nir), ~ 0.25-0.35 with noise.
    base_level = 0.30 if year == 2015 else 0.26
    level = base_level + rng.normal(0, 0.02, size=(height, width)).astype(np.float32)

    # Target NDVI field: healthy forest around 0.75 (2015) vs degraded (2025).
    ndvi_target = np.full((height, width), 0.75 if year == 2015 else 0.42, dtype=np.float32)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)

    if year == 2025:
        # A deforested wedge (right half) plus scattered clearings.
        wedge = (xx / max(width - 1, 1)) - 0.35 * (yy / max(height - 1, 1))
        ndvi_target[wedge > 0.5] = 0.12
        clearing = ((xx - 0.30 * width) ** 2 + (yy - 0.55 * height) ** 2) < (0.06 * height) ** 2
        ndvi_target[clearing] = 0.05
    else:
        # Small natural clearings for texture.
        clearing = ((xx - 0.75 * width) ** 2 + (yy - 0.25 * height) ** 2) < (0.04 * height) ** 2
        ndvi_target[clearing] = 0.45

    ndvi = ndvi_target + rng.normal(0, 0.03, size=(height, width)).astype(np.float32)
    ndvi = np.clip(ndvi, 0.0, 0.95)

    red = np.clip(level * (1.0 - ndvi) / 2.0, 0.01, 0.5).astype(np.float32)
    nir = np.clip(level * (1.0 + ndvi) / 2.0, 0.01, 0.5).astype(np.float32)

    # Encode as uint16 DN-like values (x10000) for realistic-looking storage.
    scale = 10000
    red_u16 = (red * scale).astype(np.uint16)
    nir_u16 = (nir * scale).astype(np.uint16)

    west, south, east, north = AMAZON_BBOX
    transform = rasterio.transform.from_bounds(west, south, east, north, width, height)
    profile = dict(
        driver="GTiff",
        width=width,
        height=height,
        count=1,
        dtype="uint16",
        crs=AMAZON_CRS,
        transform=transform,
    )
    tags = {
        "GEONEXUS_SYNTHETIC": "TRUE",
        "GEONEXUS_DEMO": "amazon-ndvi",
        "GEONEXUS_YEAR": str(year),
        "DESCRIPTION": "SYNTHETIC data - not real satellite imagery",
    }
    red_path = out_dir / f"synthetic_red_{year}.tif"
    nir_path = out_dir / f"synthetic_nir_{year}.tif"
    with rasterio.open(red_path, "w", **profile) as dst:
        dst.write(red_u16, 1)
        dst.update_tags(**tags)
    with rasterio.open(nir_path, "w", **profile) as dst:
        dst.write(nir_u16, 1)
        dst.update_tags(**tags)
    logger.info("Generated synthetic scene for %s -> %s", year, out_dir)
    return {"red": str(red_path), "nir": str(nir_path)}


# --------------------------------------------------------------------------- #
# NDVI computation (numpy core, rasterio for GeoTIFF)
# --------------------------------------------------------------------------- #
def compute_ndvi_arrays(
    red: np.ndarray, nir: np.ndarray, nodata: float = NDVI_NODATA
) -> np.ndarray:
    """Compute NDVI from red/nir arrays (float32)."""
    red_f = red.astype(np.float32)
    nir_f = nir.astype(np.float32)
    denom = red_f + nir_f
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(denom != 0, (nir_f - red_f) / np.where(denom == 0, 1, denom), nodata)
    valid = np.isfinite(ndvi) & (ndvi != nodata)
    ndvi = np.where(valid, np.clip(ndvi, -1.0, 1.0), nodata)
    return ndvi


def compute_ndvi_from_files(red_path: str, nir_path: str, out_path: str) -> dict[str, Any]:
    """Compute NDVI from two GeoTIFFs and write a float32 NDVI GeoTIFF.

    Returns summary statistics (see :func:`summarize_ndvi`).
    """
    rasterio = _require_rasterio()
    with rasterio.open(red_path) as src_red, rasterio.open(nir_path) as src_nir:
        profile = src_red.profile.copy()
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)
        profile.update(dtype="float32", driver="GTiff", nodata=NDVI_NODATA)
        ndvi = compute_ndvi_arrays(src_red.read(1), src_nir.read(1))
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(ndvi, 1)
            dst.update_tags(
                GEONEXUS_SYNTHETIC="TRUE",
                GEONEXUS_DEMO="amazon-ndvi",
                GEONEXUS_SOURCE="synthetic",
                DESCRIPTION="NDVI computed from SYNTHETIC red/nir - not real data",
            )
    return summarize_ndvi(out_path)


def summarize_ndvi(path: str) -> dict[str, Any]:
    """Compute summary statistics of an NDVI raster."""
    rasterio = _require_rasterio()
    with rasterio.open(path) as src:
        data = src.read(1).astype(np.float32)
    valid = data[(data != NDVI_NODATA) & np.isfinite(data)]
    if valid.size == 0:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "std": None,
            "min": None,
            "max": None,
            "p5": None,
            "p95": None,
            "vegetation_fraction": None,
            "synthetic": True,
        }
    return {
        "count": int(valid.size),
        "mean": float(np.mean(valid)),
        "median": float(np.median(valid)),
        "std": float(np.std(valid)),
        "min": float(np.min(valid)),
        "max": float(np.max(valid)),
        "p5": float(np.percentile(valid, 5)),
        "p95": float(np.percentile(valid, 95)),
        "vegetation_fraction": float(np.mean(valid > 0.3)),
        "synthetic": True,
    }


def compute_change(ndvi_a_path: str, ndvi_b_path: str, out_path: str) -> dict[str, Any]:
    """Compute the NDVI change raster (b - a) between two NDVI GeoTIFFs."""
    rasterio = _require_rasterio()
    with rasterio.open(ndvi_a_path) as src_a, rasterio.open(ndvi_b_path) as src_b:
        a = src_a.read(1).astype(np.float32)
        b = src_b.read(1).astype(np.float32)
        profile = src_a.profile.copy()
        profile.pop("blockxsize", None)
        profile.pop("blockysize", None)
        profile.update(dtype="float32", driver="GTiff", nodata=NDVI_NODATA)
        change = np.where(
            (a != NDVI_NODATA) & (b != NDVI_NODATA) & np.isfinite(a) & np.isfinite(b),
            b - a,
            NDVI_NODATA,
        )
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(change, 1)
            dst.update_tags(
                GEONEXUS_SYNTHETIC="TRUE",
                GEONEXUS_DEMO="amazon-ndvi",
                GEONEXUS_SOURCE="synthetic",
                DESCRIPTION="NDVI change (2025-2015) from SYNTHETIC data",
            )
    stats = summarize_ndvi(out_path)
    stats["mean_change"] = stats["mean"]
    return stats


# --------------------------------------------------------------------------- #
# GeoSkill handler
# --------------------------------------------------------------------------- #
def ndvi_analysis_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """Skill handler for ``ndvi-analysis``.

    Required params:
        nir: path to the NIR GeoTIFF
        red: path to the Red GeoTIFF

    Optional params:
        output: output NDVI GeoTIFF path (defaults to workdir/ndvi.tif)
    """
    red_path = params.get("red")
    nir_path = params.get("nir")
    if not red_path or not nir_path:
        raise ValueError("ndvi-analysis requires 'red' and 'nir' raster paths")
    out_path = params.get("output")
    if not out_path:
        workdir = context.workdir if context else None
        base = Path(workdir) if workdir else Path.cwd()
        out_path = str(base / "ndvi.tif")
    stats = compute_ndvi_from_files(str(red_path), str(nir_path), str(out_path))
    return {
        "ndvi_raster": str(out_path),
        "stats": stats,
        "synthetic": True,
    }


def ndvi_change_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """Skill handler for ``ndvi-change`` (pipeline step).

    Required params:
        ndvi_a: path to the earlier NDVI GeoTIFF
        ndvi_b: path to the later NDVI GeoTIFF

    Optional params:
        output: change GeoTIFF path (defaults to workdir/ndvi_change.tif)

    Outputs the change raster ``ndvi_b - ndvi_a`` plus statistics. This skill
    is designed to consume the outputs of two ``ndvi-analysis`` steps in a
    GeoAgent pipeline via ``${stepN.outputs.ndvi_raster}`` templates.
    """
    ndvi_a = params.get("ndvi_a")
    ndvi_b = params.get("ndvi_b")
    if not ndvi_a or not ndvi_b:
        raise ValueError("ndvi-change requires 'ndvi_a' and 'ndvi_b' raster paths")
    out_path = params.get("output")
    if not out_path:
        workdir = context.workdir if context else None
        base = Path(workdir) if workdir else Path.cwd()
        out_path = str(base / "ndvi_change.tif")
    stats = compute_change(str(ndvi_a), str(ndvi_b), str(out_path))
    return {
        "change_raster": str(out_path),
        "stats": stats,
        "synthetic": True,
    }
