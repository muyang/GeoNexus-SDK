"""Tests for the OGC write-side bridge (GeoMCP execute -> OGC Processes)."""

from __future__ import annotations

import httpx
import pytest
from examples.ogc_process.run_demo import _build_mock_ogc_app
from test_registry import _start_server

from geonexus.adapters import OgcProcessExecutionError, OgcProcessExecutor
from geonexus.adapters.ogc_exec import _to_ogc_inputs, make_ogc_process_handler
from geonexus.geomcp import GeoMCPClient
from geonexus.geonode import GeoNode


def _mock_ogc() -> tuple[str, object]:
    app = _build_mock_ogc_app()
    handle = _start_server(app, 0)
    return f"http://127.0.0.1:{handle.port}", handle


def _transport_client(routes) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for key, value in routes.items():
            if path.endswith(key):
                return value(request) if callable(value) else httpx.Response(200, json=value)
        return httpx.Response(404, json={"code": "NotFound"})

    return httpx.Client(base_url="https://ogc.example/v1", transport=httpx.MockTransport(handler))


def test_inputs_conversion() -> None:
    """Skill params convert to OGC inputs (scalar/list/dict passthrough)."""

    assert _to_ogc_inputs({"name": "x"}) == {"name": {"value": "x"}}
    assert _to_ogc_inputs({"ids": [1, 2]}) == {"ids": [{"value": 1}, {"value": 2}]}
    assert _to_ogc_inputs({"href": {"href": "http://x"}}) == {"href": {"href": "http://x"}}
    assert _to_ogc_inputs({"num": 3.5}) == {"num": {"value": 3.5}}


def test_executor_async_job() -> None:
    """Execution -> Location -> poll -> results."""
    job_id = "/jobs/abc"
    routes = {
        "/processes/echo/execution": lambda req: httpx.Response(
            201,
            json={"status": "accepted"},
            headers={"Location": job_id},
        ),
        job_id: {"status": "successful", "results": {"href": f"{job_id}/results"}},
        f"{job_id}/results": {"echo": {"message": "hi"}},
    }
    client = _transport_client(routes)
    with OgcProcessExecutor("https://ogc.example/v1", client=client) as executor:
        submitted = executor.execute("echo", {"message": {"value": "hi"}})
        assert submitted == {"status": "accepted", "job_location": job_id}
        job = executor.wait_for_job(job_id, poll_interval=0.05, timeout=5)
        assert job["status"] == "successful"
        results = executor.get_results(f"{job_id}/results")
        assert results == {"echo": {"message": "hi"}}


def test_executor_sync_result() -> None:
    """Synchronous execution returns the inline result."""
    routes = {
        "/processes/add/execution": {"sum": 5},
    }
    client = _transport_client(routes)
    with OgcProcessExecutor("https://ogc.example/v1", client=client) as executor:
        result = executor.execute("add", {"a": {"value": 2}, "b": {"value": 3}})
        assert result == {"status": "successful", "result": {"sum": 5}}


def test_executor_failed_job() -> None:
    """A failed job is surfaced by the handler as an error."""
    routes = {
        "/processes/crash/execution": lambda req: httpx.Response(
            201, json={"status": "accepted"}, headers={"Location": "/jobs/f"}
        ),
        "/jobs/f": {"status": "failed", "message": "boom"},
    }
    client = _transport_client(routes)
    handler = make_ogc_process_handler("https://ogc.example/v1", "crash", client=client)
    with pytest.raises(OgcProcessExecutionError, match="failed"):
        handler({}, None)


def test_executor_rejection() -> None:
    """A 4xx execution response raises OgcProcessExecutionError."""
    client = _transport_client({})
    with (
        OgcProcessExecutor("https://ogc.example/v1", client=client) as executor,
        pytest.raises(OgcProcessExecutionError, match="rejected"),
    ):
        executor.execute("missing", {})


def test_bridge_end_to_end() -> None:
    """GeoMCP geo.execute drives an OGC process through a GeoNode."""
    ogc_url, ogc_handle = _mock_ogc()
    node = GeoNode(name="bridge-test", port=0)
    from geonexus.adapters import register_ogc_process_skill

    register_ogc_process_skill(node, ogc_url, "echo")
    node_handle = _start_server(node.create_app(), 0)
    try:
        with GeoMCPClient(f"http://127.0.0.1:{node_handle.port}", timeout=15) as client:
            caps = client.capabilities()
            assert any(s["name"] == "echo" for s in caps["skills"])

            result = client.execute(
                skill="echo",
                params={"message": "hello", "times": 2},
                request_id="bridge-test-1",
            )
            assert result["status"] == "ok"
            outputs = result["outputs"]
            assert outputs["job_status"] == "successful"
            assert outputs["ogc_process"] == "echo"
            assert outputs["results"] == {"echo": {"message": "hello", "times": 2}}
    finally:
        node_handle.stop()
        ogc_handle.stop()
