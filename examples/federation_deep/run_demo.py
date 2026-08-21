"""GeoNode-to-GeoNode federation demo (V1.0+): delegation + health.

Demonstrates node-level pushdown:

    client -> GeoNode B (relay, owns nothing)
               -> registry lookup for sentinel-2-amazon
               -> delegates geo.execute to GeoNode A (data owner)
               -> result relayed back through B

and health-aware discovery: after node A stops, the registry marks it
unhealthy and the federated client refuses to route to it.

Usage:
    python run_demo.py [--registry-port N] [--node-a-port N] [--node-b-port N]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from geonexus.federation import FederatedExecutionError, FederatedGeoMCPClient  # noqa: E402
from geonexus.geocard import GeoCardBuilder, load_geocard  # noqa: E402
from geonexus.geomcp import GeoMCPClient, GeoMCPClientError  # noqa: E402
from geonexus.geonode import GeoNode, Skill  # noqa: E402
from geonexus.registry import RegistryServer  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent
AMAZON_DIR = DEMO_DIR.parent / "amazon_ndvi"


def _echo_skill_handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
    """Returns which node actually ran the skill (proves delegation)."""
    return {"ran_on": context.node_name if context else "?", "echo": params}


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
    registry_port: int = 8790,
    node_a_port: int = 8787,
    node_b_port: int = 8788,
) -> dict:
    print("=" * 74)
    print("GeoNexus Reference Stack - GeoNode-to-GeoNode Federation (V1.0+)")
    print("Node-level delegation + health-aware discovery")
    print("=" * 74)

    # 1. Registry with health probing ---------------------------------------- #
    print("\n[1/6] Starting registry (health_probe enabled)")
    registry = RegistryServer(name="deep-registry", health_probe=True, health_cache_ttl=1.0)
    reg = _start_server(registry.create_app(), registry_port)
    reg_url = f"http://127.0.0.1:{reg.port if registry_port == 0 else registry_port}"

    # 2. Node A: data node (owns the card + the skill) ------------------------ #
    print("[2/6] Starting data node A")
    node_a = GeoNode(name="data-node-a", port=node_a_port)
    card = load_geocard(AMAZON_DIR / "geocard.yaml")
    node_a.register_geocard(card)
    node_a.register_skill_object(
        Skill(
            name="echo-skill",
            description="Echo with the executing node name.",
            input_schema={"required": []},
            handler=_echo_skill_handler,
            geocard=(
                GeoCardBuilder(
                    id="skill-echo",
                    type="skill",
                    name="Echo",
                    description="echo",
                )
                .capability("echo")
                .build()
            ),
        )
    )
    a_handle = _start_server(node_a.create_app(), node_a_port)
    a_url = f"http://127.0.0.1:{a_handle.port if node_a_port == 0 else node_a_port}"
    node_a.advertise(reg_url, endpoint=a_url)
    print(f"      node A {a_url} advertised card + skill")

    # 3. Node B: relay node (owns nothing, delegates via the registry) -------- #
    print("[3/6] Starting relay node B (no data, delegation enabled)")
    node_b = GeoNode(name="relay-node-b", port=node_b_port, registry_url=reg_url)
    b_handle = _start_server(node_b.create_app(), node_b_port)
    b_url = f"http://127.0.0.1:{b_handle.port if node_b_port == 0 else node_b_port}"
    print(f"      node B {b_url} (registry bound)")

    try:
        # 4. Execute through the relay: B delegates to A ----------------------- #
        print("[4/6] geo.execute to relay node B -> delegated to A")
        with GeoMCPClient(b_url, timeout=30) as client:
            result = client.execute(
                skill="echo-skill",
                geocards=["sentinel-2-amazon"],
                params={"hello": "world"},
                request_id="deep-demo-1",
            )
            print(f"      status: {result['status']} | executed_by: {result['executed_by']}")
            print(f"      outputs: {result['outputs']}")

        # 5. Health-aware discovery -------------------------------------------- #
        print("[5/6] Health-aware discovery via the registry")
        with FederatedGeoMCPClient(reg_url) as fed:
            discovered = fed.discover()
            for url, info in discovered["nodes"].items():
                print(f"      node {url} cards={info} healthy={discovered['health'].get(url)}")

            # 6. Stop node A -> registry marks it unhealthy --------------------- #
            print("[6/6] Stopping node A; registry health probe flips it")
            a_handle.stop()
            time.sleep(1.5)  # let the probe cache expire
            health = fed.node_health()
            a_health = health["nodes"].get(a_url, {}).get("healthy")
            print(f"      registry health for node A: {a_health}")
            try:
                fed.execute(
                    skill="echo-skill",
                    geocards=["sentinel-2-amazon"],
                    params={"hello": "after-stop"},
                    request_id="deep-demo-2",
                )
                print("      ERROR: unexpected success")
            except FederatedExecutionError as exc:
                print(f"      federated execute refused: {exc}")

            # Relay delegation now also fails with a clear error.
            try:
                with GeoMCPClient(b_url, timeout=10) as relay_client:
                    relay_client.execute(
                        skill="echo-skill",
                        geocards=["sentinel-2-amazon"],
                        params={"hello": "after-stop"},
                        request_id="deep-demo-3",
                    )
                print("      ERROR: relay unexpected success")
            except GeoMCPClientError as exc:
                print(f"      relay delegation refused: {exc}")

            print("\nGeoNode-to-GeoNode federation demo complete.")
            return {
                "demo": "federation-deep",
                "node_a": a_url,
                "node_b": b_url,
                "delegated_outputs": result["outputs"],
                "node_a_healthy_after_stop": a_health,
            }
    finally:
        b_handle.stop()
        if a_handle._thread.is_alive():
            a_handle.stop()
        reg.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GeoNode federation demo")
    parser.add_argument("--registry-port", type=int, default=8790)
    parser.add_argument("--node-a-port", type=int, default=8787)
    parser.add_argument("--node-b-port", type=int, default=8788)
    args = parser.parse_args(argv)
    run(
        registry_port=args.registry_port,
        node_a_port=args.node_a_port,
        node_b_port=args.node_b_port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
