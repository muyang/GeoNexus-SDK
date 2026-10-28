"""公共产品市场的安装侧：publish 校验、install 校验和、uninstall 反操作。

安全纪律（docs/plans/client-marketplace-v2.md §8 的红字）：
  · **安装即执行代码**。市场能做的只是把门关紧一点：只装 approved 条目；
    URL 引荐必须带 sha256 且下载后逐字节校验，对不上立刻中止、不落盘安装；
    pip 需求串引荐无法预校验，安装时明确告警。
  · 平台托管安装走审批单（BFF 触发），CLI 直装是运维行为、自己负责。
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx


class MarketError(Exception):
    """市场操作失败（校验和不对、条目未批准、pip 报错……）。"""


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pip(args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, "-m", "pip", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise MarketError(f"pip 失败（exit {proc.returncode}）：{' | '.join(tail)}")
    return proc


def install_plugin(
    entry: dict[str, Any], *, target: str | None = None, timeout: float = 60.0
) -> dict[str, Any]:
    """安装一个**已批准**的市场条目。返回安装摘要（含校验结果）。"""
    if entry.get("status") != "approved":
        raise MarketError(
            f"条目 {entry.get('name')}@{entry.get('version')} 状态为 "
            f"{entry.get('status')}：只有 approved 才能安装（先过审）"
        )
    ref = entry.get("install_ref", "")
    name, version = entry.get("name"), entry.get("version")
    # --target 托管安装只放插件本体：依赖（如 geonexus-sdk）由节点环境提供，
    # 否则托管目录里会躺第二份 SDK，导入时谁赢全看运气。
    target_args = ["--target", str(target), "--no-deps"] if target else []
    verified: str | None = None

    if ref.startswith(("http://", "https://")):
        expected = entry.get("sha256")
        if not expected:
            raise MarketError(f"URL 引荐缺少 sha256，拒绝安装：{ref}")
        # pip 会校验 wheel 文件名结构（name-ver-pytag-abi-plat.whl），
        # 所以临时文件必须沿用 URL 里的规范名，不能叫 tmpXXXX.whl
        import re as _re

        fname = ref.rsplit("/", 1)[-1].split("?")[0]
        if not _re.fullmatch(r"[A-Za-z0-9_.\-]+\.whl", fname):
            fname = f"{name}-{version}-py3-none-any.whl"
        tmp_dir = Path(tempfile.mkdtemp(prefix="gnx-market-"))
        tmp_path = tmp_dir / fname
        try:
            with httpx.stream("GET", ref, timeout=timeout, follow_redirects=True) as res:
                if res.status_code != 200:
                    raise MarketError(f"下载失败 HTTP {res.status_code}：{ref}")
                with open(tmp_path, "wb") as out:
                    for chunk in res.iter_bytes():
                        out.write(chunk)
            verified = sha256_file(tmp_path)
            if verified != expected:
                raise MarketError(
                    f"sha256 不匹配，中止安装：期望 {expected[:16]}… 实际 {verified[:16]}…"
                )
            _pip(["install", *target_args, str(tmp_path)])
        finally:
            import shutil as _sh

            _sh.rmtree(tmp_dir, ignore_errors=True)
    else:
        if entry.get("sha256"):
            # pip 索引引荐拿不到确定字节，校验和无法预验证 —— 说清楚，不假装验过
            verified = "unverifiable(pip-ref)"
        _pip(["install", *target_args, ref])

    return {
        "installed": f"{name}@{version}",
        "target": target or "current-env",
        "sha256_verified": verified,
    }


def uninstall_plugin(name: str, *, target: str | None = None) -> dict[str, Any]:
    """反操作：当前环境走 pip uninstall；--target 目录手工清（pip 不支持 target 卸载）。"""
    if target:
        root = Path(target)
        norm = name.replace("-", "_").lower()
        removed = []
        for cand in root.iterdir() if root.exists() else []:
            low = cand.name.lower()
            is_dist_info = low.endswith(".dist-info") and (
                low == f"{norm}.dist-info" or low.startswith(f"{norm}-")
            )
            is_pkg_dir = low in (norm, name.lower())
            if cand.is_dir() and (is_dist_info or is_pkg_dir):
                shutil.rmtree(cand, ignore_errors=True)
                removed.append(cand.name)
        if not removed:
            raise MarketError(f"在 {target} 下没找到 {name} 的安装残留")
        return {"uninstalled": name, "target": str(target), "removed": removed}
    _pip(["uninstall", "-y", name])
    return {"uninstalled": name, "target": "current-env"}
