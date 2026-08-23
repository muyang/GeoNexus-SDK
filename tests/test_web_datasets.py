"""Tests for the data registration endpoints (web/datasets.py)."""

from __future__ import annotations

# Ensure the demo skill (synthetic raster generator) is importable.
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from geonexus.registry import RegistryServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from examples.amazon_ndvi import skill as ndvi_skill  # noqa: E402


def _start_registry() -> str:
    import uvicorn

    server = RegistryServer(name="review-test")
    config = uvicorn.Config(server.create_app(), host="127.0.0.1", port=0, log_level="error")
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


@pytest.fixture()
def app(tmp_path: Path):
    from geonexus.web import JWTConfig, WebConfig, create_web_app

    registry_url = _start_registry()
    config = WebConfig(
        registry_url=registry_url,
        jwt=JWTConfig(secret="test-secret-that-is-long-enough-0123456789abcdef"),
        users={"admin": "pw"},
        datasets_dir=str(tmp_path / "uploads"),
        upload_node_url="http://node:8787",
    )
    return create_web_app(config)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def token(client: TestClient) -> str:
    r = client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    assert r.status_code == 200
    return r.json()["token"]


def _make_raster(tmp_path: Path) -> Path:
    out = tmp_path / "rasters"
    out.mkdir(parents=True, exist_ok=True)
    paths = ndvi_skill.generate_synthetic_scene(2025, str(out), width=32, height=32, seed=3)
    return Path(paths["red"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestUpload:
    def test_upload_generates_draft(self, client: TestClient, token: str, tmp_path: Path) -> None:
        raster = _make_raster(tmp_path)
        with raster.open("rb") as f:
            r = client.post(
                "/api/datasets/upload",
                headers=_auth(token),
                files={"file": ("scene.tif", f, "image/tiff")},
                data={"name": "My Scene", "capabilities": "ndvi"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "draft"
        card = body["card"]
        assert card["type"] == "data"
        assert card["spatial"]["crs"] == "EPSG:4326"
        assert "ndvi" in [c["name"] for c in card["capabilities"]]
        assert Path(body["file"]).exists()

    def test_upload_rejects_unsupported(self, client: TestClient, token: str) -> None:
        r = client.post(
            "/api/datasets/upload",
            headers=_auth(token),
            files={"file": ("notes.txt", b"not a raster", "text/plain")},
        )
        assert r.status_code == 415

    def test_upload_requires_auth(self, client: TestClient) -> None:
        r = client.post("/api/datasets/upload", files={"file": ("x.tif", b"", "image/tiff")})
        assert r.status_code == 401


class TestReviewFlow:
    def test_submit_approve_discover(self, client: TestClient, token: str, tmp_path: Path) -> None:
        raster = _make_raster(tmp_path)
        with raster.open("rb") as f:
            up = client.post(
                "/api/datasets/upload",
                headers=_auth(token),
                files={"file": ("scene.tif", f, "image/tiff")},
                data={"name": "Scene"},
            )
        upload_id = up.json()["id"]

        # Submit -> pending.
        r = client.post(f"/api/datasets/{upload_id}/submit", headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["status"] == "pending"

        # Pending queue has it; approved search does not (yet).
        pending = client.get("/api/datasets/pending", headers=_auth(token))
        assert pending.status_code == 200
        ids = [c["card"]["id"] for c in pending.json()["cards"]]
        assert f"upload-{upload_id}" in ids

        # Approve -> discoverable.
        r = client.post(f"/api/datasets/upload-{upload_id}/approve", headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

        pending = client.get("/api/datasets/pending", headers=_auth(token))
        assert pending.json()["count"] == 0

        # Registry search now sees it.
        from geonexus.registry import RegistryClient

        registry_url = client.app.state.web_config.registry_url
        with RegistryClient(registry_url) as rc:
            hits = rc.search(type="data")
        assert any(f"upload-{upload_id}" == h["entry"]["card"]["id"] for h in hits)

    def test_reject_keeps_hidden(self, client: TestClient, token: str, tmp_path: Path) -> None:
        raster = _make_raster(tmp_path)
        with raster.open("rb") as f:
            up = client.post(
                "/api/datasets/upload",
                headers=_auth(token),
                files={"file": ("bad.tif", f, "image/tiff")},
            )
        upload_id = up.json()["id"]
        client.post(f"/api/datasets/{upload_id}/submit", headers=_auth(token))
        r = client.post(
            f"/api/datasets/upload-{upload_id}/reject",
            headers=_auth(token),
            data={"note": "missing license"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"

        from geonexus.registry import RegistryClient

        registry_url = client.app.state.web_config.registry_url
        with RegistryClient(registry_url) as rc:
            hits = rc.search(type="data")
            assert not any(f"upload-{upload_id}" == h["entry"]["card"]["id"] for h in hits)
            entry = rc.get(f"upload-{upload_id}")
            assert entry["status"] == "rejected"
            assert entry["review_note"] == "missing license"
