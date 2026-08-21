"""CLI coverage for service-facing commands (registry/node interactions)."""

from __future__ import annotations

from test_registry import _start_server

from geonexus.cli import main
from geonexus.geocard import GeoCardBuilder
from geonexus.geonode import GeoNode, Skill
from geonexus.registry import RegistryServer

DEMO_CARD = "examples/amazon_ndvi/geocard.yaml"


def _node_with_skill() -> GeoNode:
    node = GeoNode(name="cli-node", port=0)
    node.register_skill_object(
        Skill(
            name="cli-echo",
            description="echo",
            input_schema={"required": []},
            handler=lambda params, context: {"ran_on": context.node_name},
        )
    )
    return node


def test_cli_registry_register_search_skill(tmp_path, capsys) -> None:
    """registry register / search / skill register / skill list."""
    registry = RegistryServer(name="cli-reg")
    handle = _start_server(registry.create_app(), 0)
    url = f"http://127.0.0.1:{handle.port}"
    try:
        assert main(["registry", "register", DEMO_CARD, "--url", url]) == 0
        out = capsys.readouterr().out
        assert "Registered 'sentinel-2-amazon'" in out

        assert main(["registry", "search", "--url", url, "--capability", "ndvi"]) == 0
        assert "sentinel-2-amazon" in capsys.readouterr().out

        assert (
            main(
                ["registry", "skill", "register", "cli-skill", "--url", url, "--capability", "ndvi"]
            )
            == 0
        )
        assert "Registered skill 'cli-skill'" in capsys.readouterr().out

        assert main(["registry", "skill", "list", "--url", url]) == 0
        assert "cli-skill" in capsys.readouterr().out
    finally:
        handle.stop()


def test_cli_skill_list_and_inspector(capsys) -> None:
    """skill list and inspector against a live node."""
    node = _node_with_skill()
    node_handle = _start_server(node.create_app(), 0)
    url = f"http://127.0.0.1:{node_handle.port}"
    try:
        assert main(["skill", "list", "--url", url]) == 0
        assert "cli-echo" in capsys.readouterr().out

        assert main(["inspector", url]) == 0
        out = capsys.readouterr().out
        assert "GeoMCP Inspector" in out
        assert "cli-echo" in out

        assert main(["inspector", url, "--json"]) == 0
        assert '"node"' in capsys.readouterr().out
    finally:
        node_handle.stop()


def test_cli_registry_sync(capsys) -> None:
    """registry sync pulls a peer's catalog."""
    reg_a = RegistryServer(name="a")
    a_handle = _start_server(reg_a.create_app(), 0)
    a_url = f"http://127.0.0.1:{a_handle.port}"
    reg_b = RegistryServer(name="b", peers=[a_url])
    b_handle = _start_server(reg_b.create_app(), 0)
    b_url = f"http://127.0.0.1:{b_handle.port}"
    try:
        from geonexus.registry import RegistryClient

        card = (
            GeoCardBuilder(id="sync-card", type="data", name="S", description="d")
            .capability("ndvi")
            .build()
        )
        with RegistryClient(a_url) as a:
            a.register(card, node_url="http://node:8787")

        assert main(["registry", "sync", "--url", b_url]) == 0
        out = capsys.readouterr().out
        assert "cards_added:" in out

        with RegistryClient(b_url) as b:
            assert len(b.list_cards()) == 1
    finally:
        b_handle.stop()
        a_handle.stop()


def test_cli_agent_run_error_no_capability(capsys) -> None:
    """agent run against an empty registry fails cleanly."""
    registry = RegistryServer(name="empty")
    handle = _start_server(registry.create_app(), 0)
    url = f"http://127.0.0.1:{handle.port}"
    try:
        assert main(["agent", "run", "--registry", url, "--capability", "ndvi"]) == 1
        assert "No skill provides capability" in capsys.readouterr().err
    finally:
        handle.stop()


def test_cli_registry_search_bad_bbox(capsys) -> None:
    """registry search rejects a malformed bbox."""
    assert main(["registry", "search", "--url", "http://127.0.0.1:1", "--bbox", "not,numbers"]) == 1
    assert "must be 'w,s,e,n'" in capsys.readouterr().err
