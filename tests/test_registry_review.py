"""Tests for the registry review workflow (pending / approve / reject)."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from geonexus.geocard import GeoCardBuilder
from geonexus.registry import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    RegistryClient,
    RegistryServer,
    RegistryStore,
)


def _card(card_id: str = "data-1") -> Any:
    return (
        GeoCardBuilder(card_id, "data", f"Data {card_id}", "test dataset")
        .capability("ndvi")
        .build()
    )


def _start_server(server: RegistryServer, port: int) -> str:
    import uvicorn

    config = uvicorn.Config(server.create_app(), host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if srv.started:
            break
        time.sleep(0.05)
    else:
        raise TimeoutError("registry not ready")
    return f"http://127.0.0.1:{srv.servers[0].sockets[0].getsockname()[1]}"


class TestStoreStatus:
    def test_default_approved(self) -> None:
        store = RegistryStore()
        from geonexus.registry.models import RegistryEntry

        store.register(RegistryEntry(card=_card(), node_url="http://n:1"))
        assert store.get("data-1").status == STATUS_APPROVED

    def test_set_status_and_filter(self) -> None:
        store = RegistryStore()
        from geonexus.registry.models import RegistryEntry

        store.register(RegistryEntry(card=_card("a"), node_url="http://n:1"))
        store.register(RegistryEntry(card=_card("b"), node_url="http://n:1", status=STATUS_PENDING))
        store.register(RegistryEntry(card=_card("c"), node_url="http://n:1", status=STATUS_REJECTED))

        assert [e.card.id for e in store.list_entries(status=STATUS_APPROVED)] == ["a"]
        assert [e.card.id for e in store.list_entries(status=STATUS_PENDING)] == ["b"]
        assert [e.card.id for e in store.list_entries(status=STATUS_REJECTED)] == ["c"]
        assert len(store.list_entries()) == 3

    def test_approve_reject(self) -> None:
        store = RegistryStore()
        from geonexus.registry.models import RegistryEntry

        store.register(RegistryEntry(card=_card("a"), node_url="http://n:1", status=STATUS_PENDING))
        entry = store.approve("a", note="looks good")
        assert entry.status == STATUS_APPROVED
        assert entry.review_note == "looks good"
        entry = store.reject("a", note="bad metadata")
        assert entry.status == STATUS_REJECTED
        assert entry.review_note == "bad metadata"

    def test_approve_unknown_raises(self) -> None:
        store = RegistryStore()
        from geonexus.registry.store import RegistryEntryNotFound

        with pytest.raises(RegistryEntryNotFound):
            store.approve("missing")

    def test_invalid_status_rejected(self) -> None:
        store = RegistryStore()
        from geonexus.registry.models import RegistryEntry

        store.register(RegistryEntry(card=_card("a"), node_url="http://n:1"))
        with pytest.raises(ValueError, match="invalid status"):
            store.set_status("a", "weird")

    def test_search_excludes_pending_by_default(self) -> None:
        store = RegistryStore()
        from geonexus.registry.models import RegistryEntry

        store.register(RegistryEntry(card=_card("ok"), node_url="http://n:1"))
        store.register(RegistryEntry(card=_card("pending"), node_url="http://n:1", status=STATUS_PENDING))
        # Default search returns only approved.
        assert [r.entry.card.id for r in store.search()] == ["ok"]
        # Explicit pending filter sees the review queue.
        assert [r.entry.card.id for r in store.search(status=STATUS_PENDING)] == ["pending"]
        # status=None includes everything (admin).
        assert len(store.search(status=None)) == 2

    def test_persistence_keeps_status(self, tmp_path: Any) -> None:
        store = RegistryStore(persist_path=str(tmp_path / "reg.json"))
        from geonexus.registry.models import RegistryEntry

        store.register(RegistryEntry(card=_card("a"), node_url="http://n:1", status=STATUS_PENDING))
        reloaded = RegistryStore(persist_path=str(tmp_path / "reg.json"))
        assert reloaded.get("a").status == STATUS_PENDING


class TestRegistryHttpReview:
    def test_full_review_flow(self) -> None:
        server = RegistryServer(name="review")
        url = _start_server(server, 0)
        with RegistryClient(url) as rc:
            # 1. Submit as pending (not discoverable).
            rc.register(_card("pending"), "http://n:1", status=STATUS_PENDING)
            assert len(rc.list_cards()) == 0  # approved-only listing
            assert len(rc.list_cards(status=STATUS_PENDING)) == 1
            # Search excludes it too.
            assert rc.search(type="data") == []

            # 2. Approve -> discoverable.
            rc.approve("pending", note="ok")
            assert len(rc.list_cards()) == 1
            assert len(rc.search(type="data")) == 1

            # 3. Reject -> hidden again.
            rc.reject("pending", note="withdrawn")
            assert len(rc.list_cards()) == 0
            entry = rc.get("pending")
            assert entry["status"] == STATUS_REJECTED
            assert entry["review_note"] == "withdrawn"

    def test_register_default_approved(self) -> None:
        server = RegistryServer(name="review")
        url = _start_server(server, 0)
        with RegistryClient(url) as rc:
            rc.register(_card("plain"), "http://n:1")
            assert len(rc.list_cards()) == 1  # immediately visible (compat)

    def test_approve_unknown_404(self) -> None:
        server = RegistryServer(name="review")
        url = _start_server(server, 0)
        with RegistryClient(url) as rc:
            from geonexus.registry import RegistryClientError

            with pytest.raises(RegistryClientError) as exc_info:
                rc.approve("missing")
            assert "404" in str(exc_info.value)
