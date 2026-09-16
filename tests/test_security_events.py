"""Tests for Security Gateway + SSE Events + Neo4j Store + GeoKG backend."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from geonexus.cafe.events import clear_events, notify_checkpoint, push_event, subscribe_events
from geonexus.kg.neo4j_store import Neo4jStore
from geonexus.security import AccessRequest, PolicyDecision, SecurityGateway

# --------------------------------------------------------------------------- #
# Security Gateway
# --------------------------------------------------------------------------- #

class TestSecurityGateway:
    def test_allow_public_read(self):
        gw = SecurityGateway()
        req = AccessRequest(subject="user1", action="read", sensitivity="public")
        assert gw.is_allowed(req)

    def test_deny_anonymous_write(self):
        gw = SecurityGateway()
        req = AccessRequest(subject="anonymous", action="write", sensitivity="public")
        assert not gw.is_allowed(req)

    def test_deny_cross_region_secret(self):
        gw = SecurityGateway()
        req = AccessRequest(
            subject="user2", action="read", region="eu",
            sensitivity="secret",
        )
        decision = gw.evaluate(req)
        assert not decision.allowed
        assert "cross" in decision.reason.lower() or "deny" in decision.rule_name

    def test_default_deny(self):
        gw = SecurityGateway()
        req = AccessRequest(subject="anon", action="admin", sensitivity="restricted")
        decision = gw.evaluate(req)
        assert not decision.allowed
        assert decision.rule_name == "default_deny"

    def test_allow_internal_region(self):
        gw = SecurityGateway()
        req = AccessRequest(
            subject="admin1", action="write", region="admin",
            sensitivity="restricted",
            metadata={"user_region": "admin"},
        )
        assert gw.is_allowed(req)

    def test_custom_rule(self):
        gw = SecurityGateway()
        gw.add_rule("allow_test", lambda req: PolicyDecision(True, "test rule", "allow_test") if req.action == "ping" else None)
        assert gw.is_allowed(AccessRequest(action="ping"))
        assert not gw.is_allowed(AccessRequest(action="delete"))

    def test_check_convenience(self):
        gw = SecurityGateway()
        decision = gw.check("read", subject="viewer", sensitivity="public")
        assert decision.allowed

    def test_decision_fields(self):
        decision = PolicyDecision(True, "ok", "rule1", ["warn1"])
        assert decision.allowed
        assert decision.reason == "ok"


# --------------------------------------------------------------------------- #
# SSE Events
# --------------------------------------------------------------------------- #

class TestSSEEvents:
    def setup_method(self):
        clear_events("test-req-001")

    def test_push_event_no_error(self):
        """push_event 不抛异常。"""
        push_event("test-req-001", "checkpoint", {"step": 1})

    def test_notify_checkpoint_no_error(self):
        """notify_checkpoint 不抛异常。"""
        notify_checkpoint("test-ckpt", "step1", "running", "processing")

    def test_clear_events_no_error(self):
        """clear_events 不抛异常。"""
        push_event("test-clr", "start", {})
        clear_events("test-clr")

    @pytest.mark.asyncio
    async def test_subscribe_sse_generator(self):
        """SSE 生成器产生 connected 事件。"""
        gen = subscribe_events("sse-sub-test")
        items = []
        try:
            async for item in gen:
                items.append(item)
                if len(items) >= 1:
                    break
        except asyncio.TimeoutError:
            pass
        assert len(items) >= 1
        assert "connected" in items[0]
        clear_events("sse-sub-test")


# --------------------------------------------------------------------------- #
# Neo4j Store
# --------------------------------------------------------------------------- #

class TestNeo4jStore:
    def test_from_env_empty(self):
        store = Neo4jStore.from_env()
        assert store is None  # No NEO4J_URI in test env

    @patch.dict("os.environ", {"NEO4J_URI": "bolt://localhost:7687", "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "test"})
    def test_from_env_with_uri(self):
        store = Neo4jStore.from_env()
        assert store is not None
        assert store.uri == "bolt://localhost:7687"
        assert store.available

    def test_not_available_without_uri(self):
        store = Neo4jStore("", "", "")
        assert not store.available

    def test_search_returns_empty_without_driver(self):
        store = Neo4jStore("bolt://localhost:7687", "u", "p")
        store._driver = None
        results = store.search("test")
        assert results == []