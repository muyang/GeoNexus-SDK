"""OGC write-side bridge demo (V1.0).

Demonstrates GeoMCP execution bridging to OGC API - Processes:

    GeoMCP geo.execute("echo", params={...})
        -> GeoNode
        -> GeoSkill (OGC process bridge)
        -> OGC API - Processes POST /processes/echo/execution
        -> job polling -> results

The demo runs a **local mock OGC Processes server** (fully offline): the
mock implements the execution endpoints and echoes the submitted inputs.
Use ``--remote URL`` to point at a real OGC API - Processes service instead.

Usage:
    python run_demo.py [--node-port N] [--remote URL]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Module-level imports so FastAPI can resolve annotations for the mock app
# (function-scope imports break string-annotation resolution).
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from geonexus.adapters import register_ogc_process_skill  # noqa: E402
from geonexus.geomcp import GeoMCPClient  # noqa: E402
from geonexus.geonode import GeoNode  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent


def _build_mock_ogc_app() -> Any:
    """A minimal OGC API - Processes implementation that echoes inputs."""

    app = FastAPI(title="mock-ogc-processes")
    jobs: dict[str, dict[str, Any]] = {}
    counter = 0

    @app.get("/processes")
    async def list_processes() -> dict[str, Any]:
        return {
            "processes": [
                {
                    "id": "echo",
                    "title": "Echo",
                    "description": "Echoes the submitted inputs (mock).",
                    "inputs": {"message": {"schema": {"type": "string"}}},
                    "outputs": {"echo": {"schema": {"type": "object"}}},
                }
            ]
        }

    @app.get("/processes/echo")
    async def get_echo_process() -> dict[str, Any]:
        return {
            "id": "echo",
            "title": "Echo",
            "description": "Echoes the submitted inputs (mock).",
            "inputs": {"message": {"schema": {"type": "string"}}},
            "outputs": {"echo": {"schema": {"type": "object"}}},
        }

    @app.post("/processes/echo/execution")
    async def execute_echo(request: Request) -> JSONResponse:
        nonlocal counter
        counter += 1
        body = await request.json()
        job_id = f"echo-job-{counter}"
        jobs[job_id] = {
            "status": "running",
            "inputs": (body or {}).get("inputs", {}),
            "results_href": f"/jobs/{job_id}/results",
        }
        return JSONResponse(
            {"status": "accepted", "job": f"/jobs/{job_id}"},
            status_code=201,
            headers={"Location": f"/jobs/{job_id}"},
        )

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        job = jobs[job_id]
        if job["status"] == "running":
            job["status"] = "successful"
        return {
            "status": job["status"],
            "results": {"href": job["results_href"]},
        }

    @app.get("/jobs/{job_id}/results")
    async def get_results(job_id: str) -> dict[str, Any]:
        job = jobs[job_id]
        inputs = job["inputs"]
        echo = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in inputs.items()}
        return {"echo": echo}

    return app


class _ServerHandle:
    def __init__(self, server, thread) -> None:
        self._server = server
        self._thread = thread

    def wait_ready(self, timeout: float = 15.0) -> _ServerHandle:
        import time

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


def run(node_port: int = 8787, remote: str | None = None, process: str = "echo") -> dict:
    print("=" * 74)
    print("GeoNexus Reference Stack - OGC Write-Side Bridge Demo (V1.0)")
    print("GeoMCP geo.execute -> OGC API - Processes")
    print("=" * 74)

    # 1. OGC Processes endpoint (local mock by default) ---------------------- #
    if remote:
        ogc_url = remote.rstrip("/")
        print(f"\n[1/5] OGC API - Processes endpoint: {ogc_url} (remote)")
        ogc_handle = None
    else:
        print("\n[1/5] Starting LOCAL mock OGC API - Processes server (offline)")
        mock = _build_mock_ogc_app()
        ogc_handle = _start_server(mock, 0)
        ogc_url = f"http://127.0.0.1:{ogc_handle.port}"
        print(f"      mock endpoint: {ogc_url} (process '{process}')")

    # 2. GeoNode with the OGC process registered as a remote skill ----------- #
    print("[2/5] Starting GeoNode and registering OGC process skill")
    node = GeoNode(name="ogc-bridge-node", port=node_port)
    try:
        skill = register_ogc_process_skill(node, ogc_url, process)
    except Exception as exc:  # noqa: BLE001 - startup reporting
        print(f"      WARNING: {exc}", file=sys.stderr)
        skill = None
    node_handle = _start_server(node.create_app(), node_port)
    node_url = f"http://127.0.0.1:{node_handle.port if node_port == 0 else node_port}"
    if skill is not None:
        print(f"      skill '{process}' -> {ogc_url} (geocard: {skill.geocard.id})")

    try:
        # 3. GeoMCP capability surface ---------------------------------------- #
        print(f"[3/5] GeoMCP over HTTP at {node_url}")
        with GeoMCPClient(node_url, timeout=30) as client:
            caps = client.capabilities()
            skill_names = [s["name"] for s in caps["skills"]]
            print(f"      skills: {skill_names}")

            # 4. Execute the OGC process via GeoMCP --------------------------- #
            print(f"[4/5] geo.execute('{process}', params={{...}})")
            params: dict[str, Any]
            if process == "hello-world":
                # pygeoapi's hello-world requires a 'name' input.
                params = {"name": "GeoNexus", "message": "hello from GeoNexus"}
            else:
                params = {"message": "hello from GeoNexus", "times": 3}
            result = client.execute(
                skill=process,
                params=params,
                request_id="ogc-bridge-demo",
            )
            outputs = result["outputs"]
            print(f"      status:       {result['status']}")
            print(f"      job_status:   {outputs.get('job_status')}")
            print(f"      ogc_process:  {outputs.get('ogc_process')}")
            print(f"      results:      {outputs.get('results')}")

            # 5. Contract gate: unknown process skill is refused --------------- #
            print("[5/5] Contract gate: unknown skill is refused")
            try:
                client.execute(skill="no-such-process", params={})
                print("      ERROR: unexpected success")
            except Exception as exc:  # noqa: BLE001 - expected refusal
                print(f"      refused: {exc}")

            summary = {
                "demo": "ogc-process",
                "ogc_endpoint": ogc_url,
                "remote": bool(remote),
                "skill": process,
                "job_status": outputs.get("job_status"),
                "results": outputs.get("results"),
            }
            print("\nOGC write-side bridge demo complete.")
            return summary
    finally:
        node_handle.stop()
        if ogc_handle is not None:
            ogc_handle.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OGC write-side bridge demo")
    parser.add_argument("--node-port", type=int, default=8787)
    parser.add_argument(
        "--remote",
        default=None,
        help="Real OGC API - Processes root (default: local mock)",
    )
    parser.add_argument(
        "--process",
        default="echo",
        help="OGC process id (mock: 'echo'; pygeoapi: 'hello-world')",
    )
    args = parser.parse_args(argv)
    run(node_port=args.node_port, remote=args.remote, process=args.process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
