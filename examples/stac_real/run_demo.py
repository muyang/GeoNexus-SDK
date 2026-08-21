"""Real Sentinel-2 data demo (V0.4) — STAC adapter + real pixels when possible.

Flow:
  1. Fetch a REAL Sentinel-2 L2A item from Microsoft Planetary Computer STAC
     (default: the most recent tile over the Amazon, T21NWA).
  2. Import it as a GeoCard (`geonexus.adapters.import_stac_item`).
  3. Attempt to read REAL B04/B08 pixels (signed COG, windowed read).
  4. Compute NDVI. If real pixels are unavailable (network/auth), fall back
     to synthetic execution and say so loudly — outputs are labelled
     GEONEXUS_REAL=TRUE or GEONEXUS_SYNTHETIC=TRUE accordingly.

Usage:
    python run_demo.py [--output DIR] [--item URL]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from geonexus.adapters import (  # noqa: E402
    StacAdapterError,
    fetch_stac_item,
    import_stac_item,
)

DEMO_DIR = Path(__file__).resolve().parent

DEFAULT_ITEM = (
    "https://planetarycomputer.microsoft.com/api/stac/v1/collections/"
    "sentinel-2-l2a/items/S2B_MSIL2A_20260819T141709_R010_T21NWA_20260819T175632"
)


def _get_sas_token(collection: str = "sentinel-2-l2a") -> str:
    """Fetch an anonymous SAS token for a Planetary Computer collection."""
    import httpx

    response = httpx.get(
        f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}",
        timeout=20,
    )
    response.raise_for_status()
    token = response.json().get("token", "")
    if not token:
        raise RuntimeError("SAS token response contained no token")
    return token


def _try_read_real_ndvi(
    item: dict[str, Any], output_dir: Path, window_size: int = 256
) -> dict[str, Any] | None:
    """Try to read real B04/B08 pixels and compute NDVI.

    Strategy: scan a coarse overview of the scene to locate valid (non-zero)
    pixels, then read a small full-resolution window at that centroid —
    small windows keep the byte range small enough for slow/unreliable links.

    Returns stats dict on success, None on any failure (network/auth/etc.).
    """
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    assets = item.get("assets", {})
    if "B04" not in assets or "B08" not in assets:
        return None
    try:
        token = _get_sas_token()
    except Exception as exc:  # noqa: BLE001 - degrade path
        print(f"      (SAS token unavailable: {exc})")
        return None

    def signed(asset: dict[str, Any]) -> str:
        href = asset["href"]
        # The Planetary Computer SAS token IS a full Azure SAS query string;
        # it must be appended directly (no ?token= wrapper).
        sep = "&" if "?" in href else "?"
        return f"{href}{sep}{token}"

    b04_url = signed(assets["B04"])
    b08_url = signed(assets["B08"])
    gdal_env = dict(
        GDAL_HTTP_TIMEOUT=30,
        GDAL_HTTP_MAX_RETRY=5,
        GDAL_HTTP_RETRY_DELAY=2,
        GDAL_HTTP_MULTIPLEX="NO",
    )

    try:
        with rasterio.Env(**gdal_env):
            # 1. Coarse overview scan to find valid pixels (small tiles).
            with rasterio.open(b04_url) as src:
                overview = src.read(1, out_shape=(549, 549))
                scale = src.width / overview.shape[1]
            ys, xs = np.where(overview > 0)
            if len(xs) == 0:
                print("      (no valid pixels found in scene overview)")
                return None
            cx, cy = int(xs.mean() * scale), int(ys.mean() * scale)
            w = Window(
                max(cx - window_size // 2, 0),
                max(cy - window_size // 2, 0),
                window_size,
                window_size,
            )

            # 2. Read the full-resolution window from both bands.
            with rasterio.open(b04_url) as src:
                red = src.read(1, window=w)
                window_transform = src.window_transform(w)
                profile = src.profile.copy()
            with rasterio.open(b08_url) as src:
                nir = src.read(1, window=w)
    except Exception as exc:  # noqa: BLE001 - degrade path
        print(f"      (real pixel read failed: {type(exc).__name__}: {str(exc)[:120]})")
        return None

    # Compute NDVI (DN ratio; L2A DN = reflectance x 10000, ratio is invariant).
    red_f = red.astype(np.float32)
    nir_f = nir.astype(np.float32)
    denom = red_f + nir_f
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(denom != 0, (nir_f - red_f) / np.where(denom == 0, 1, denom), -9999.0)
    valid = np.isfinite(ndvi) & (ndvi != -9999.0) & (red > 0) & (nir > 0)
    ndvi = np.where(valid, np.clip(ndvi, -1.0, 1.0), -9999.0)

    out_path = output_dir / "ndvi_real.tif"
    profile.update(
        dtype="float32",
        driver="GTiff",
        nodata=-9999.0,
        transform=window_transform,
    )
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(ndvi.astype(np.float32), 1)
        dst.update_tags(
            GEONEXUS_REAL="TRUE",
            GEONEXUS_SOURCE="Microsoft Planetary Computer sentinel-2-l2a",
            GEONEXUS_ITEM=item["id"],
            DESCRIPTION="REAL Sentinel-2 L2A NDVI (windowed, 10m)",
        )

    vals = ndvi[valid]
    if vals.size == 0:
        print("      (window contained no valid pixels)")
        return None
    return {
        "count": int(vals.size),
        "mean": float(vals.mean()),
        "median": float(np.median(vals)),
        "std": float(vals.std()),
        "min": float(vals.min()),
        "max": float(vals.max()),
        "vegetation_fraction": float((vals > 0.3).mean()),
        "real": True,
        "raster": str(out_path),
    }


def run(output_dir: str | None = None, item_url: str | None = None) -> dict:
    item_url = item_url or DEFAULT_ITEM
    print("=" * 74)
    print("GeoNexus Reference Stack - Real Sentinel-2 Data Demo (V0.4)")
    print("STAC adapter + real pixels (Microsoft Planetary Computer)")
    print("=" * 74)

    out_dir = Path(output_dir) if output_dir else DEMO_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Fetch a REAL STAC item and import it as a GeoCard ------------------ #
    print("\n[1/4] Fetching REAL Sentinel-2 item from Planetary Computer STAC")
    try:
        raw_item = fetch_stac_item(item_url, timeout=30)
        card = import_stac_item(item_url, timeout=30)
    except StacAdapterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("The demo needs network access to planetarycomputer.microsoft.com.")
        return {"status": "error", "error": str(exc)}
    print(f"      GeoCard '{card.id}'")
    print(f"      scene date : {card.temporal.start if card.temporal else '-'}")
    print(
        f"      bbox       : {card.spatial.bbox if card.spatial else '-'} ({card.spatial.crs if card.spatial else '-'})"
    )
    print(f"      bands      : {card.band_names()[:6]} ...")

    # 2. Try REAL pixels ---------------------------------------------------- #
    print("\n[2/4] Attempting REAL pixel read (B04/B08, windowed COG)")
    real_stats = _try_read_real_ndvi(raw_item, out_dir)

    if real_stats:
        print(
            f"      REAL NDVI computed: mean={real_stats['mean']:.3f}, "
            f"vegetation_fraction={real_stats['vegetation_fraction']:.3f}"
        )
        stats_2015 = stats_2025 = real_stats
        source_label = "REAL Sentinel-2 (windowed)"
    else:
        print("      REAL pixels unavailable on this machine (network/auth).")
        print("      Falling back to SYNTHETIC execution (see amazon_ndvi demo).")
        sys.path.insert(0, str(DEMO_DIR.parent.as_posix()))
        from examples.amazon_ndvi import skill as ndvi_skill
        from geonexus.geomcp.models import ExecuteParams
        from geonexus.geonode import GeoNode

        node = GeoNode(name="fallback-node", workdir=str(out_dir))
        node.register_geocard(card)
        node.register_skill(
            name="ndvi-analysis",
            handler=ndvi_skill.ndvi_analysis_handler,
            input_schema={"required": ["red", "nir"]},
        )
        stats_by_year = {}
        for year in (2015, 2025):
            scenes = ndvi_skill.generate_synthetic_scene(year, out_dir, seed=year)
            ndvi_out = out_dir / f"ndvi_{year}.tif"
            result = node.execute(
                ExecuteParams(
                    skill="ndvi-analysis",
                    geocards=[card.id],
                    params={
                        "red": scenes["red"],
                        "nir": scenes["nir"],
                        "output": str(ndvi_out),
                    },
                    request_id=f"stac-real-{year}",
                )
            )
            stats_by_year[year] = result["outputs"]["stats"]
        change = ndvi_skill.compute_change(
            str(out_dir / "ndvi_2015.tif"),
            str(out_dir / "ndvi_2025.tif"),
            str(out_dir / "ndvi_change.tif"),
        )
        print(f"      mean NDVI change (synthetic): {change['mean']:.3f}")
        stats_2015, stats_2025 = stats_by_year[2015], stats_by_year[2025]
        source_label = "SYNTHETIC (real pixels unavailable)"

    # 3. Report -------------------------------------------------------------- #
    print(f"\n[3/4] Statistics ({source_label})")
    print(f"      {'metric':<20}{'value':>12}")
    for metric in ("mean", "median", "std", "min", "max", "vegetation_fraction"):
        value = (stats_2015 if real_stats else stats_2025).get(metric)
        if value is not None:
            print(f"      {metric:<20}{value:>12.4f}")

    print(f"\n[4/4] Outputs in {out_dir}")
    for f in sorted(out_dir.glob("*.tif")):
        print(f"      - {f.name}")
    if real_stats:
        print("      Labels: GEONEXUS_REAL=TRUE (real Sentinel-2 L2A, windowed)")
    else:
        print("      Labels: GEONEXUS_SYNTHETIC=TRUE — see examples/stac_real/README.md")

    summary = {
        "demo": "stac-real",
        "geocard": card.id,
        "real_pixels": bool(real_stats),
        "source_label": source_label,
        "stats": real_stats
        or {
            "2015": stats_2015,
            "2025": stats_2025,
        },
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real Sentinel-2 data demo")
    parser.add_argument("--output", default=None)
    parser.add_argument("--item", default=DEFAULT_ITEM, help="STAC item URL")
    args = parser.parse_args(argv)
    run(output_dir=args.output, item_url=args.item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
