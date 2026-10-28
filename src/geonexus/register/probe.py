"""探测：给一个 URL 或文件路径，判断它是什么、能取到哪些事实。

只读、不执行。每种探测都返回 (kind, facts, warnings)：
取不到的事实进 warnings，绝不编造默认值填进去。
"""

from __future__ import annotations

import csv as _csv
import io
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

TIMEOUT = 10.0

#: 识别为坐标列的表头名（小写比较）
LAT_NAMES = {"lat", "latitude", "y", "lat_dd", "center_lat"}
LON_NAMES = {"lon", "lng", "long", "longitude", "x", "lon_dd", "center_lon"}
GEOM_NAMES = {"geom", "geometry", "wkt", "the_geom", "shape"}


@dataclass
class ProbeResult:
    kind: str  # ogc-features / ogc-coverages / ogc-tiles / ogc-processes /
    # wms / stac / geotiff / csv / geojson / onnx / torch /
    # safetensors / compute / unknown
    target: str
    facts: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "facts": self.facts,
            "warnings": self.warnings,
        }


def probe_target(target: str, timeout: float = TIMEOUT) -> ProbeResult:
    """URL 走网络探测（只 GET），本地路径走文件嗅探。"""
    if re.match(r"^https?://", str(target), re.I):
        return _probe_url(str(target), timeout)
    return _probe_file(Path(str(target)).expanduser())


# --------------------------------------------------------------------------- #
# URL 探测
# --------------------------------------------------------------------------- #
def _get(url: str, timeout: float, params: dict | None = None):
    try:
        r = httpx.get(url, params=params, timeout=timeout, follow_redirects=True)
        return r
    except Exception:  # noqa: BLE001 - 探测失败是常态，交给调用方降级
        return None


def _probe_url(url: str, timeout: float) -> ProbeResult:
    warnings: list[str] = []

    # 0) 是不是一个 GeoNexus 节点（算力/能力源）
    health = _get(url.rstrip("/") + "/health", timeout)
    caps = _get(url.rstrip("/") + "/capabilities", timeout)
    if (
        health is not None
        and caps is not None
        and health.status_code == 200
        and caps.status_code == 200
    ):
        try:
            h = health.json()
            c = caps.json()
            if h.get("status") == "ok" and "methods" in c:
                return ProbeResult(
                    "compute",
                    url,
                    {
                        "title": c.get("node") or h.get("node"),
                        "description": f"GeoNexus 能力节点（{len(c.get('skills', []))} 个技能）",
                        "methods": c.get("methods", []),
                        "skills": [s.get("name") for s in c.get("skills", [])],
                    },
                    warnings,
                )
        except Exception:  # noqa: BLE001
            warnings.append("节点 health/capabilities 响应不是预期 JSON，继续按服务探测")

    # 1) OGC API：landing / conformance / collections
    landing = _get(url, timeout)
    conf = _get(url.rstrip("/") + "/conformance", timeout)
    classes = ""
    if conf is not None and conf.status_code == 200:
        try:
            classes = " ".join(conf.json().get("conformsTo", []))
        except Exception:  # noqa: BLE001
            classes = ""
    kind = None
    if classes:
        if "ogcapi-processes" in classes:
            kind = "ogc-processes"
        elif "ogcapi-coverages" in classes:
            kind = "ogc-coverages"
        elif "ogcapi-tiles" in classes:
            kind = "ogc-tiles"
        elif "ogcapi-features" in classes:
            kind = "ogc-features"
    facts: dict[str, Any] = {}
    if kind:
        colls = _get(url.rstrip("/") + "/collections", timeout)
        if colls is not None and colls.status_code == 200:
            try:
                items = colls.json().get("collections", [])
                if items:
                    first = items[0]
                    facts["title"] = first.get("title") or first.get("id")
                    facts["description"] = first.get("description") or ""
                    bbox = (
                        ((first.get("extent") or {}).get("spatial") or {}).get("bbox") or [[None]]
                    )[0]
                    if len(bbox) == 4 and all(v is not None for v in bbox):
                        facts["bbox"] = [float(v) for v in bbox]
                    facts["collections"] = [c.get("id") for c in items][:20]
            except Exception:  # noqa: BLE001
                warnings.append("collections 解析失败，仅登记服务级信息")
        return ProbeResult(kind, url, facts, warnings)

    # 2) STAC：landing 是 Catalog/Collection，或带 stac_version
    if landing is not None and landing.status_code == 200:
        try:
            d = landing.json()
            if isinstance(d, dict) and (
                d.get("type") in ("Catalog", "Collection") or "stac_version" in d
            ):
                bbox = (((d.get("extent") or {}).get("spatial") or {}).get("bbox") or [[None]])[0]
                if len(bbox) == 4 and all(v is not None for v in bbox):
                    facts["bbox"] = [float(v) for v in bbox]
                facts["title"] = d.get("title") or d.get("id")
                facts["description"] = d.get("description") or ""
                return ProbeResult("stac", url, facts, warnings)
        except Exception:  # noqa: BLE001
            pass

    # 3) WMS/WMTS：GetCapabilities 是 XML
    wms = _get(url, timeout, params={"service": "WMS", "request": "GetCapabilities"})
    if wms is not None and wms.status_code == 200 and b"WMS_Capabilities" in wms.content[:4000]:
        m = re.search(
            rb"<EX_GeographicBoundingBox>\s*<westBC>([-\d.]+)</westBC>\s*"
            rb"<southBC>([-\d.]+)</southBC>\s*<eastBC>([-\d.]+)</eastBC>\s*"
            rb"<northBC>([-\d.]+)</northBC>",
            wms.content,
        )
        if m:
            facts["bbox"] = [
                float(m.group(1)),
                float(m.group(2)),
                float(m.group(3)),
                float(m.group(4)),
            ]
        t = re.search(rb"<Title[^>]*>([^<]{1,120})</Title>", wms.content)
        if t:
            facts["title"] = t.group(1).decode("utf-8", "replace").strip()
        return ProbeResult("wms", url, facts, warnings)

    warnings.append(
        "未能识别服务类型（试过 GeoNode/OGC API/STAC/WMS）；仍可按 unknown 登记，但需人工补字段"
    )
    return ProbeResult("unknown", url, facts, warnings)


