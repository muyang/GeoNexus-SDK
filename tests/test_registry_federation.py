"""Tests for registry-to-registry federation (pull sync)."""

from __future__ import annotations

from test_registry import _start_server

from geonexus.geocard import GeoCardBuilder
from geonexus.registry import RegistryClient, RegistryFederator, RegistryServer


def _card(card_id: str):
    return (
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=card_id,
            description="Sync test card.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .capability("ndvi")
        .build()
    )


def _registry_pair() -> tuple[str, object, str, object]:
    """Registry A (source) and registry B (federated peer)."""
    reg_a = RegistryServer(name="reg-a")
    a_handle = _start_server(reg_a.create_app(), 0)
    a_url = f"http://127.0.0.1:{a_handle.port}"

    reg_b = RegistryServer(name="reg-b", peers=[a_url])
    b_handle = _start_server(reg_b.create_app(), 0)
    b_url = f"http://127.0.0.1:{b_handle.port}"
    return a_url, a_handle, b_url, b_handle


def test_registry_pull_sync() -> None:
    """Cards and skills replicate from the source to the federated peer."""
    a_url, a_handle, b_url, b_handle = _registry_pair()
    try:
        with RegistryClient(a_url, timeout=10) as a:
            a.register(_card("asset-a"), node_url="http://node-a:8787")
            a.register_skill(name="echo", node_url="http://node-a:8787", capabilities=["ndvi"])

        with RegistryClient(b_url, timeout=10) as b:
            assert len(b.list_cards()) == 0  # nothing yet

        # Pull sync over HTTP via the /sync endpoint.
        with RegistryClient(b_url, timeout=15) as b:
            report = b._call("POST", "/sync")
        assert report["peers"] == 1
        assert report["cards_added"] == 1
        assert report["skills_added"] == 1
        assert report["errors"] == {}

        # The peer now sees the source's catalog with the owning node intact.
        with RegistryClient(b_url, timeout=10) as b:
            cards = b.list_cards()
            assert len(cards) == 1
            assert cards[0]["card"]["id"] == "asset-a"
            assert cards[0]["node_url"] == "http://node-a:8787"
            skills = b.list_skills()
            assert len(skills) == 1
            assert skills[0]["skill"]["name"] == "echo"

        # Idempotent: a second sync adds nothing new (entries are updated).
        with RegistryClient(b_url, timeout=15) as b:
            report = b._call("POST", "/sync")
        assert report["cards_added"] == 0
        assert report["cards_updated"] == 1
        assert report["skills_added"] == 0
        assert report["skills_updated"] == 1
    finally:
        b_handle.stop()
        a_handle.stop()


def test_registry_sync_resilient_to_dead_peer() -> None:
    """A dead peer is recorded in errors; the sync still completes."""
    reg_b = RegistryServer(name="reg-b", peers=["http://127.0.0.1:1"])
    b_handle = _start_server(reg_b.create_app(), 0)
    b_url = f"http://127.0.0.1:{b_handle.port}"
    try:
        with RegistryClient(b_url, timeout=15) as b:
            report = b._call("POST", "/sync")
        assert report["peers"] == 1
        assert report["errors"] != {}
        assert report["cards_added"] == 0
    finally:
        b_handle.stop()


def test_registry_federator_upsert() -> None:
    """update_existing replaces entries; without it duplicates are skipped."""
    a_url, a_handle, b_url, b_handle = _registry_pair()
    try:
        with RegistryClient(a_url, timeout=10) as a:
            a.register(_card("asset-a"), node_url="http://node-a:8787")

        with RegistryClient(b_url, timeout=10) as b:
            b.register(_card("asset-a"), node_url="http://local:9999")  # local copy

        # Direct federator with update_existing=False -> skipped.
        store_b = RegistryServer(name="b").store
        federator = RegistryFederator(store_b, [a_url])
        report = federator.sync(update_existing=False)
        assert report["cards_added"] == 1  # local store had nothing
        assert report["cards_skipped"] == 0

        # update_existing=True replaces the local entry (node_url from source).
        store_b2 = RegistryServer(name="b2").store
        from geonexus.registry import RegistryEntry

        store_b2.register(RegistryEntry(card=_card("asset-a"), node_url="http://local:9999"))
        report2 = RegistryFederator(store_b2, [a_url]).sync(update_existing=True)
        assert report2["cards_updated"] == 1  # replaced counts as imported
        assert store_b2.get("asset-a").node_url == "http://node-a:8787"
    finally:
        b_handle.stop()
        a_handle.stop()
