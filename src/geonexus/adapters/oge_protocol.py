"""OGE 协议映射器 — OGE 数据引用协议 ↔ GeoCard 引用互转。

OGE 使用字符串表示数据引用，如：
  Personal:MyData:myData/文件名  — 用户上传的数据
  Personal:Asset:{assetUuid}      — 资产引用
  Personal:Process:{processId}    — 中间结果引用
  Platform:Coverage:{id}:{product} — 平台影像
  Platform:Product:{productName}  — 平台产品
  Tms:{tileUrl}                   — 瓦片引用

GeoCard 使用 card_id 表示数据引用。

本模块提供双向映射，使 OGE 和 GeoNexus 之间可以互相引用数据。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── 引用类型 ──

class OgeReferenceType:
    """OGE 引用协议类型常量。"""
    MY_DATA = "Personal:MyData"       # 用户上传数据
    ASSET = "Personal:Asset"          # 资产
    PROCESS = "Personal:Process"      # 中间结果
    COVERAGE = "Platform:Coverage"    # 平台影像
    PRODUCT = "Platform:Product"      # 平台产品
    TMS = "Tms"                       # 瓦片


@dataclass
class OgeReference:
    """OGE 引用解析结果。"""
    ref_type: str          # 如 Personal:MyData
    raw: str               # 原始引用字符串
    parts: list[str]       # 拆分后的各部分

    # 便捷属性
    @property
    def is_user_data(self) -> bool:
        return self.ref_type == OgeReferenceType.MY_DATA

    @property
    def is_asset(self) -> bool:
        return self.ref_type == OgeReferenceType.ASSET

    @property
    def is_process(self) -> bool:
        return self.ref_type == OgeReferenceType.PROCESS

    @property
    def is_platform_coverage(self) -> bool:
        return self.ref_type == OgeReferenceType.COVERAGE

    @property
    def is_platform_product(self) -> bool:
        return self.ref_type == OgeReferenceType.PRODUCT


# ── 映射器 ──

class OgeProtocolMapper:
    """OGE 引用协议 ↔ GeoCard 引用映射。

    使用方式：
        mapper = OgeProtocolMapper()
        ref = mapper.parse("Personal:MyData:myData/landsat.tif")
        # ref.ref_type = "Personal:MyData", ref.parts = ["myData", "landsat.tif"]

        card_id = mapper.to_geocard_id(ref)
        # "oge-user-data:myData/landsat.tif"

        oge_ref = mapper.to_oge_reference("oge-user-data:myData/landsat.tif")
        # "Personal:MyData:myData/landsat.tif"
    """

    # GeoCard ID 前缀 → OGE 引用类型
    _CARD_PREFIX_MAP: dict[str, str] = {
        "oge-user-data": OgeReferenceType.MY_DATA,
        "oge-asset": OgeReferenceType.ASSET,
        "oge-process": OgeReferenceType.PROCESS,
        "oge-coverage": OgeReferenceType.COVERAGE,
        "oge-product": OgeReferenceType.PRODUCT,
        "oge-tms": OgeReferenceType.TMS,
    }

    # OGE 引用类型 → GeoCard ID 前缀
    _OGE_TO_CARD_PREFIX = {v: k for k, v in _CARD_PREFIX_MAP.items()}

    # OGE 引用正则
    _OGE_REF_PATTERN = re.compile(
        r"^(Personal:(MyData|Asset|Process)|Platform:(Coverage|Product)|Tms):(.+)$"
    )

    # GeoCard ID 正则（oge-xxx:xxx）
    _CARD_ID_PATTERN = re.compile(
        r"^(oge-(?:user-data|asset|process|coverage|product|tms)):(.+)$"
    )

    def parse(self, oge_reference: str) -> OgeReference:
        """解析 OGE 引用字符串。"""
        m = self._OGE_REF_PATTERN.match(oge_reference)
        if not m:
            raise ValueError(f"Invalid OGE reference: {oge_reference}")

        ref_type = m.group(1)
        # 对于 Personal:MyData:xxx → ref_type 是 "Personal:MyData"
        # 需要修正
        if m.group(2):
            if m.group(2) == "MyData":
                ref_type = "Personal:MyData"
            elif m.group(2) == "Asset":
                ref_type = "Personal:Asset"
            elif m.group(2) == "Process":
                ref_type = "Personal:Process"
        if m.group(3):
            if m.group(3) == "Coverage":
                ref_type = "Platform:Coverage"
            elif m.group(3) == "Product":
                ref_type = "Platform:Product"

        rest = m.group(4) if m.group(4) else ""
        parts = rest.split(":") if ":" in rest else rest.split("/")

        return OgeReference(ref_type=ref_type, raw=oge_reference, parts=parts)

    def to_geocard_id(self, ref: OgeReference) -> str:
        """OGE 引用 → GeoCard ID。"""
        prefix = self._OGE_TO_CARD_PREFIX.get(ref.ref_type)
        if not prefix:
            raise ValueError(f"Unknown OGE reference type: {ref.ref_type}")

        suffix = ":".join(ref.parts) if isinstance(ref.parts, list) else ref.parts
        return f"{prefix}:{suffix}"

    def to_oge_reference(self, geocard_id: str) -> str:
        """GeoCard ID → OGE 引用。"""
        m = self._CARD_ID_PATTERN.match(geocard_id)
        if not m:
            raise ValueError(f"Invalid GeoCard ID format: {geocard_id}")

        prefix = m.group(1)
        suffix = m.group(2)
        oge_type = self._CARD_PREFIX_MAP.get(prefix)

        if not oge_type:
            raise ValueError(f"Unknown GeoCard prefix: {prefix}")

        return f"{oge_type}:{suffix}"

    def is_oge_card(self, geocard_id: str) -> bool:
        """判断 GeoCard ID 是否指向 OGE 数据。"""
        return bool(self._CARD_ID_PATTERN.match(geocard_id))


# ── 便捷函数 ──

def parse_oge_reference(ref: str) -> OgeReference:
    return OgeProtocolMapper().parse(ref)


def oge_ref_to_geocard_id(ref: str) -> str:
    return OgeProtocolMapper().to_geocard_id(parse_oge_reference(ref))


def geocard_id_to_oge_ref(card_id: str) -> str:
    return OgeProtocolMapper().to_oge_reference(card_id)