# --------------------------------------------------------------------------- #
# 文件探测
# --------------------------------------------------------------------------- #
def _probe_file(path: Path) -> ProbeResult:
    warnings: list[str] = []
    if not path.exists():
        return ProbeResult("unknown", str(path), {}, [f"文件不存在：{path}"])
    head = path.read_bytes()[:16]
    size = path.stat().st_size

    # GeoTIFF
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        facts: dict[str, Any] = {"size_bytes": size}
        try:
            import rasterio  # 可选依赖：没有就只登记大小

            with rasterio.open(path) as src:
                b = src.bounds
                facts["bbox"] = [b.left, b.bottom, b.right, b.top]
                facts["crs"] = str(src.crs) if src.crs else None
                facts["bands"] = src.count
                facts["shape"] = list(src.shape)
        except ImportError:
            warnings.append("未安装 rasterio：GeoTIFF 只登记大小，bbox/CRS 需人工补")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"rasterio 读取失败（{exc}）：bbox/CRS 需人工补")
        return ProbeResult("geotiff", str(path), facts, warnings)

    # safetensors：头部是长度前缀 JSON，可安全解析（不碰权重）
    if path.suffix == ".safetensors" and len(head) >= 8:
        (n,) = struct.unpack("<Q", head[:8])
        facts = {"size_bytes": size}
        try:
            meta = json.loads(path.read_bytes()[8 : 8 + min(n, 1_000_000)])
            if isinstance(meta, dict):
                names = [k for k in meta if k != "__metadata__"]
                facts["tensor_names"] = names[:20]
                facts["tensors"] = {
                    k: meta[k].get("dtype") for k in names[:20] if isinstance(meta.get(k), dict)
                }
        except Exception:  # noqa: BLE001
            warnings.append("safetensors 头解析失败，仅登记大小")
        return ProbeResult("safetensors", str(path), facts, warnings)

    if path.suffix in (".onnx",):
        # 不导入 onnx（重依赖）：只登记大小与文件名，权重结构留给人工或后续工具
        return ProbeResult(
            "onnx",
            str(path),
            {"size_bytes": size},
            ["ONNX 仅登记大小与文件名（不解析图结构，避免重依赖与执行风险）"],
        )
    if path.suffix in (".pt", ".pth", ".bin"):
        return ProbeResult(
            "torch",
            str(path),
            {"size_bytes": size},
            ["torch 权重**不做加载**（加载即执行代码）；仅登记大小，结构需人工说明"],
        )

    # GeoJSON / JSON
    if path.suffix in (".json", ".geojson"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return ProbeResult("unknown", str(path), {}, [f"JSON 解析失败：{exc}"])
        if isinstance(d, dict) and d.get("type") in ("Feature", "FeatureCollection"):
            feats = d.get("features", [d] if d.get("type") == "Feature" else [])
            bbox = _bbox_from_features(feats)
            facts = {"features": len(feats)}
            if bbox:
                facts["bbox"] = bbox
            else:
                warnings.append("GeoJSON 里没有可解析的坐标，不上地球（侧栏展示）")
            return ProbeResult("geojson", str(path), facts, warnings)
        return ProbeResult(
            "unknown", str(path), {}, ["JSON 不是 GeoJSON Feature/FeatureCollection"]
        )

    # CSV / TSV
    if path.suffix in (".csv", ".tsv", ".txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            sample = _csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
            delim = sample.delimiter
        except Exception:  # noqa: BLE001
            delim = ","
        reader = _csv.DictReader(io.StringIO(text), delimiter=delim)
        header = [(h or "").strip() for h in (reader.fieldnames or [])]
        low = {h.lower(): h for h in header}
        lat = next((low[k] for k in low if k in LAT_NAMES), None)
        lon = next((low[k] for k in low if k in LON_NAMES), None)
        geom = next((low[k] for k in low if k in GEOM_NAMES), None)
        facts = {"fields": header[:40], "delimiter": delim}
        if lat and lon:
            bbox, n = _bbox_from_csv(text, delim, lat, lon)
            if bbox:
                facts["bbox"] = bbox
                facts["rows_scanned"] = n
            else:
                warnings.append("有经纬度列但无数值行，bbox 留空")
        elif geom:
            warnings.append(f"发现几何列 {geom}（WKT）：本工具不解析 WKT，bbox 留空，可人工补")
        else:
            warnings.append("未发现经纬度/几何列：登记为非空间表格（侧栏展示）")
        return ProbeResult("csv", str(path), facts, warnings)

    return ProbeResult(
        "unknown", str(path), {"size_bytes": size}, ["未识别的文件类型；可人工补字段后登记"]
    )


def _bbox_from_csv(text: str, delim: str, lat: str, lon: str, limit: int = 20000):
    xs: list[float] = []
    ys: list[float] = []
    reader = _csv.DictReader(io.StringIO(text), delimiter=delim)
    n = 0
    for row in reader:
        n += 1
        if n > limit:
            break
        try:
            ys.append(float(row[lat]))
            xs.append(float(row[lon]))
        except (TypeError, ValueError):
            continue
    if not xs:
        return None, n
    return [min(xs), min(ys), max(xs), max(ys)], n


def _bbox_from_features(feats: list[dict]) -> list[float] | None:
    xs: list[float] = []
    ys: list[float] = []

    def walk(g: Any) -> None:
        if isinstance(g, dict):
            if g.get("type") == "GeometryCollection":
                for sub in g.get("geometries", []):
                    walk(sub)
            elif isinstance(g.get("coordinates"), list):
                _collect(g["coordinates"], xs, ys)
        elif isinstance(g, list):
            for item in g:
                walk(item)

    def _collect(coords: Any, xs: list, ys: list) -> None:
        if isinstance(coords, (int, float)):
            return
        if len(coords) >= 2 and all(isinstance(v, (int, float)) for v in coords[:2]):
            xs.append(float(coords[0]))
            ys.append(float(coords[1]))
            return
        for c in coords:
            _collect(c, xs, ys)

    for f in feats:
        if isinstance(f, dict):
            if isinstance(f.get("bbox"), list) and len(f["bbox"]) == 4:
                xs += [f["bbox"][0], f["bbox"][2]]
                ys += [f["bbox"][1], f["bbox"][3]]
                continue
            walk(f.get("geometry"))
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]
