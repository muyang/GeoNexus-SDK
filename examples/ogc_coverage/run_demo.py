"""OGC API - Coverages demo (V1.0): raster retrieval + NDVI.

Demonstrates the standards data plane:

    GeoMCP geo.execute("coverage-ndvi", {collection: "amazon"})
      -> GeoNode
      -> GeoSkill (coverage-ndvi)
      -> OGC API - Coverages /coverage/ranges/red + /nir  (CoverageJSON)
      -> numpy arrays -> NDVI -> GeoTIFF + statistics

The demo runs a **local mock OGC Coverages server** (fully offline) that
serves synthetic red/NIR ranges as CoverageJSON. Use ``--remote URL`` to
point at a real OGC API - Coverages service instead.

Usage:
    python run_demo.py [--node-port N] [--remote URL]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, Request  # noqa: E402 (module-level for FastAPI)
from fastapi.responses import JSONResponse  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from geonexus.adapters import (  # noqa: E402
    OgcCoverageError,
    coverage_to_geotiff,
    fetch_coverage_range,
)
from geonexus.geomcp import GeoMCPClient  # noqa: E402
from geonexus.geonode import GeoNode, Skill  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent
AMAZON_BBOX = [-73.9, -15.0, -44.0, 5.0]


def _synthetic_red_nir(width: int = 64, height: int = 48, seed: int = 2020) -> tuple[Any, Any]:
    """Synthetic uint16 red/nir arrays (forest scene, DN = reflectance x 10000)."""
    rng = np.random.default_rng(seed)
    level = 0.28 + rng.normal(0, 0.02, size=(height, width)).astype(np.float32)
    ndvi_field = np.full((height, width), 0.65, dtype=np.float32)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    clearing = (xx - 0.7 * width) ** 2 + (yy - 0.3 * height) ** 2 < (0.08 * height) ** 2
    ndvi_field[clearing] = 0.2
    ndvi_field += rng.normal(0, 0.03, size=(height, width)).astype(np.float32)
    ndvi_field = np.clip(ndvi_field, 0.0, 0.9)
    red = np.clip(level * (1.0 - ndvi_field) / 2.0, 0.01, 0.5) * 10000
    nir = np.clip(level * (1.0 + ndvi_field) / 2.0, 0.01, 0.5) * 10000
    return red.astype(np.uint16), nir.astype(np.uint16)


def _build_mock_coverages_app() -> Any:
    """A minimal OGC API - Coverages implementation (CoverageJSON ranges)."""
    app = FastAPI(title="mock-ogc-coverages")
    red, nir = _synthetic_red_nir()
    height, width = red.shape

    def _coverage_json(name: str, values: Any) -> dict[str, Any]:
        return {
            "type": "Coverage",
            "domain": {
                "type": "Domain",
                "domainType": "Grid",
                "axes": {
                    "x": {"start": AMAZON_BBOX[0], "stop": AMAZON_BBOX[2], "num": width},
                    "y": {"start": AMAZON_BBOX[1], "stop": AMAZON_BBOX[3], "num": height},
                },
                "referencing": [{"coordinates": ["x", "y"], "crs": {"type": "EPSG:4326"}}],
            },
            "ranges": {
                name: {
                    "type": "NdArray",
                    "dataType": "uint16",
                    "axisNames": ["y", "x"],
                    "shape": [height, width],
                    "values": values.flatten().tolist(),
                }
            },
        }

    @app.get("/collections/amazon")
    async def collection() -> dict[str, Any]:
        return {
            "id": "amazon",
            "title": "Amazon synthetic coverage",
            "description": "SYNTHETIC red/NIR coverage for the GeoNexus demo.",
            "extent": {
                "spatial": {"bbox": [AMAZON_BBOX], "crs": "EPSG:4326"},
                "temporal": {"interval": [["2020-01-01T00:00:00Z", "2020-12-31T00:00:00Z"]]},
            },
            "links": [{"rel": "self", "href": "/collections/amazon"}],
        }

    @app.get("/collections/amazon/coverage/ranges")
    async def ranges() -> dict[str, Any]:
        return {
            "ranges": [
                {
                    "name": "red",
                    "dataType": "uint16",
                    "unit": "dn",
                    "description": "Red band (synthetic)",
                },
                {
                    "name": "nir",
                    "dataType": "uint16",
                    "unit": "dn",
                    "description": "NIR band (synthetic)",
                },
            ]
        }

    @app.get("/collections/amazon/coverage/ranges/red")
    async def red_range(request: Request) -> JSONResponse:
        return JSONResponse(_coverage_json("red", red))

    @app.get("/collections/amazon/coverage/ranges/nir")
    async def nir_range(request: Request) -> JSONResponse:
        return JSONResponse(_coverage_json("nir", nir))

    return app


def make_coverage_ndvi_skill(ogc_url: str) -> Skill:
    """Build the coverage-ndvi skill bound to an OGC Coverages endpoint."""

    def handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
        collection = params.get("collection", "amazon")
        red_range = params.get("red_range", "red")
        nir_range = params.get("nir_range", "nir")
        try:
            red, _ = fetch_coverage_range(ogc_url, collection, red_range)
            nir, _ = fetch_coverage_range(ogc_url, collection, nir_range)
        except OgcCoverageError as exc:
            raise ValueError(f"coverage retrieval failed: {exc}") from exc
        red_f = red.astype(np.float32)
        nir_f = nir.astype(np.float32)
        denom = red_f + nir_f
        with np.errstate(divide="ignore", invalid="ignore"):
            ndvi = np.where(denom != 0, (nir_f - red_f) / np.where(denom == 0, 1, denom), -9999.0)
        valid = np.isfinite(ndvi) & (denom > 0)
        ndvi = np.where(valid, np.clip(ndvi, -1.0, 1.0), -9999.0)
        out_path = params.get("output")
        if not out_path:
            base = Path(context.workdir) if context and context.workdir else Path.cwd()
            out_path = str(base / "coverage_ndvi.tif")
        coverage_to_geotiff(ndvi, out_path, crs=params.get("crs", "EPSG:4326"))
        vals = ndvi[valid]
        return {
            "ndvi_raster": out_path,
            "stats": {
                "count": int(vals.size),
                "mean": float(vals.mean()),
                "median": float(np.median(vals)),
                "std": float(vals.std()),
                "min": float(vals.min()),
                "max": float(vals.max()),
                "vegetation_fraction": float((vals > 0.3).mean()),
            },
            "source": ogc_url,
            "synthetic": True,
        }

    return Skill(
        name="coverage-ndvi",
        description=f"Compute NDVI from an OGC API - Coverages service ({ogc_url}).",
        input_schema={"required": ["collection"]},
        output_schema={},
        handler=handler,
    )


class _ServerHandle:
    def __init__(self, server, thread) -> None:
        self._server = server
        self._thread = thread

    def wait_ready(self, timeout: float = 15.0) -> _ServerHandle:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._server.started:
                return self
            time.sleep(0.05)
        raise TimeoutError("server not ready")

    @property
    def port(self) -> int:
        return self._server.servers[0].sockets[0].getsockname()[1]

    def stop(self, timeout: float = 10.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=timeout)


def _start_server(app, port: int) -> _ServerHandle:
    import threading

    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return _ServerHandle(server, thread).wait_ready()


def run(node_port: int = 8787, remote: str | None = None, output: str | None = None) -> dict:
    print("=" * 74)
    print("GeoNexus Reference Stack - OGC API - Coverages Demo (V1.0)")
    print("Raster retrieval: CoverageJSON -> NDVI -> GeoTIFF")
    print("=" * 74)

    if remote:
        ogc_url = remote.rstrip("/")
        print(f"\n[1/4] OGC Coverages endpoint: {ogc_url} (remote)")
        ogc_handle = None
    else:
        print("\n[1/4] Starting LOCAL mock OGC Coverages server (offline, synthetic)")
        mock = _build_mock_coverages_app()
        ogc_handle = _start_server(mock, 0)
        ogc_url = f"http://127.0.0.1:{ogc_handle.port}"
        print(f"      mock endpoint: {ogc_url} (collection 'amazon', ranges red/nir)")

    out_dir = Path(output) if output else DEMO_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[2/4] Starting GeoNode with 'coverage-ndvi' skill")
    node = GeoNode(name="coverage-node", port=node_port, workdir=str(out_dir))
    node.register_skill_object(make_coverage_ndvi_skill(ogc_url))
    node_handle = _start_server(node.create_app(), node_port)
    node_url = f"http://127.0.0.1:{node_handle.port if node_port == 0 else node_port}"

    try:
        print(f"[3/4] GeoMCP over HTTP at {node_url}")
        with GeoMCPClient(node_url, timeout=60) as client:
            ndvi_out = out_dir / "coverage_ndvi.tif"
            print("[4/4] geo.execute('coverage-ndvi', {collection: amazon})")
            result = client.execute(
                skill="coverage-ndvi",
                params={"collection": "amazon", "output": str(ndvi_out)},
                request_id="coverage-demo",
            )
            stats = result["outputs"]["stats"]
            print(f"      status:   {result['status']}")
            print(
                f"      NDVI mean={stats['mean']:.3f} "
                f"vegetation_fraction={stats['vegetation_fraction']:.3f}"
            )
            print(f"      raster:   {result['outputs']['ndvi_raster']}")
            summary = {
                "demo": "ogc-coverage",
                "ogc_endpoint": ogc_url,
                "remote": bool(remote),
                "skill": "coverage-ndvi",
                "stats": stats,
                "raster": result["outputs"]["ndvi_raster"],
            }
            print("\nOGC API - Coverages demo complete.")
            return summary
    finally:
        node_handle.stop()
        if ogc_handle is not None:
            ogc_handle.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OGC API - Coverages demo")
    parser.add_argument("--node-port", type=int, default=8787)
    parser.add_argument("--remote", default=None, help="Real OGC Coverages root")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    run(node_port=args.node_port, remote=args.remote, output=args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
