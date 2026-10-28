"""公共产品市场测试：审核态、sha256 闸门、安装/卸载反操作。

红字纪律的测试化：**安装即执行代码**，所以市场能做的只有把门关紧——
只装 approved；URL 引荐必须带 sha256 且逐字节对上，对不上在 pip 之前中止。
"""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from geonexus.market import MarketError, install_plugin, sha256_file, uninstall_plugin
from geonexus.registry.models import PluginEntry
from geonexus.registry.server import RegistryServer
from geonexus.registry.store import PluginEntryConflict, RegistryStore


def _entry(**kw):
    base = {
        "name": "geonexus-plugin-demo",
        "version": "0.1.0",
        "install_ref": "pip:geonexus-plugin-demo",
        "description": "demo",
    }
    base.update(kw)
    return PluginEntry(**base)


# --------------------------------------------------------------------------- #
# store：审核态与持久化
# --------------------------------------------------------------------------- #
def test_publish_is_pending_and_list_defaults_approved(tmp_path):
    store = RegistryStore(persist_path=tmp_path / "reg.json")
    store.register_plugin(_entry())
    assert store.get_plugin("geonexus-plugin-demo", "0.1.0").status == "pending"
    assert store.list_plugins() == []  # 默认只看 approved
    assert len(store.list_plugins(status="pending")) == 1
    store.review_plugin("geonexus-plugin-demo", "0.1.0", approve=True, note="验收")
    assert [e.address for e in store.list_plugins()] == ["geonexus-plugin-demo@0.1.0"]


def test_duplicate_address_conflicts_but_versions_coexist(tmp_path):
    store = RegistryStore(persist_path=tmp_path / "reg.json")
    store.register_plugin(_entry())
    with pytest.raises(PluginEntryConflict):
        store.register_plugin(_entry())
    store.register_plugin(_entry(version="0.2.0"))  # 新版本是另一条记录
    assert len(store.list_plugins(status="pending")) == 2


def test_plugins_persist_across_restart(tmp_path):
    path = tmp_path / "reg.json"
    store = RegistryStore(persist_path=path)
    store.register_plugin(_entry())
    store.review_plugin("geonexus-plugin-demo", "0.1.0", approve=True)
    again = RegistryStore(persist_path=path)
    assert [e.address for e in again.list_plugins()] == ["geonexus-plugin-demo@0.1.0"]


def test_search_matches_name_desc_tags(tmp_path):
    store = RegistryStore(persist_path=tmp_path / "reg.json")
    store.register_plugin(_entry(tags=["ndvi"], description="vegetation index"))
    store.register_plugin(_entry(name="other-plugin", version="1.0", description="finance"))
    store.review_plugin("geonexus-plugin-demo", "0.1.0", approve=True)
    store.review_plugin("other-plugin", "1.0", approve=True)
    assert [e.name for e in store.search_plugins("ndvi")] == ["geonexus-plugin-demo"]
    assert len(store.search_plugins("")) == 2


# --------------------------------------------------------------------------- #
# HTTP：路由与 422/404 语义
# --------------------------------------------------------------------------- #
@pytest.fixture()
def registry():
    reg = RegistryServer()
    running = reg.start_in_thread(port=0)
    running.wait_until_ready()
    yield reg, f"http://127.0.0.1:{running.port}"
    running.stop()


def test_http_publish_approve_flow(registry):
    from geonexus.registry.client import RegistryClient

    reg, url = registry
    with RegistryClient(url) as client:
        out = client.publish_plugin(
            {"name": "p", "version": "1.0", "install_ref": "pip:p", "sha256": None}
        )
        assert out["entry"]["status"] == "pending"
        assert client.list_plugins() == []  # 未批准不可见
        client.approve_plugin("p", "1.0", note="ok", by="admin")
        assert [e["name"] for e in client.list_plugins()] == ["p"]
        assert client.get_plugin("p", "1.0")["status"] == "approved"


def test_http_url_ref_without_sha256_rejected(registry):
    from geonexus.registry.client import RegistryClient, RegistryClientError

    _, url = registry
    with RegistryClient(url) as client:
        with pytest.raises(RegistryClientError) as exc:
            client.publish_plugin({"name": "p", "version": "1.0", "install_ref": "http://x/y.whl"})
        assert exc.value.code == 422


def test_http_review_unknown_is_404(registry):
    from geonexus.registry.client import RegistryClient, RegistryClientError

    _, url = registry
    with RegistryClient(url) as client:
        with pytest.raises(RegistryClientError) as exc:
            client.approve_plugin("ghost", "9.9")
        assert exc.value.code == 404


# --------------------------------------------------------------------------- #
# install：sha256 闸门在 pip 之前
# --------------------------------------------------------------------------- #
def _serve(payload: bytes):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/pkg.whl"


def test_install_refuses_unapproved():
    with pytest.raises(MarketError, match="approved"):
        install_plugin({"name": "p", "version": "1", "status": "pending", "install_ref": "pip:p"})


def test_install_aborts_on_sha256_mismatch_before_pip():
    httpd, url = _serve(b"NOT A WHEEL AT ALL")
    try:
        entry = {
            "name": "p",
            "version": "1",
            "status": "approved",
            "install_ref": url,
            "sha256": "0" * 64,
        }
        with pytest.raises(MarketError, match="sha256"):
            install_plugin(entry)
    finally:
        httpd.shutdown()


def test_install_url_ref_without_sha256_refused():
    httpd, url = _serve(b"x")
    try:
        with pytest.raises(MarketError, match="sha256"):
            install_plugin({"name": "p", "version": "1", "status": "approved", "install_ref": url})
    finally:
        httpd.shutdown()


def test_install_bad_wheel_surfaces_pip_error():
    payload = b"garbage-not-a-wheel"
    httpd, url = _serve(payload)
    try:
        entry = {
            "name": "p",
            "version": "1",
            "status": "approved",
            "install_ref": url,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        with pytest.raises(MarketError, match="pip"):
            install_plugin(entry)  # 校验和过了，但包是坏的 → pip 报错被包起来
    finally:
        httpd.shutdown()


def test_uninstall_target_removes_dist_info_and_package(tmp_path):
    target = tmp_path / "managed"
    (target / "geonexus_plugin_demo-0.1.0.dist-info").mkdir(parents=True)
    (target / "geonexus_plugin_demo").mkdir(parents=True)
    (target / "unrelated").mkdir(parents=True)
    out = uninstall_plugin("geonexus-plugin-demo", target=str(target))
    assert sorted(out["removed"]) == [
        "geonexus_plugin_demo",
        "geonexus_plugin_demo-0.1.0.dist-info",
    ]
    assert (target / "unrelated").exists()


def test_sha256_file_matches_hashlib(tmp_path):
    f: Path = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    assert sha256_file(f) == hashlib.sha256(b"abc").hexdigest()
