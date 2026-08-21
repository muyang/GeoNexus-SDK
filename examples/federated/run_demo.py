"""Federated execution demo: registry + data node + pushdown.

Demonstrates V0.3 federation:

    Client
      |  discovery: "who owns sentinel-2-amazon?"
      v
    GeoCard Registry  -------------------------->  Node A (owns card + skill + data)
      |  returns node_url
      v
    Client routes geo.execute to Node A (pushdown: computation moves to the data)

All rasters are **synthetic** (see examples/amazon_ndvi/skill.py).

Usage:
    python run_demo.py [--registry-port N] [--node-port N] [--output DIR]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from examples.amazon_ndvi import skill as ndvi_skill  # noqa: E402
from geonexus.federation import FederatedGeoMCPClient  # noqa: E402
from geonexus.geocard import load_geocard  # noqa: E402
from geonexus.geonode import GeoNode, Skill  # noqa: E402
from geonexus.registry import RegistryServer  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent
AMAZON_DIR = DEMO_DIR.parent / "amazon_ndvi"


def run(
    registry_port: int = 8790,
    node_port: int = 8787,
    output_dir: str | None = None,
) -> dict:
    print("=" * 74)
    print("GeoNexus Reference Stack - Federated Execution Demo (V0.3)")
    print("Registry + data node + pushdown routing (SYNTHETIC data)")
    print("=" * 74)

    out_dir = Path(output_dir) if output_dir else DEMO_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Start the shared GeoCard Registry ---------------------------------- #
    print(f"\n[1/6] Starting GeoCard Registry on 127.0.0.1:{registry_port}")
    registry = RegistryServer(name="demo-registry")
    reg_running = _start_server(registry.create_app(), registry_port)
    reg_url = f"http://127.0.0.1:{reg_running.port if registry_port == 0 else registry_port}"

    # 2. Start the data node (owns the card, the skill and the data) -------- #
    print(f"[2/6] Starting data GeoNode on 127.0.0.1:{node_port}")
    node = GeoNode(name="data-node", port=node_port, workdir=str(out_dir))
    card = load_geocard(AMAZON_DIR / "geocard.yaml")
    skill = Skill(
        name="ndvi-analysis",
        description="Compute NDVI from red/NIR rasters (synthetic data).",
        input_schema={"required": ["red", "nir"]},
        output_schema={},
        handler=ndvi_skill.ndvi_analysis_handler,
    )
    node.register_geocard(card)
    node.register_skill_object(skill)
    node_running = _start_server(node.create_app(), node_port)
    node_url = f"http://127.0.0.1:{node_running.port if node_port == 0 else node_port}"

    # 3. The node advertises its cards at the registry ----------------------- #
    print(f"[3/6] Node advertises cards at {reg_url}")
    node.advertise(reg_url, endpoint=node_url)
    print(f"      advertised: {[c.id for c in node.geocard_registry.list_cards()]}")

    try:
        # 4. Federated discovery --------------------------------------------- #
        print(f"[4/6] Federated discovery via {reg_url}")
        with FederatedGeoMCPClient(reg_url) as client:
            discovered = client.discover()
            for node_endpoint, cards in discovered["nodes"].items():
                print(f"      node {node_endpoint} owns: {cards}")

            hits = client.search(
                capability="ndvi",
                bbox=[-73.9, -15.0, -44.0, 5.0],
                crs="EPSG:4326",
                start="2020-01-01",
                end="2025-01-01",
                required_bands=["B04", "B08"],
            )
            print(f"      contract-gated search: {len(hits)} card(s) match")
            for hit in hits:
                print(
                    f"        - {hit['entry']['card']['id']} "
                    f"-> {hit['entry']['node_url']} "
                    f"(satisfied={hit['contract']['satisfied']})"
                )

            # 5. Pushdown execution ------------------------------------------ #
            print(f"[5/6] Pushdown execution: routing geo.execute to {node_url}")
            spatial = {"bbox": [-73.9, -15.0, -44.0, 5.0], "crs": "EPSG:4326"}
            stats_by_year = {}
            for year in (2015, 2025):
                scenes = ndvi_skill.generate_synthetic_scene(year, out_dir, seed=year)
                ndvi_out = out_dir / f"ndvi_{year}.tif"
                result = client.execute(
                    skill="ndvi-analysis",
                    geocards=["sentinel-2-amazon"],
                    spatial=spatial,
                    temporal={"start": f"{year}-01-01", "end": f"{year}-12-31"},
                    params={
                        "red": scenes["red"],
                        "nir": scenes["nir"],
                        "output": str(ndvi_out),
                    },
                    request_id=f"federated-{year}",
                )
                assert result["status"] == "ok"
                stats = result["outputs"]["stats"]
                stats_by_year[year] = stats
                print(
                    f"      {year}: NDVI mean={stats['mean']:.3f} "
                    f"(executed_by={result['executed_by']})"
                )

            # 6. Change raster + summary -------------------------------------- #
            print("[6/6] Change raster and summary")
            change = ndvi_skill.compute_change(
                str(out_dir / "ndvi_2015.tif"),
                str(out_dir / "ndvi_2025.tif"),
                str(out_dir / "ndvi_change.tif"),
            )
            print(f"      mean NDVI change: {change['mean']:.3f} (vegetation declined)")
            print(f"\n      Outputs (all SYNTHETIC) in {out_dir}:")
            for name in ("ndvi_2015.tif", "ndvi_2025.tif", "ndvi_change.tif"):
                print(f"        - {out_dir / name}")

            summary = {
                "demo": "federated",
                "synthetic": True,
                "registry": reg_url,
                "node": node_url,
                "stats_2015": stats_by_year[2015],
                "stats_2025": stats_by_year[2025],
                "change_mean": change["mean"],
            }
            return summary
    finally:
        node_running.stop()
        reg_running.stop()


class _ServerHandle:
    """Handle for a FastAPI app running in a background thread."""

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
    """Start a FastAPI app in a thread and wait until ready."""
    import threading

    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return _ServerHandle(server, thread).wait_ready()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Federated execution demo")
    parser.add_argument("--registry-port", type=int, default=8790)
    parser.add_argument("--node-port", type=int, default=8787)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    run(
        registry_port=args.registry_port,
        node_port=args.node_port,
        output_dir=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
