"""GeoKG 存储层 — gzip 压缩 + NDJSON 增量追加。

三种格式，按扩展名自动选择：

    geokg.json          紧凑 JSON（全量重写）
    geokg.json.gz       gzip 压缩 JSON（体积约 1/8）
    geokg.ndjson        NDJSON 行式（可追加，流式加载）
    geokg.ndjson.gz     gzip 压缩 NDJSON（推荐：体积小 + 可追加）

NDJSON 每行一个记录，首行为 header：

    {"_type":"header","name":"geonexus","version":1}
    {"_type":"entity","id":"country.KEN","type":"Country","properties":{...},"labels":[...]}
    {"_type":"relation","source":"...","target":"...","relation":"LOCATED_IN","properties":{}}

追加时只需 append 新行，无需重写全文件。
"""

from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1


# --------------------------------------------------------------------------- #
# 格式探测
# --------------------------------------------------------------------------- #
def detect_format(path: str | Path) -> tuple[str, bool]:
    """返回 (format, compressed)。

    format ∈ {"json", "ndjson"}
    """
    name = str(path).lower()
    compressed = name.endswith(".gz")
    if compressed:
        name = name[:-3]
    fmt = "ndjson" if name.endswith(".ndjson") else "json"
    return fmt, compressed


def _open_write(path: Path, compressed: bool):
    if compressed:
        return gzip.open(path, "wt", encoding="utf-8", compresslevel=6)
    return open(path, "w", encoding="utf-8")


def _open_read(path: Path, compressed: bool):
    if compressed:
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def _open_append(path: Path, compressed: bool):
    """以追加模式打开 gzip 文件。

    gzip 支持多成员流（multi-member），因此附加一个新的 gzip 成员
    即可实现追加语义，读取时按顺序解压所有成员。
    """
    return gzip.open(path, "at", encoding="utf-8", compresslevel=6)


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #
def save_graph(
    path: str | Path,
    name: str,
    entities: Iterable[dict[str, Any]],
    relations: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """全量保存图谱。返回统计信息。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fmt, compressed = detect_format(p)

    n_ent = 0
    n_rel = 0

    if fmt == "ndjson":
        with _open_write(p, compressed) as fh:
            fh.write(json.dumps(
                {"_type": "header", "name": name, "version": FORMAT_VERSION},
                ensure_ascii=False,
            ) + "\n")
            for e in entities:
                fh.write(json.dumps({"_type": "entity", **e}, ensure_ascii=False) + "\n")
                n_ent += 1
            for r in relations:
                fh.write(json.dumps({"_type": "relation", **r}, ensure_ascii=False) + "\n")
                n_rel += 1
    else:
        ent_list = list(entities)
        rel_list = list(relations)
        n_ent, n_rel = len(ent_list), len(rel_list)
        payload = {"name": name, "version": FORMAT_VERSION,
                   "entities": ent_list, "relations": rel_list}
        with _open_write(p, compressed) as fh:
            json.dump(payload, fh, ensure_ascii=False)

    size = p.stat().st_size
    logger.info("KG saved: %d entities, %d relations → %s (%.2f MB)",
                n_ent, n_rel, path, size / 1024 / 1024)
    return {
        "path": str(p),
        "format": fmt,
        "compressed": compressed,
        "entities": n_ent,
        "relations": n_rel,
        "bytes": size,
    }


def append_graph(
    path: str | Path,
    entities: Iterable[dict[str, Any]] = (),
    relations: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """增量追加实体/关系到 NDJSON 文件（不重写已有内容）。

    仅支持 NDJSON 格式（.ndjson / .ndjson.gz）。文件不存在时自动建表头。
    """
    p = Path(path)
    fmt, compressed = detect_format(p)
    if fmt != "ndjson":
        raise ValueError(
            f"增量追加仅支持 NDJSON 格式（.ndjson/.ndjson.gz），当前: {path}"
        )

    existed = p.exists() and p.stat().st_size > 0
    p.parent.mkdir(parents=True, exist_ok=True)

    n_ent = n_rel = 0
    mode = _open_append(p, compressed) if existed else _open_write(p, compressed)
    with mode as fh:
        if not existed:
            fh.write(json.dumps(
                {"_type": "header", "name": p.stem.split(".")[0], "version": FORMAT_VERSION},
                ensure_ascii=False,
            ) + "\n")
        for e in entities:
            fh.write(json.dumps({"_type": "entity", **e}, ensure_ascii=False) + "\n")
            n_ent += 1
        for r in relations:
            fh.write(json.dumps({"_type": "relation", **r}, ensure_ascii=False) + "\n")
            n_rel += 1

    logger.info("KG appended: +%d entities, +%d relations → %s", n_ent, n_rel, path)
    return {"appended_entities": n_ent, "appended_relations": n_rel,
            "bytes": p.stat().st_size}


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #
def load_graph(path: str | Path) -> tuple[str, list[dict], list[dict]]:
    """加载图谱，返回 (name, entities, relations)。"""
    p = Path(path)
    fmt, compressed = detect_format(p)

    if fmt == "ndjson":
        return _load_ndjson(p, compressed)
    return _load_json(p, compressed)


def _load_json(p: Path, compressed: bool) -> tuple[str, list[dict], list[dict]]:
    with _open_read(p, compressed) as fh:
        data = json.load(fh)
    return (
        data.get("name", "geokg"),
        data.get("entities", []),
        data.get("relations", []),
    )


def _load_ndjson(p: Path, compressed: bool) -> tuple[str, list[dict], list[dict]]:
    name = "geokg"
    entities: list[dict] = []
    relations: list[dict] = []
    with _open_read(p, compressed) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("跳过损坏行: %.60s", line)
                continue
            kind = rec.pop("_type", None)
            if kind == "header":
                name = rec.get("name", name)
            elif kind == "entity":
                entities.append(rec)
            elif kind == "relation":
                relations.append(rec)
    return name, entities, relations


def iter_entities(path: str | Path) -> Iterator[dict[str, Any]]:
    """流式迭代实体（不把全图载入内存）。"""
    p = Path(path)
    fmt, compressed = detect_format(p)
    if fmt != "ndjson":
        _, ents, _ = load_graph(p)
        yield from ents
        return
    with _open_read(p, compressed) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("_type") == "entity":
                rec.pop("_type")
                yield rec


def storage_stats(path: str | Path) -> dict[str, Any]:
    """返回文件存储统计。"""
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    fmt, compressed = detect_format(p)
    return {
        "exists": True,
        "path": str(p),
        "format": fmt,
        "compressed": compressed,
        "bytes": p.stat().st_size,
        "megabytes": round(p.stat().st_size / 1024 / 1024, 2),
    }