"""建卡：把探测结果变成 GeoCard 草稿。

规则（与案例模版同一套纪律）：
  · 探测到的事实直接进卡片；探测不到的**不编**——没有 bbox 就不写 spatial，
    卡片自然按"非空间"走侧栏，而不是给一个假位置。
  · `license` / `sensitivity` / `provider` 属于**人的判断**，只能来自向导或 --set，
    探测不给默认许可（"未声明"比"猜一个 CC-BY"诚实）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .probe import ProbeResult

#: 探测类型 → GeoCard type
KIND_TYPE = {
    "ogc-features": "data",
    "ogc-coverages": "data",
    "ogc-tiles": "data",
    "wms": "data",
    "stac": "data",
    "geotiff": "data",
    "csv": "data",
    "geojson": "data",
    "ogc-processes": "skill",
    "onnx": "model",
    "torch": "model",
    "safetensors": "model",
    "compute": "compute",
}

#: 探测类型 → access.protocol（沿用 OGC 适配器的词表）
_FORMAT = {
    "geotiff": "GeoTIFF",
    "csv": "CSV",
    "geojson": "GeoJSON",
    "onnx": "ONNX",
    "torch": "torch-script",
    "safetensors": "safetensors",
}

KIND_PROTOCOL = {
    "ogc-features": "ogcapi",
    "ogc-coverages": "ogcapi",
    "ogc-tiles": "ogcapi",
    "ogc-processes": "ogcapi",
    "wms": "wms",
    "stac": "stac",
    "geotiff": "file",
    "csv": "file",
    "geojson": "file",
    "onnx": "file",
    "torch": "file",
    "safetensors": "file",
    "compute": "geomcp",
}


def _slug(text: str, maxlen: int = 32) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (s or "asset")[:maxlen].strip("-") or "asset"


def _hash8(target: str) -> str:
    return hashlib.sha256(target.encode("utf-8")).hexdigest()[:8]


def build_card(
    probe: ProbeResult, overrides: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """返回 (卡片草稿,  warnings)。warnings 是给向导/界面看的"还缺什么"。"""
    o = overrides or {}
    kind = probe.kind
    ctype = KIND_TYPE.get(kind, "data")
    title = (
        o.get("name") or probe.facts.get("title") or Path(str(probe.target)).name or probe.target
    )
    card: dict[str, Any] = {
        "geocard_version": "1.0",
        "id": o.get("id")
        or f"geocard.imported.{_slug(kind)}-{_slug(title)}-{_hash8(probe.target)}",
        "type": ctype,
        "name": title,
        "description": o.get("description")
        or probe.facts.get("description")
        or f"由 geonexus register 探测登记（{kind}）：{probe.target}",
        "tags": sorted({"imported", kind, *(o.get("tags") or [])}),
        "compliance": {"sensitivity": o.get("sensitivity") or "public"},
        "provenance": {
            "provider": o.get("provider") or "geonexus-register",
            "source": probe.target,
        },
        "access": {
            "protocol": KIND_PROTOCOL.get(kind, "file"),
            "endpoint": probe.target,
            **({"format": _FORMAT.get(kind)} if kind in _FORMAT else {}),
        },
    }
    if o.get("license"):
        card["license"] = {"name": o["license"]}

    warnings = list(probe.warnings)
    bbox = probe.facts.get("bbox")
    if isinstance(bbox, list) and len(bbox) == 4:
        card["spatial"] = {
            "bbox": [round(float(v), 6) for v in bbox],
            "crs": o.get("crs") or probe.facts.get("crs") or "EPSG:4326",
        }
    else:
        warnings.append("未探测到空间范围：卡片不带 spatial，将按非空间资产展示（侧栏）")
    if not o.get("license"):
        warnings.append("未声明许可（license）：默认按 private 对待，发布前必须人工确认")
    return card, warnings


def validate_card(card: dict[str, Any]) -> list[str]:
    """用 GeoCard schema 真校验一遍，返回问题列表（空 = 通过）。"""
    from geonexus.geocard import GeoCard

    try:
        GeoCard.model_validate(card).validate()
        return []
    except Exception as exc:  # noqa: BLE001 - 校验失败要变成可读清单，不是栈
        msg = str(exc)
        return [line.strip() for line in msg.splitlines() if line.strip()][:12]
