"""Tests for Registry persistence and API-key auth (V1.0)."""

from __future__ import annotations

from test_registry import _start_server

from geonexus.geocard import GeoCardBuilder
from geonexus.registry import (
    RegistryClient,
    RegistryClientError,
    RegistryEntry,
    RegistryServer,
    SkillDescriptor,
    SkillEntry,
)


def _card(card_id: str = "persist-card"):
    return (
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=card_id,
            description="Persistence test card.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .capability("ndvi")
        .build()
    )


def test_registry_persistence(tmp_path) -> None:
    """Cards and skills survive a store restart via the JSON file."""
    persist = tmp_path / "registry.json"

    server = RegistryServer(name="p1", persist_path=str(persist))
    server.store.register(RegistryEntry(card=_card(), node_url="http://127.0.0.1:8787"))
    server.store.register_skill(
        SkillEntry(
            skill=SkillDescriptor(name="echo", capabilities=["ndvi"]),
            node_url="http://127.0.0.1:8787",
        )
    )
    assert persist.exists()

    # New server instance on the same file reloads everything.
    server2 = RegistryServer(name="p2", persist_path=str(persist))
    assert server2.store.count() == 1
    assert server2.store.get("persist-card") is not None
    assert len(server2.store.list_skills()) == 1
    assert server2.store.get_skill("echo") is not None

    # Unregister persists too.
    server2.store.unregister("persist-card")
    server3 = RegistryServer(name="p3", persist_path=str(persist))
    assert server3.store.count() == 0
    assert len(server3.store.list_skills()) == 1


def test_registry_auth() -> None:
    """Write endpoints require the API key; reads stay open."""
    server = RegistryServer(name="auth", api_keys={"secret-key"})
    running = _start_server(server.create_app(), 0)
    url = f"http://127.0.0.1:{running.port}"
    try:
        # Without a key: reads OK, writes 401.
        with RegistryClient(url, timeout=10) as client:
            assert client.health()["status"] == "ok"
            try:
                client.register(_card(), node_url="http://node")
                raise AssertionError("expected 401")
            except RegistryClientError as exc:
                assert exc.code == 401
            try:
                client.register_skill(name="echo", node_url="http://node")
                raise AssertionError("expected 401")
            except RegistryClientError as exc:
                assert exc.code == 401

        # With the key: writes succeed.
        with RegistryClient(url, timeout=10, api_key="secret-key") as client:
            client.register(_card(), node_url="http://node")
            client.register_skill(name="echo", node_url="http://node")
            assert len(client.list_cards()) == 1
            assert len(client.list_skills()) == 1

        # Wrong key still rejected.
        with RegistryClient(url, timeout=10, api_key="wrong") as client:
            try:
                client.register_skill(name="x", node_url="http://node")
                raise AssertionError("expected 401")
            except RegistryClientError as exc:
                assert exc.code == 401
    finally:
        running.stop()
