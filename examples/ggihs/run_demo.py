"""GGIHS demo (V1.0+): cross-registry health/catalog aggregation.

Topology:

    node A (data) -> registry A  --peer-->  registry B (federated, synced)
    node B (data) -> registry B
                          \         /
                     GGIHS aggregates A and B
                       /summary /nodes /catalog

Steps: start everything, show the aggregated view; stop node A and show the
health flip while the catalog keeps listing its card (owner + healthy=false).

Usage:
    python run_demo.py [--registry-a-port N] [--registry-b-port N]
                       [--node-a-port N] [--node-b-port N] [--ggihs-port N]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from geonexus.geocard import GeoCardBuilder  # noqa: E402
from geonexus.geonode import GeoNode, Skill  # noqa: E402
from geonexus.ggihs import GGIHSService  # noqa: E402
from geonexus.registry import RegistryServer  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent


def _echo_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    return {"ran_on": context.node_name if context else "?", "echo": params}


def _make_node(name: str, card_id: str, port: int) -> GeoNode:
    node = GeoNode(name=name, port=port)
    node.register_geocard(
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=card_id,
            description=f"Card owned by {name}.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .capability("ndvi")
        .build()
    )
    node.register_skill_object(
        Skill(
            name="echo",
            description="echo",
            input_schema={"required": []},
            handler=_echo_handler,
            geocard=(
                GeoCardBuilder(
                    id=f"skill-{card_id}",
                    type="skill",
                    name="echo",
                    description="echo",
                )
                .capability("echo")
                .build()
            ),
        )
    )
    return node


class _ServerHandle:
    def __init__(self, server, thread) -> None:
        self._server = server
        self._thread = thread

    def wait_ready(self, timeout: float = 15.0) -> _ServerHandle:
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


def run(
    registry_a_port: int = 8790,
    registry_b_port: int = 8791,
    node_a_port: int = 8787,
    node_b_port: int = 8788,
    ggihs_port: int = 8800,
) -> dict:
    print("=" * 74)
    print("GeoNexus Reference Stack - GGIHS Demo (V1.0+)")
    print("Cross-registry health/catalog aggregation")
    print("=" * 74)

    # 1. Registries: A (primary) and B (federated peer of A) ------------------ #
    print("\n[1/6] Starting registries A and B (B peers A)")
    reg_a = RegistryServer(name="reg-a", health_probe=True, health_cache_ttl=1.0)
    a_reg_handle = _start_server(reg_a.create_app(), registry_a_port)
    a_reg_url = f"http://127.0.0.1:{a_reg_handle.port if registry_a_port == 0 else registry_a_port}"
    reg_b = RegistryServer(name="reg-b", health_probe=True, health_cache_ttl=1.0, peers=[a_reg_url])
    b_reg_handle = _start_server(reg_b.create_app(), registry_b_port)
    b_reg_url = f"http://127.0.0.1:{b_reg_handle.port if registry_b_port == 0 else registry_b_port}"
    print(f"      registry A {a_reg_url} | registry B {b_reg_url} (peer of A)")

    # 2. Nodes ----------------------------------------------------------------- #
    print("[2/6] Starting data nodes A and B")
    node_a = _make_node("ggihs-node-a", "asset-a", node_a_port)
    a_node_handle = _start_server(node_a.create_app(), node_a_port)
    a_node_url = f"http://127.0.0.1:{a_node_handle.port if node_a_port == 0 else node_a_port}"
    node_a.advertise(a_reg_url, endpoint=a_node_url)

    node_b = _make_node("ggihs-node-b", "asset-b", node_b_port)
    b_node_handle = _start_server(node_b.create_app(), node_b_port)
    b_node_url = f"http://127.0.0.1:{b_node_handle.port if node_b_port == 0 else node_b_port}"
    node_b.advertise(b_reg_url, endpoint=b_node_url)

    # 3. Registry federation: B pulls A's catalog ------------------------------- #
    print("[3/6] Syncing registry B from peer A")
    report = reg_b.sync_peers()
    print(
        f"      B synced: +{report['cards_added']} cards, +{report['skills_added']} skills from A"
    )

    # 4. GGIHS aggregation ------------------------------------------------------ #
    print("[4/6] Starting GGIHS aggregating both registries")
    ggihs = GGIHSService([a_reg_url, b_reg_url], name="demo-ggihs")
    g_handle = _start_server(ggihs.create_app(), ggihs_port)
    g_url = f"http://127.0.0.1:{g_handle.port if ggihs_port == 0 else ggihs_port}"

    try:
        with httpx.Client(base_url=g_url, timeout=15) as client:
            # 5. Aggregated views ------------------------------------------------ #
            summary = client.get("/summary").json()
            print(
                f"[5/6] GGIHS summary: nodes={summary['nodes']} "
                f"(healthy={summary['nodes_healthy']}) "
                f"cards={summary['cards']} skills={summary['skills']}"
            )

            catalog = client.get("/catalog").json()
            card_ids = sorted(c["id"] for c in catalog["cards"])
            print(
                f"      catalog cards: {card_ids} (from both registries, "
                f"incl. asset-a via B's sync)"
            )
            assert "asset-a" in card_ids and "asset-b" in card_ids

            node_view = client.get("/nodes").json()
            for url, info in node_view["nodes"].items():
                print(f"      node {url} cards={info['cards']} healthy={info['healthy']}")

            # 6. Stop node A -> health flips, catalog keeps the entry ------------- #
            print("[6/6] Stopping node A; health flips, catalog survives")
            a_node_handle.stop()
            time.sleep(1.5)
            summary2 = client.get("/summary").json()
            print(
                f"      summary after stop: nodes={summary2['nodes']} "
                f"healthy={summary2['nodes_healthy']} "
                f"unhealthy={summary2['nodes_unhealthy']}"
            )
            catalog2 = client.get("/catalog").json()
            asset_a = next(c for c in catalog2["cards"] if c["id"] == "asset-a")
            print(f"      asset-a still cataloged, healthy={asset_a['healthy']}")

            # Dashboard page.
            dashboard = client.get("/").text
            print(f"      dashboard: {g_url}/ (HTML, ok={'federation dashboard' in dashboard})")

            print("\nGGIHS demo complete.")
            return {
                "demo": "ggihs",
                "summary_before": summary,
                "summary_after": summary2,
                "catalog_ids": card_ids,
            }
    finally:
        b_node_handle.stop()
        a_node_handle.stop()
        g_handle.stop()
        b_reg_handle.stop()
        a_reg_handle.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GGIHS demo")
    parser.add_argument("--registry-a-port", type=int, default=8790)
    parser.add_argument("--registry-b-port", type=int, default=8791)
    parser.add_argument("--node-a-port", type=int, default=8787)
    parser.add_argument("--node-b-port", type=int, default=8788)
    parser.add_argument("--ggihs-port", type=int, default=8800)
    args = parser.parse_args(argv)
    run(
        registry_a_port=args.registry_a_port,
        registry_b_port=args.registry_b_port,
        node_a_port=args.node_a_port,
        node_b_port=args.node_b_port,
        ggihs_port=args.ggihs_port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
