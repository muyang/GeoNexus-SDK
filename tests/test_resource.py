"""Tests for the GeoCard resource matching module (resource.py)."""

from __future__ import annotations

import pytest

from geonexus.geocard import GeoCardBuilder
from geonexus.resource import (
    ComputeCapability,
    ResourceError,
    ResourceMatch,
    match_resource,
    parse_memory,
    resolve_compute_capabilities,
)


def _model(runtime: dict | None = None, model_id: str = "model-ndvi") -> object:
    builder = (
        GeoCardBuilder(model_id, "model", f"Model {model_id}", "test model")
        .capability("ndvi")
        .interface(type="geomcp-skill", version="1.0")
    )
    if runtime:
        builder.runtime(**runtime)
    return builder.build()


class TestParseMemory:
    def test_gi(self) -> None:
        assert parse_memory("4Gi") == 4.0
        assert parse_memory("4GiB") == 4.0
        assert parse_memory("8 GB") == 8.0
        assert parse_memory("2gib") == 2.0

    def test_mi(self) -> None:
        assert parse_memory("4096Mi") == 4.0
        assert parse_memory("1024MiB") == 1.0

    def test_number(self) -> None:
        assert parse_memory(4) == 4.0
        assert parse_memory("6") == 6.0

    def test_invalid(self) -> None:
        assert parse_memory("lots") is None
        assert parse_memory(None) is None


class TestMatchResource:
    def test_no_runtime_any_node(self) -> None:
        caps = [ComputeCapability(node_url="http://a:1")]
        match = match_resource(_model(), caps)
        assert match.matched is True
        assert match.compute_node == "http://a:1"

    def test_cpu_match(self) -> None:
        model = _model({"cpu": 2, "memory": "4Gi", "gpu": "none"})
        caps = [ComputeCapability(node_url="http://a:1", cpu=4, memory_gb=8.0, gpu="none")]
        match = match_resource(model, caps)
        assert match.matched is True
        assert match.compute_node == "http://a:1"

    def test_cpu_insufficient(self) -> None:
        model = _model({"cpu": 4, "memory": "4Gi", "gpu": "none"})
        caps = [ComputeCapability(node_url="http://a:1", cpu=2, memory_gb=8.0, gpu="none")]
        match = match_resource(model, caps)
        assert match.matched is False
        assert match.compute_node is None
        assert any("cpu: model needs 4" in r for r in match.reasons)

    def test_memory_insufficient(self) -> None:
        model = _model({"cpu": 1, "memory": "16Gi", "gpu": "none"})
        caps = [ComputeCapability(node_url="http://a:1", cpu=2, memory_gb=8.0, gpu="none")]
        match = match_resource(model, caps)
        assert match.matched is False
        assert any("memory" in r for r in match.reasons)

    def test_gpu_required_no_gpu_node(self) -> None:
        model = _model({"cpu": 2, "memory": "4Gi", "gpu": "cuda"})
        caps = [ComputeCapability(node_url="http://cpu:1", cpu=8, memory_gb=32.0, gpu="none")]
        match = match_resource(model, caps)
        assert match.matched is False
        assert any("gpu" in r for r in match.reasons)

    def test_gpu_required_picks_gpu_node(self) -> None:
        model = _model({"cpu": 2, "memory": "4Gi", "gpu": "cuda"})
        caps = [
            ComputeCapability(node_url="http://cpu:1", cpu=8, memory_gb=32.0, gpu="none"),
            ComputeCapability(node_url="http://gpu:2", cpu=8, memory_gb=32.0, gpu="cuda"),
        ]
        match = match_resource(model, caps)
        assert match.matched is True
        assert match.compute_node == "http://gpu:2"

    def test_gpu_none_ok_on_any(self) -> None:
        model = _model({"gpu": "none"})
        caps = [ComputeCapability(node_url="http://gpu:2", gpu="cuda")]
        match = match_resource(model, caps)
        assert match.matched is True

    def test_first_satisfying_wins(self) -> None:
        model = _model({"cpu": 1, "gpu": "none"})
        caps = [
            ComputeCapability(node_url="http://a:1", cpu=1, gpu="none"),
            ComputeCapability(node_url="http://b:2", cpu=8, gpu="none"),
        ]
        match = match_resource(model, caps)
        assert match.compute_node == "http://a:1"

    def test_to_dict_shape(self) -> None:
        match = ResourceMatch(matched=True, model_id="m", compute_node="http://n:1", reasons=["ok"])
        d = match.to_dict()
        assert d["matched"] is True
        assert d["compute_node"] == "http://n:1"


class TestResolveComputeCapabilities:
    def test_resolves_from_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeRegistry:
            def __init__(self, *a: object, **kw: object) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

            def search(self, type: str | None = None, **kw: object) -> list[dict]:
                assert type == "compute"
                return [
                    {
                        "entry": {
                            "card": {
                                "id": "compute-gpu",
                                "type": "compute",
                                "runtime": {"cpu": 8, "memory": "16Gi", "gpu": "cuda"},
                            },
                            "node_url": "http://gpu:8789",
                        }
                    },
                    {
                        "entry": {
                            "card": {
                                "id": "compute-cpu",
                                "type": "compute",
                                "runtime": {"cpu": 4, "memory": "8Gi", "gpu": "none"},
                            },
                            "node_url": "http://cpu:8789",
                        }
                    },
                ]

        import geonexus.resource as resource_mod

        monkeypatch.setattr(resource_mod, "RegistryClient", lambda *a, **k: _FakeRegistry())
        caps = resolve_compute_capabilities("http://registry:8790")
        assert len(caps) == 2
        gpu = caps[0]
        assert gpu.node_url == "http://gpu:8789"
        assert gpu.cpu == 8
        assert gpu.memory_gb == 16.0
        assert gpu.gpu == "cuda"

    def test_no_compute_cards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeRegistry:
            def __init__(self, *a: object, **kw: object) -> None:
                pass

            def close(self) -> None:
                pass

            def search(self, type: str | None = None, **kw: object) -> list[dict]:
                return []

        import geonexus.resource as resource_mod

        monkeypatch.setattr(resource_mod, "RegistryClient", lambda *a, **k: _FakeRegistry())
        assert resolve_compute_capabilities("http://registry:8790") == []

    def test_registry_error_surfaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Broken:
            def __init__(self, *a: object, **kw: object) -> None:
                pass

            def close(self) -> None:
                pass

            def search(self, type: str | None = None, **kw: object) -> list[dict]:
                from geonexus.registry import RegistryClientError

                raise RegistryClientError("boom", code=502)

        import geonexus.resource as resource_mod

        monkeypatch.setattr(resource_mod, "RegistryClient", lambda *a, **k: _Broken())
        with pytest.raises(ResourceError, match="Compute discovery failed"):
            resolve_compute_capabilities("http://registry:8790")
