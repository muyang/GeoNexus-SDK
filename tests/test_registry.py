"""Tests for the shared GeoCard Registry (service, store, client)."""

from __future__ import annotations

from geonexus.geocard import GeoCardBuilder
from geonexus.registry import RegistryClient, RegistryClientError, RegistryServer


def _card(card_id: str = "asset-1", capability: str = "ndvi"):
    builder = (
        GeoCardBuilder(
            id=card_id,
            type="data",
            name=card_id,
            description="Test asset.",
        )
        .spatial(bbox=[-73.9, -15.0, -44.0, 5.0], crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .band("B04")
        .band("B08")
    )
    if capability:
        builder.capability(capability)
    return builder.build()


def test_registry_store_basics() -> None:
    """Register / get / list / unregister and duplicate conflict."""
    from geonexus.registry import RegistryEntry
    from geonexus.registry.store import RegistryEntryConflict, RegistryEntryNotFound

    server = RegistryServer(name="test")
    store = server.store
    store.register(RegistryEntry(card=_card(), node_url="http://127.0.0.1:8787"))

    assert store.count() == 1
    assert store.get("asset-1").node_url == "http://127.0.0.1:8787"
    assert len(store.list_entries()) == 1

    try:
        store.register(RegistryEntry(card=_card(), node_url="http://other"))
        raise AssertionError("expected conflict")
    except RegistryEntryConflict:
        pass

    store.unregister("asset-1")
    assert store.count() == 0
    try:
        store.unregister("asset-1")
        raise AssertionError("expected not found")
    except RegistryEntryNotFound:
        pass


def test_registry_search_contract_gate() -> None:
    """Search pre-filters by contract; non-matching cards are excluded."""
    from geonexus.registry import RegistryEntry

    server = RegistryServer(name="test")
    store = server.store
    store.register(RegistryEntry(card=_card("asset-ok"), node_url="http://node-a"))
    narrow = (
        GeoCardBuilder(
            id="asset-narrow",
            type="data",
            name="asset-narrow",
            description="Narrow test asset.",
        )
        .spatial(bbox=[10.0, 10.0, 20.0, 20.0], crs="EPSG:4326", resolution=10)
        .temporal(start="2015-01-01", end="2025-12-31")
        .capability("ndvi")
        .build()
    )
    store.register(RegistryEntry(card=narrow, node_url="http://node-b"))

    hits = store.search(
        capability="ndvi",
        bbox=[-70.0, -10.0, -50.0, 0.0],
        crs="EPSG:4326",
        start="2020-01-01",
        end="2025-01-01",
        required_bands=["B04", "B08"],
    )
    ids = {h.entry.card.id for h in hits}
    assert ids == {"asset-ok"}  # asset-narrow's bbox does not intersect

    # Contract result is attached to each hit.
    hit = next(h for h in hits if h.entry.card.id == "asset-ok")
    assert hit.contract is not None and hit.contract.satisfied

    # Capability filter alone (no contract constraints).
    assert {h.entry.card.id for h in store.search(capability="ndvi")} == {
        "asset-ok",
        "asset-narrow",
    }


def test_registry_http(tmp_path) -> None:
    """The registry service works over real HTTP with the client."""
    server = RegistryServer(name="http-test")
    running = _start_server(server.create_app(), 0)
    url = f"http://127.0.0.1:{running.port}"
    try:
        with RegistryClient(url, timeout=10) as client:
            assert client.health()["status"] == "ok"

            client.register(_card("asset-1"), node_url="http://127.0.0.1:8787")
            client.register(_card("asset-2"), node_url="http://127.0.0.1:8788")

            got = client.get("asset-1")
            assert got["node_url"] == "http://127.0.0.1:8787"

            assert len(client.list_cards()) == 2

            hits = client.search(
                capability="ndvi",
                bbox=[-70.0, -10.0, -50.0, 0.0],
                crs="EPSG:4326",
                start="2020-01-01",
                end="2025-01-01",
                required_bands=["B04", "B08"],
            )
            assert {h["entry"]["card"]["id"] for h in hits} == {"asset-1", "asset-2"}

            # Duplicate registration -> HTTP 409 surfaced as client error.
            try:
                client.register(_card("asset-1"), node_url="http://127.0.0.1:8787")
                raise AssertionError("expected duplicate error")
            except RegistryClientError as exc:
                assert exc.code == 409

            client.unregister("asset-1")
            assert len(client.list_cards()) == 1
    finally:
        running.stop()


def _start_server(app, port: int):
    import threading
    import time

    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise TimeoutError("server not ready")

    class _Handle:
        @property
        def port(self) -> int:
            return server.servers[0].sockets[0].getsockname()[1]

        def stop(self, timeout: float = 10.0) -> None:
            server.should_exit = True
            thread.join(timeout=timeout)

    return _Handle()
