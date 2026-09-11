"""OGE 算子 → GeoCard 构建器。

从 OGE 算子定义自动生成 GeoCard（operator_card 类型），
使 OGE 能力可通过 GeoCard 发现和契约校验。

OGE 算子（如 Coverage.terrSlope）被映射为 GeoCard：
  type: skill
  capabilities: ["terrain-slope"]
  inputs: [coverage, outputName]
  outputs: [slope_raster, process_id]
  access.protocol: "oge"
"""

from __future__ import annotations

from typing import Any

from geonexus.geocard import GeoCardBuilder


class OgeCardBuilder:
    """OGE 算子 → GeoCard 构建器。

    使用方式：
        builder = OgeCardBuilder()
        card = builder.build_operator_card(
            operator_name="Coverage.terrSlope",
            description="Compute terrain slope from DEM",
            inputs=[{"name": "coverage", "type": "string", "required": True}],
            outputs=[{"name": "slope_raster", "type": "raster"}],
        )
        card.validate()
    """

    # OGE 算子 → GeoCard 能力名映射
    OPERATOR_CAPABILITY_MAP: dict[str, list[str]] = {
        "Coverage.terrSlope": ["terrain-slope", "terrain-analysis"],
        "Coverage.terrAspect": ["terrain-aspect", "terrain-analysis"],
        "Coverage.terrHillshade": ["terrain-hillshade", "terrain-analysis"],
        "Coverage.terrRoughness": ["terrain-roughness", "terrain-analysis"],
        "Coverage.ndvi": ["ndvi", "vegetation-analysis"],
    }

    def __init__(self, oge_endpoint: str = "http://openge.org.cn/api") -> None:
        self.oge_endpoint = oge_endpoint

    def build_operator_card(self, operator_name: str, description: str = "",
                            inputs: list[dict[str, Any]] | None = None,
                            outputs: list[dict[str, Any]] | None = None) -> Any:
        """从 OGE 算子定义构建 operator_card。

        Args:
            operator_name: 算子全名，如 "Coverage.terrSlope"
            description: 算子描述
            inputs: 输入参数定义
            outputs: 输出参数定义

        Returns:
            GeoCard 实例
        """
        card_id = self._operator_to_card_id(operator_name)
        display_name = self._operator_to_display_name(operator_name)
        capabilities = self.OPERATOR_CAPABILITY_MAP.get(operator_name, [operator_name.lower()])

        builder = GeoCardBuilder(
            id=card_id,
            type="skill",
            name=display_name,
            description=description or f"OGE operator: {operator_name}",
        )

        # 能力
        for cap in capabilities:
            builder.capability(cap)

        # 输入
        for inp in (inputs or []):
            builder.input(
                name=inp.get("name", "arg"),
                type=inp.get("type", "string"),
                required=inp.get("required", False),
            )

        # 输出
        for out in (outputs or []):
            builder.output(
                name=out.get("name", "result"),
                type=out.get("type", "raster"),
            )

        # 访问协议
        builder.access(
            protocol="oge",
            endpoint=f"{self.oge_endpoint}/openapi/algorithm/{operator_name.replace('.', '/')}",
            auth="tk",
        )

        # 接口声明
        builder.interface(type="oge-skill", version="1.0")

        # 标签
        builder.tag("oge", *capabilities)

        return builder.build()

    def build_model_card(self, operator_name: str, description: str = "",
                         runtime: dict[str, Any] | None = None) -> Any:
        """构建 OGE 算子的 model_card（用于需要 GPU 等资源的场景）。"""
        card_id = f"oge-model-{operator_name.replace('.', '-').lower()}"
        capabilities = self.OPERATOR_CAPABILITY_MAP.get(operator_name, [operator_name.lower()])

        builder = GeoCardBuilder(
            id=card_id,
            type="model",
            name=f"OGE {operator_name}",
            description=description or f"OGE model: {operator_name}",
        )
        for cap in capabilities:
            builder.capability(cap)
        builder.access(protocol="oge", endpoint=self.oge_endpoint, auth="tk")
        builder.interface(type="oge-model", version="1.0")
        if runtime:
            builder.runtime(**runtime)
        builder.tag("oge", "model", *capabilities)
        return builder.build()

    # ── 工具方法 ──

    def _operator_to_card_id(self, operator_name: str) -> str:
        """Coverage.terrSlope → oge-operator:coverage-terrslope"""
        name = operator_name.replace(".", "-").lower()
        return f"oge-operator:{name}"

    def _operator_to_display_name(self, operator_name: str) -> str:
        """Coverage.terrSlope → Terrain Slope"""
        # 去掉包名，驼峰分词
        parts = operator_name.split(".")
        op_name = parts[-1] if len(parts) > 1 else parts[0]
        # 简单分词
        import re
        words = re.findall(r'[A-Z][a-z]*', op_name)
        if not words:
            words = [op_name]

        # 已知算子名映射
        name_map = {
            "terrSlope": "Terrain Slope",
            "terrAspect": "Terrain Aspect",
            "terrHillshade": "Terrain Hillshade",
            "terrRoughness": "Terrain Roughness",
            "ndvi": "NDVI",
        }
        return name_map.get(op_name, " ".join(words))


def build_operator_card(operator_name: str, **kwargs: Any) -> Any:
    """便捷函数：构建 OGE operator_card。"""
    return OgeCardBuilder().build_operator_card(operator_name, **kwargs)


def build_model_card(operator_name: str, **kwargs: Any) -> Any:
    """便捷函数：构建 OGE model_card。"""
    return OgeCardBuilder().build_model_card(operator_name, **kwargs)