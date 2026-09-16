"""GAAG — Geo Asset Contract Registry.

GAAG 合约注册中心（参考 AutoGIS GAAG 架构）为每个地理空间资产生成
机器可读的 Data Contract：CRS/bbox/bands/temporal/provenance + 语义向量，
并支持合约满足度门控（Contract Satisfaction Gating）。

模块组成:
- :mod:`geonexus.gaag.contract` — GAAGContract 数据模型
- :mod:`geonexus.gaag.scanner` — scan 流水线: 文件 → 元数据 → GeoCard
- :mod:`geonexus.gaag.embed` — 语义向量嵌入（可插拔 LLM / 确定性特征）
- :mod:`geonexus.gaag.registry` — 注册中心 + 语义检索 + 合约门控
"""

from .contract import GAAGContract, GAAGContractSpec, GAAGError
from .contract_types import (
    CONTRACT_SCHEMAS,
    ContractCheckResult,
    ContractSchema,
    contract_coverage,
    detect_asset_type,
    scan_pointcloud,
    scan_statistical,
    scan_temporal,
    scan_text,
    scan_volume3d,
    validate_contract,
)
from .embed import FeatureEmbedder, cosine_similarity, embed_card, embed_text
from .registry import ContractGate, ContractGateResult, GAAGRegistry
from .scanner import (
    RasterScanner,
    VectorScanner,
    scan_asset_to_contract,
    scan_raster,
    scan_vector,
)

__all__ = [
    "GAAGContract",
    "GAAGContractSpec",
    "GAAGError",
    "GAAGRegistry",
    "ContractGate",
    "ContractGateResult",
    "RasterScanner",
    "VectorScanner",
    "scan_asset_to_contract",
    "scan_raster",
    "scan_vector",
    "embed_card",
    "embed_text",
    "cosine_similarity",
    "FeatureEmbedder",
    # 合约类型扩展
    "CONTRACT_SCHEMAS",
    "ContractSchema",
    "ContractCheckResult",
    "detect_asset_type",
    "validate_contract",
    "contract_coverage",
    "scan_temporal",
    "scan_volume3d",
    "scan_pointcloud",
    "scan_statistical",
    "scan_text",
]