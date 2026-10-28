"""登记核心测试：探测只读不执行、取不到就留空、建卡可校验、提交默认 dry。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from geonexus.register import build_card, probe_target, submit, validate_card

OGC_CONFORMANCE = {
    "conformsTo": [
        "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
        "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
    ]
}
OGC_COLLECTIONS = {
    "collections": [
        {
            "id": "lakes",
            "title": "Lakes",
            "description": "Freshwater lakes",
            "extent": {
                "spatial": {
                    "bbox": [[100.0, 0.0, 110.0, 10.0]],
                    "crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
                }
            },
        }
    ]
}
STAC_CATALOG = {
    "type": "Catalog",
    "id": "demo",
    "title": "Demo Catalog",
    "stac_version": "1.0.0",
    "links": [],
}
WMS_XML = b"""<?xml version="1.0"?>
<WMS_Capabilities version="1.3.0">
  <Capability><Layer><Title>Demo WMS</Title>
    <EX_GeographicBoundingBox><westBC>80</westBC><southBC>-5</southBC>
    <eastBC>90</eastBC><northBC>5</northBC></EX_GeographicBoundingBox>
  </Layer></Capability>
</WMS_Capabilities>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静音
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/ogc/conformance":
            body, ctype = json.dumps(OGC_CONFORMANCE).encode(), "application/json"
        elif path == "/ogc/collections":
            body, ctype = json.dumps(OGC_COLLECTIONS).encode(), "application/json"
        elif path == "/ogc":
            body, ctype = json.dumps({"links": []}).encode(), "application/json"
        elif path == "/stac":
            body, ctype = json.dumps(STAC_CATALOG).encode(), "application/json"
        elif path == "/wms" and "GetCapabilities" in self.path:
            body, ctype = WMS_XML, "application/xml"
        else:
            body, ctype = b"{}", "application/json"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


# --------------------------------------------------------------------------- #
# 探测
# --------------------------------------------------------------------------- #
def test_probe_ogc_features_extracts_bbox(server):
    r = probe_target(f"{server}/ogc")
    assert r.kind == "ogc-features"
    assert r.facts["bbox"] == [100.0, 0.0, 110.0, 10.0]
    assert r.facts["title"] == "Lakes"


def test_probe_stac(server):
    assert probe_target(f"{server}/stac").kind == "stac"


def test_probe_wms(server):
    r = probe_target(f"{server}/wms?service=WMS&request=GetCapabilities")
    assert r.kind == "wms"
    assert r.facts["bbox"] == [80.0, -5.0, 90.0, 5.0]


def test_probe_geotiff_reads_real_bounds(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy as np
    from rasterio.transform import from_bounds

    path = tmp_path / "demo.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_bounds(120, 30, 121, 31, 4, 4),
    ) as dst:
        dst.write(np.zeros((1, 4, 4), dtype="uint8"))
    r = probe_target(str(path))
    assert r.kind == "geotiff"
    assert r.facts["bbox"] == [120.0, 30.0, 121.0, 31.0]


def test_probe_csv_with_latlon(tmp_path):
    p = tmp_path / "sites.csv"
    p.write_text("id,lat,lon,val\na,31.2,121.4,1\nb,30.1,122.0,2\nc,xx,yy,3\n", encoding="utf-8")
    r = probe_target(str(p))
    assert r.kind == "csv"
    assert r.facts["bbox"] == [121.4, 30.1, 122.0, 31.2]
    assert "fields" in r.facts


def test_probe_csv_without_coords_warns_not_invents(tmp_path):
    p = tmp_path / "plain.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    r = probe_target(str(p))
    assert r.kind == "csv" and "bbox" not in r.facts
    assert any("非空间" in w for w in r.warnings)


def test_probe_geojson_bbox(tmp_path):
    p = tmp_path / "f.geojson"
    p.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Point", "coordinates": [121.0, 31.0]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    r = probe_target(str(p))
    assert r.kind == "geojson" and r.facts["bbox"] == [121.0, 31.0, 121.0, 31.0]


def test_probe_torch_never_loads_weights(tmp_path):
    p = tmp_path / "model.pt"
    p.write_bytes(b"\x00" * 64)
    r = probe_target(str(p))
    assert r.kind == "torch"
    assert any("不做加载" in w for w in r.warnings)  # 加载即执行，登记工具不碰


def test_probe_missing_file(server):
    r = probe_target(f"{server}/nope")
    assert r.kind == "unknown" and r.warnings


# --------------------------------------------------------------------------- #
# 建卡 / 校验 / 提交
# --------------------------------------------------------------------------- #
def test_build_card_maps_kind_and_keeps_facts(server):
    probe = probe_target(f"{server}/ogc")
    card, warnings = build_card(probe, {"license": "CC-BY-4.0", "provider": "测试机构"})
    assert card["type"] == "data"
    assert card["access"]["protocol"] == "ogcapi"
    assert card["access"]["endpoint"] == probe.target
    assert card["spatial"]["bbox"] == probe.facts["bbox"]
    assert card["license"]["name"] == "CC-BY-4.0"
    assert not validate_card(card), validate_card(card)
    assert not any("许可" in w for w in warnings)


def test_build_card_without_bbox_has_no_spatial(tmp_path):
    p = tmp_path / "plain.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    card, warnings = build_card(probe_target(str(p)), {"license": "MIT"})
    assert "spatial" not in card
    assert any("非空间" in w for w in warnings)
    assert not validate_card(card)


def test_build_card_without_license_warns(server):
    probe = probe_target(f"{server}/ogc")
    _, warnings = build_card(probe)
    assert any("许可" in w for w in warnings)


def test_submit_dry_touches_no_network(server):
    card, _ = build_card(probe_target(f"{server}/ogc"), {"license": "CC-BY-4.0"})
    out = submit(card, to="dry")
    assert out["submitted"] is False and out["card"]["id"] == card["id"]


def test_submit_to_registry_sends_pending_by_default(server):
    """登记 ≠ 发布：submit 默认带 status=pending，payload 用 registry 的 {card, node_url, status}。"""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: dict = {}

    class Rec(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("content-length", 0))
            seen.update(json.loads(self.rfile.read(n) or b"{}"))
            self.send_response(201)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"registered"}')

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Rec)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        card, _ = build_card(probe_target(f"{server}/ogc"), {"license": "CC-BY-4.0"})
        out = submit(
            card, to="registry", registry_url=f"http://127.0.0.1:{httpd.server_address[1]}"
        )
        assert out["submitted"], out
        assert seen["status"] == "pending", "默认必须是 pending：登记不等于发布"
        assert seen["card"]["id"] == card["id"]
    finally:
        httpd.shutdown()


def test_submit_platform_requires_credentials(server):
    card, _ = build_card(probe_target(f"{server}/ogc"), {"license": "CC-BY-4.0"})
    out = submit(card, to="platform", platform_url="http://127.0.0.1:1")  # 不可达端口
    assert out["submitted"] is False and "登录" in out.get("error", "")
