"""Minimal embedded Local GeoNode example.

Registers a tiny skill (no rasters) and runs the node in-process, calling
it directly and over HTTP.

Run:
    python examples/local_node.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geonexus.geocard import GeoCardBuilder  # noqa: E402
from geonexus.geomcp.models import ExecuteParams  # noqa: E402
from geonexus.geonode import GeoNode  # noqa: E402


def hello_skill(params: dict, context) -> dict:
    """A trivial skill that greets the caller with the request context."""
    return {
        "greeting": f"Hello from GeoNode '{context.node_name}'!",
        "skill_param": params.get("who", "world"),
        "geocards": [c.id for c in context.geocards],
    }


def main() -> None:
    card = (
        GeoCardBuilder(
            id="example-demo-asset",
            type="data",
            name="Example asset",
            description="A tiny example GeoCard.",
        )
        .spatial(bbox=[-74, -16, -44, 6], crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .band("B04", dtype="uint16")
        .build()
    )

    node = GeoNode(name="local-node", port=8791)
    node.register_geocard(card)
    node.register_skill(
        name="hello",
        handler=hello_skill,
        description="Trivial greeting skill",
        input_schema={"required": []},
    )

    print("=== In-process call ===")
    result = node.execute(
        ExecuteParams(
            skill="hello",
            geocards=["example-demo-asset"],
            params={"who": "world"},
            request_id="example-1",
        )
    )
    print(result)

    print("\n=== Over HTTP ===")
    server = node.start_in_thread(port=8791)
    server.wait_until_ready()
    from geonexus.geomcp import GeoMCPClient

    with GeoMCPClient("http://127.0.0.1:8791") as client:
        print("health:", client.health()["status"])
        print("execute:", client.execute(skill="hello", params={"who": "HTTP"}))
    server.stop()


if __name__ == "__main__":
    main()
