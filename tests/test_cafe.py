"""Tests for CAFE pushdown engine (CentralServer + WorkerNode + protocol)."""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from geonexus.cafe import (
    CentralServer,
    PushdownRequest,
    TaskStatus,
    WorkerNode,
    worker_function,
)


def _make_raster(path, val=0.5, w=16, h=16):
    data = np.full((h, w), val, dtype=np.float32)
    with rasterio.open(path, "w", driver="GTiff", dtype=rasterio.float32, count=1,
                       width=w, height=h, crs="EPSG:4326",
                       transform=from_bounds(-10, -5, 10, 5, w, h)) as dst:
        dst.write(data, 1)
    return str(path)


NDVI_STATS_CODE = """\
import json
import rasterio
import numpy as np

path = inputs['raster']
with rasterio.open(path) as src:
    data = src.read(1).astype(np.float32)
result = {
    "mean": float(data.mean()),
    "max": float(data.max()),
    "pixels": int(data.size),
}
"""


class TestWorkerNode:
    def test_worker_executes_pushdown_locally(self, tmp_path):
        raster = _make_raster(str(tmp_path / "r.tif"), val=0.5)
        worker = WorkerNode("worker-a", data_assets={"raster": raster})

        req = PushdownRequest(code=NDVI_STATS_CODE, inputs={"raster": "raster"})
        result = worker.execute(req)

        assert result.status == TaskStatus.SUCCEEDED
        assert result.result["mean"] == pytest.approx(0.5, abs=0.01)
        assert result.result["pixels"] == 256
        assert result.worker_id == "worker-a"

    def test_worker_resolves_absolute_path(self, tmp_path):
        raster = _make_raster(str(tmp_path / "r.tif"), val=0.7)
        worker = WorkerNode("worker-b")
        req = PushdownRequest(code=NDVI_STATS_CODE, inputs={"raster": raster})
        result = worker.execute(req)
        assert result.status == TaskStatus.SUCCEEDED
        assert result.result["mean"] == pytest.approx(0.7, abs=0.01)

    def test_worker_fails_on_missing_input(self, tmp_path):
        worker = WorkerNode("worker-c", data_assets={})
        req = PushdownRequest(code=NDVI_STATS_CODE, inputs={"raster": "nonexistent-tif"})
        result = worker.execute(req)
        assert result.status == TaskStatus.FAILED
        assert "cannot resolve" in result.error

    def test_worker_times_out(self, tmp_path):
        worker = WorkerNode("worker-slow")
        code = "import time; time.sleep(30); result = 1"
        req = PushdownRequest(code=code, timeout_seconds=1)
        result = worker.execute(req)
        assert result.status == TaskStatus.TIMEOUT


class TestCentralServer:
    def test_routes_to_data_locality(self, tmp_path):
        # Two workers: raster lives on worker-a only
        raster = _make_raster(str(tmp_path / "r.tif"), val=0.3)
        worker_a = WorkerNode("worker-a", data_assets={"raster": raster})
        worker_b = WorkerNode("worker-b", data_assets={"other": "local://other.tif"})
        central = CentralServer("central-1").register_worker(worker_a).register_worker(worker_b)

        req = PushdownRequest(code=NDVI_STATS_CODE, inputs={"raster": "raster"})
        decision = central.route(req)
        assert decision.chosen_worker == "worker-a"
        assert "data locality" in decision.reason

    def test_routes_to_region_tag(self, tmp_path):
        worker_a = WorkerNode("worker-a", tags={"region": "mekong"})
        worker_b = WorkerNode("worker-b", tags={"region": "amazon"})
        central = CentralServer().register_worker(worker_a).register_worker(worker_b)

        req = PushdownRequest(code="result=1", metadata={"region": "mekong"})
        decision = central.route(req)
        assert decision.chosen_worker == "worker-a"

    def test_dispatch_end_to_end(self, tmp_path):
        raster = _make_raster(str(tmp_path / "r.tif"), val=0.55)
        worker = WorkerNode("worker-data", data_assets={"raster": raster})
        central = CentralServer().register_worker(worker)

        req = PushdownRequest(code=NDVI_STATS_CODE, inputs={"raster": "raster"})
        result = central.dispatch(req)

        assert result.status == TaskStatus.SUCCEEDED
        assert result.result["mean"] == pytest.approx(0.55, abs=0.01)

    def test_no_workers(self):
        central = CentralServer()
        req = PushdownRequest(code="result=1")
        result = central.dispatch(req)
        assert result.status == TaskStatus.FAILED


class TestWorkerFunction:
    def test_direct_invocation(self):
        # Direct call uses subprocess; build a mini script that just returns a dict
        script = "import json; print(json.dumps({'ok': True}))"
        result, error = worker_function(script, 10)
        assert error is None
        assert result == {"ok": True}


class TestSDKDataFlow:
    def test_federated_pushdown_macro(self, tmp_path):
        """宏流程：Central 分发 → Worker 本地计算 → 结果聚合（不下载原始数据）。"""
        raster = _make_raster(str(tmp_path / "mekong.tif"), val=0.42)
        worker = WorkerNode("mekong-data", data_assets={"raster": raster}, tags={"region": "mekong"})
        central = CentralServer().register_worker(worker)

        req = PushdownRequest(
            code=NDVI_STATS_CODE,
            inputs={"raster": "raster"},
            metadata={"region": "mekong"},
            task_id="agg-01",
        )
        result = central.dispatch(req)
        assert result.task_id == "agg-01"
        assert result.status == TaskStatus.SUCCEEDED
        assert result.result["mean"] == pytest.approx(0.42, abs=0.01)