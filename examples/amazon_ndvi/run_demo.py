"""Run the Amazon NDVI demo end-to-end.

Demonstrates the full GeoNexus core loop:

    GeoCard -> Registry/Discovery -> Contract validation -> GeoMCP
        -> Local GeoNode -> GeoSkill -> Result

Uses **synthetic** raster data (clearly labelled); all output GeoTIFFs are
tagged ``GEONEXUS_SYNTHETIC=TRUE``.

Usage:
    python run_demo.py [--output DIR] [--port N] [--no-server]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Allow running as a plain script (``python run_demo.py``) and as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from examples.amazon_ndvi import skill as ndvi_skill  # noqa: E402
from geonexus import __version__  # noqa: E402
from geonexus.geocard import ContractValidator, GeoCard, GeoCardBuilder, load_geocard  # noqa: E402
from geonexus.geomcp import GeoMCPClient, GeoMCPClientError, GeoMCPProtocolError  # noqa: E402
from geonexus.geonode import GeoNode, Skill  # noqa: E402

logger = logging.getLogger("geonexus.demo.amazon_ndvi")

DEMO_DIR = Path(__file__).resolve().parent


def build_skill_geocard() -> GeoCard:
    """Build the GeoCard describing the ndvi-analysis skill itself."""
    return (
        GeoCardBuilder(
            id="skill-ndvi-analysis",
            type="skill",
            name="NDVI Analysis Skill",
            description="Computes NDVI (normalized difference vegetation index) "
            "from red/NIR rasters. Demo skill operating on synthetic data.",
        )
        .capability("ndvi")
        .input("nir", "raster", description="Near-infrared band raster", required=True)
        .input("red", "raster", description="Red band raster", required=True)
        .output("ndvi_raster", "raster", description="NDVI GeoTIFF")
        .output("stats", "object", description="Summary statistics")
        .interface(type="geomcp-skill", version="1.0")
        .build()
    )


def build_ndvi_skill() -> Skill:
    """Build the ndvi-analysis Skill object."""
    return Skill(
        name="ndvi-analysis",
        description="Compute NDVI from red/NIR rasters and summarize statistics.",
        input_schema={
            "type": "object",
            "required": ["red", "nir"],
            "properties": {
                "red": {"type": "string"},
                "nir": {"type": "string"},
                "output": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "properties": {
                "ndvi_raster": {"type": "string"},
                "stats": {"type": "object"},
            },
        },
        handler=ndvi_skill.ndvi_analysis_handler,
        geocard=build_skill_geocard(),
    )


def _print_banner() -> None:
    print("=" * 74)
    print("GeoNexus Reference Stack MVP - Amazon NDVI Demo")
    print("Vegetation change analysis in the Amazon rainforest (SYNTHETIC data)")
    print(f"geonexus version {__version__}")
    print("=" * 74)


def run(
    output_dir: str | None = None,
    port: int = 8787,
    use_server: bool = True,
    geocard_path: str | None = None,
) -> dict[str, Any]:
    """Execute the complete demo and return a summary dict.

    Args:
        output_dir: Directory for generated rasters (default: ./output).
        port: Port for the in-demo GeoNode HTTP server.
        use_server: When True, run the node over real HTTP (uvicorn thread)
            and talk to it with GeoMCPClient. When False, call the node
            in-process.
        geocard_path: Path to the demo GeoCard YAML (default: bundled one).
    """
    _print_banner()

    out_dir = Path(output_dir) if output_dir else DEMO_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load GeoCard and 2. validate it ------------------------------------ #
    card_path = Path(geocard_path) if geocard_path else DEMO_DIR / "geocard.yaml"
    print(f"\n[1/8] Loading GeoCard from {card_path}")
    card = load_geocard(card_path)
    card.validate()
    print(
        f"      GeoCard '{card.id}' ({card.type}) validated against the "
        f"official schema (geocard_version={card.geocard_version})"
    )

    # 3. Register GeoSkill --------------------------------------------------- #
    print("\n[2/8] Building and registering GeoSkill 'ndvi-analysis'")
    skill = build_ndvi_skill()
    print(f"      Skill inputs: {list(skill.input_schema.get('properties', {}))}")

    # 4. Start Local GeoNode ------------------------------------------------- #
    print("\n[3/8] Starting Local GeoNode")
    node = GeoNode(name="local-node", port=port, workdir=str(out_dir))
    node.register_geocard(card)
    if skill.geocard is not None:
        node.register_geocard(skill.geocard)
    node.register_skill_object(skill)
    print(f"      GeoCard registry: {node.geocard_registry.count()} card(s)")
    print(f"      Skill registry:   {node.skill_registry.count()} skill(s)")

    running = None
    if use_server:
        running = node.start_in_thread(host="127.0.0.1", port=port)
        running.wait_until_ready()
        actual_port = running.port if port == 0 else port
        base_url = f"http://127.0.0.1:{actual_port}"
        print(f"      GeoMCP server listening on {base_url}")
        client = GeoMCPClient(base_url)
    else:
        actual_port = None
        client = None

    try:
        # 5. Send GeoMCP request --------------------------------------------- #
        if use_server:
            assert client is not None
            print("\n[4/8] GeoMCP over HTTP")
            print(f"      GET {base_url}/health   -> {client.health()['status']}")
            caps = client.capabilities()
            print(
                f"      GET /capabilities       -> methods={caps['methods']}, "
                f"skills={[s['name'] for s in caps['skills']]}"
            )
            described = client.describe(geocards=["sentinel-2-amazon"])
            print(
                f"      geo.describe            -> {described['geocards'][0]['id']} "
                f"({described['geocards'][0]['type']})"
            )
        else:
            print("\n[4/8] GeoMCP (in-process)")
            print(f"      health                  -> {node.health()['status']}")

        # Contract validation gate ------------------------------------------- #
        print("\n[5/8] Contract validation (registry/discovery -> contract)")
        validator = ContractValidator()
        contract_result = validator.check(
            card,
            bbox=[-73.9, -15.0, -44.0, 5.0],
            crs="EPSG:4326",
            start="2020-01-01",
            end="2025-01-01",
            required_bands=["B04", "B08"],
            required_resolution=10,
        )
        print(f"      satisfied={contract_result.satisfied}")
        for reason in contract_result.reasons:
            print(f"        + {reason}")
        for warning in contract_result.warnings:
            print(f"        ! {warning}")

        # 6. Execute NDVI analysis ------------------------------------------- #
        print("\n[6/8] Generating SYNTHETIC scenes and executing NDVI analysis")
        scenes = {}
        for year in (2015, 2025):
            scenes[year] = ndvi_skill.generate_synthetic_scene(year, out_dir)
        print(f"      Synthetic red/NIR pairs written to {out_dir}")

        stats_by_year: dict[int, dict[str, Any]] = {}
        for year in (2015, 2025):
            ndvi_out = out_dir / f"ndvi_{year}.tif"
            params = {
                "red": scenes[year]["red"],
                "nir": scenes[year]["nir"],
                "output": str(ndvi_out),
            }
            if use_server:
                assert client is not None
                result = client.execute(
                    skill="ndvi-analysis",
                    geocards=["sentinel-2-amazon"],
                    spatial={"bbox": [-73.9, -15.0, -44.0, 5.0], "crs": "EPSG:4326"},
                    temporal={"start": f"{year}-01-01", "end": f"{year}-12-31"},
                    params=params,
                    request_id=f"demo-{year}",
                )
            else:
                from geonexus.geomcp.models import (
                    ExecuteParams,
                    SpatialContext,
                    TemporalContext,
                )

                result = node.execute(
                    ExecuteParams(
                        skill="ndvi-analysis",
                        geocards=["sentinel-2-amazon"],
                        spatial=SpatialContext(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326"),
                        temporal=TemporalContext(start=f"{year}-01-01", end=f"{year}-12-31"),
                        params=params,
                        request_id=f"demo-{year}",
                    )
                )
            assert result["status"] == "ok", result
            stats = result["outputs"]["stats"]
            stats_by_year[year] = stats
            print(
                f"      {year}: NDVI mean={stats['mean']:.3f} "
                f"vegetation_fraction={stats['vegetation_fraction']:.3f} "
                f"-> {ndvi_out}"
            )

        # 7. Generate output raster (change) ---------------------------------- #
        print("\n[7/8] Generating NDVI change raster (2025 - 2015)")
        change_path = out_dir / "ndvi_change.tif"
        change_stats = ndvi_skill.compute_change(
            str(out_dir / "ndvi_2015.tif"),
            str(out_dir / "ndvi_2025.tif"),
            str(change_path),
        )
        print(f"      mean NDVI change: {change_stats['mean']:.3f} -> {change_path}")

        # 8. Summary statistics ----------------------------------------------- #
        print("\n[8/8] Summary statistics (SYNTHETIC data)")
        print(f"      {'metric':<22}{'2015':>10}{'2025':>10}{'change':>10}")
        for metric in ("mean", "median", "std", "p5", "p95", "vegetation_fraction"):
            a = stats_by_year[2015].get(metric)
            b = stats_by_year[2025].get(metric)
            if a is None or b is None:
                continue
            print(f"      {metric:<22}{a:>10.4f}{b:>10.4f}{b - a:>10.4f}")

        summary = {
            "demo": "amazon-ndvi",
            "synthetic": True,
            "geocard": card.id,
            "skill": skill.name,
            "outputs": {
                "ndvi_2015": str(out_dir / "ndvi_2015.tif"),
                "ndvi_2025": str(out_dir / "ndvi_2025.tif"),
                "ndvi_change": str(change_path),
            },
            "stats_2015": stats_by_year[2015],
            "stats_2025": stats_by_year[2025],
            "change": change_stats,
            "protocol": "geomcp-http" if use_server else "in-process",
        }
        summary_path = out_dir / "demo_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\n      Summary written to {summary_path}")

        # Demonstrate contract gating with an out-of-contract request. -------- #
        print("\nContract gate demonstration (expected refusal):")
        bad_bbox = [100.0, 100.0, 120.0, 120.0]  # far outside the card bbox
        try:
            if use_server:
                assert client is not None
                client.execute(
                    skill="ndvi-analysis",
                    geocards=["sentinel-2-amazon"],
                    spatial={"bbox": bad_bbox, "crs": "EPSG:4326"},
                    temporal={"start": "2020-01-01", "end": "2025-01-01"},
                    params={"red": scenes[2015]["red"], "nir": scenes[2015]["nir"]},
                    request_id="demo-out-of-contract",
                )
            else:
                from geonexus.geomcp.models import (
                    ExecuteParams,
                    SpatialContext,
                    TemporalContext,
                )

                node.execute(
                    ExecuteParams(
                        skill="ndvi-analysis",
                        geocards=["sentinel-2-amazon"],
                        spatial=SpatialContext(bbox=bad_bbox, crs="EPSG:4326"),
                        temporal=TemporalContext(start="2020-01-01", end="2025-01-01"),
                        params={"red": scenes[2015]["red"], "nir": scenes[2015]["nir"]},
                        request_id="demo-out-of-contract",
                    )
                )
            print("      ERROR: request unexpectedly succeeded")
            summary["contract_gate"] = "unexpected-success"
        except GeoMCPClientError as exc:
            print(f"      GeoMCP refused with code {exc.code}: {exc.message}")
            summary["contract_gate"] = {"code": exc.code, "message": exc.message}
        except GeoMCPProtocolError as exc:
            # In-process mode: the node raises the protocol error directly.
            print(f"      GeoMCP refused with code {exc.code}: {exc.message}")
            summary["contract_gate"] = {"code": exc.code, "message": exc.message}
        finally:
            if client is not None:
                client.close()

        print("\nDemo complete. Output GeoTIFFs (all SYNTHETIC):")
        for name in ("ndvi_2015.tif", "ndvi_2025.tif", "ndvi_change.tif"):
            print(f"  - {out_dir / name}")
        return summary
    finally:
        if running is not None:
            running.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Amazon NDVI demo (synthetic data)")
    parser.add_argument("--output", default=None, help="Output directory")
    parser.add_argument("--port", type=int, default=8787, help="GeoMCP port")
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Run in-process without an HTTP server",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    run(
        output_dir=args.output,
        port=args.port,
        use_server=not args.no_server,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
