"""OGE 算子 → GeoSkill 适配器。

将 OGE 算子包装为 GeoSkill，使 Java 后台可以通过 GeoMCP 统一调用。
OGE 算子的执行在 Python 执行面完成，对调用方透明。

使用方式：
    adapter = OgeSkillAdapter(oge_endpoint="http://openge.org.cn/api")
    skills = adapter.discover_skills()       # 发现所有 OGE 算子 → GeoSkill
    skill = adapter.make_skill("Coverage.terrSlope")  # 单个算子 → GeoSkill
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from geonexus.geonode import Skill

from .oge_client import OgeClient
from .oge_credential import OgeCredential
from .oge_executor import OgeExecutor
from .oge_protocol import OgeProtocolMapper

logger = logging.getLogger(__name__)


class OgeSkillAdapter:
    """OGE 算子 → GeoSkill 适配器。

    将 OGE 算子包装为 GeoSkill，集成到 GeoMCP 协议中。
    调用方通过 geo.execute(skill="terrain-slope", params={...}) 执行，
    不需要知道背后是 OGE 平台。
    """

    # 已知 OGE 算子 → 技能名映射
    KNOWN_OPERATORS: dict[str, dict[str, Any]] = {
        "Coverage.terrSlope": {
            "skill_name": "terrain-slope",
            "description": "Compute terrain slope from DEM (via OGE Coverage.terrSlope)",
            "input_schema": {
                "type": "object",
                "required": ["coverage"],
                "properties": {
                    "coverage": {"type": "string", "description": "DEM coverage reference"},
                    "outputName": {"type": "string", "description": "Output raster name"},
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "slope_raster": {"type": "string", "description": "Slope raster path"},
                    "process_id": {"type": "string", "description": "OGE process ID"},
                },
            },
        },
        "Coverage.terrAspect": {
            "skill_name": "terrain-aspect",
            "description": "Compute terrain aspect from DEM (via OGE Coverage.terrAspect)",
            "input_schema": {
                "type": "object",
                "required": ["coverage"],
                "properties": {
                    "coverage": {"type": "string", "description": "DEM coverage reference"},
                    "outputName": {"type": "string", "description": "Output raster name"},
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "aspect_raster": {"type": "string", "description": "Aspect raster path"},
                    "process_id": {"type": "string", "description": "OGE process ID"},
                },
            },
        },
        "Coverage.terrHillshade": {
            "skill_name": "terrain-hillshade",
            "description": "Compute hillshade from DEM (via OGE Coverage.terrHillshade)",
            "input_schema": {
                "type": "object",
                "required": ["coverage"],
                "properties": {
                    "coverage": {"type": "string", "description": "DEM coverage reference"},
                    "outputName": {"type": "string", "description": "Output raster name"},
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "hillshade_raster": {"type": "string", "description": "Hillshade raster path"},
                    "process_id": {"type": "string", "description": "OGE process ID"},
                },
            },
        },
        "Coverage.terrRoughness": {
            "skill_name": "terrain-roughness",
            "description": "Compute terrain roughness (via OGE Coverage.terrRoughness)",
            "input_schema": {
                "type": "object",
                "required": ["coverage"],
                "properties": {
                    "coverage": {"type": "string", "description": "DEM coverage reference"},
                    "outputName": {"type": "string", "description": "Output raster name"},
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "roughness_raster": {"type": "string", "description": "Roughness raster path"},
                    "process_id": {"type": "string", "description": "OGE process ID"},
                },
            },
        },
        "Coverage.ndvi": {
            "skill_name": "oge-ndvi",
            "description": "Compute NDVI via OGE platform",
            "input_schema": {
                "type": "object",
                "required": ["coverage"],
                "properties": {
                    "coverage": {"type": "string", "description": "Red/NIR coverage reference"},
                    "outputName": {"type": "string", "description": "Output raster name"},
                },
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "ndvi_raster": {"type": "string", "description": "NDVI raster path"},
                    "process_id": {"type": "string", "description": "OGE process ID"},
                },
            },
        },
    }

    def __init__(self, credential: OgeCredential | None = None,
                 audit_callback: Callable[[dict], None] | None = None) -> None:
        self.credential = credential or OgeCredential()
        self.audit_callback = audit_callback
        self._oge_client: OgeClient | None = None
        self._executor: OgeExecutor | None = None
        self._protocol_mapper = OgeProtocolMapper()

    # ── 主入口 ──

    def discover_skills(self) -> list[Skill]:
        """从 OGE 发现所有算子并返回 GeoSkill 列表。

        优先调用 OGE API 查询算子定义，
        如果不可达则回退到 KNOWN_OPERATORS 映射表。
        """
        skills = []
        for operator_name, config in self.KNOWN_OPERATORS.items():
            skill = self._build_skill(operator_name, config)
            skills.append(skill)
        logger.info("OGE SkillAdapter: discovered %d skills", len(skills))
        return skills

    def make_skill(self, operator_name: str) -> Skill:
        """单个 OGE 算子 → GeoSkill。"""
        config = self.KNOWN_OPERATORS.get(operator_name)
        if not config:
            # 尝试从 OGE API 动态获取算子定义
            config = self._fetch_operator_config(operator_name)
        return self._build_skill(operator_name, config)

    # ── 内部 ──

    def _build_skill(self, operator_name: str,
                     config: dict[str, Any]) -> Skill:
        """构造 GeoSkill 实例。"""
        def handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
            """Skill handler — 委托到 OGE 执行。"""
            return self._execute_operator(operator_name, params, context)

        return Skill(
            name=config["skill_name"],
            description=config["description"],
            input_schema=config["input_schema"],
            output_schema=config["output_schema"],
            handler=handler,
        )

    def _execute_operator(self, operator_name: str,
                          params: dict[str, Any], context: Any) -> dict[str, Any]:
        """执行 OGE 算子 — 被 Skill handler 调用。"""
        request_id = ""
        if context:
            request_id = getattr(context, "request_id", "")

        # 将 GeoCard ID 转为 OGE 引用
        oge_params = {}
        for key, value in params.items():
            if isinstance(value, str) and self._protocol_mapper.is_oge_card(value):
                oge_params[key] = self._protocol_mapper.to_oge_reference(value)
            else:
                oge_params[key] = value

        # 执行
        executor = self._get_executor()
        result = executor.execute(operator_name, params=oge_params, request_id=request_id)

        if result.status == "failed":
            raise RuntimeError(
                f"OGE execution failed: {result.error_message} "
                f"(process_id={result.process_id})"
            )
        if result.status == "timeout":
            raise TimeoutError(
                f"OGE execution timed out: {result.operator_name} "
                f"(process_id={result.process_id})"
            )

        # 构造返回结果
        outputs = {
            "process_id": result.process_id,
        }
        # 根据 operator 确定输出键名
        if "slope" in operator_name.lower():
            outputs["slope_raster"] = f"/data/oge/{oge_params.get('outputName', 'slope.tif')}"
        elif "aspect" in operator_name.lower():
            outputs["aspect_raster"] = f"/data/oge/{oge_params.get('outputName', 'aspect.tif')}"
        elif "hillshade" in operator_name.lower():
            outputs["hillshade_raster"] = f"/data/oge/{oge_params.get('outputName', 'hillshade.tif')}"
        elif "roughness" in operator_name.lower():
            outputs["roughness_raster"] = f"/data/oge/{oge_params.get('outputName', 'roughness.tif')}"
        elif "ndvi" in operator_name.lower():
            outputs["ndvi_raster"] = f"/data/oge/{oge_params.get('outputName', 'ndvi.tif')}"
        else:
            outputs["raster"] = f"/data/oge/{oge_params.get('outputName', 'result.tif')}"

        if result.cog_url:
            outputs["cog_url"] = result.cog_url

        return outputs

    def _get_executor(self) -> OgeExecutor:
        if self._executor is None:
            self._executor = OgeExecutor(
                credential=self.credential,
                audit_callback=self.audit_callback,
            )
        return self._executor

    def _fetch_operator_config(self, operator_name: str) -> dict[str, Any]:
        """从 OGE API 获取算子定义并构造配置。"""
        try:
            client = OgeClient(endpoint=self.credential.endpoint)
            info = client.get_process_info(operator_name)
            op_name = operator_name.split(".")[-1]
            return {
                "skill_name": f"oge-{op_name.lower()}",
                "description": f"OGE operator: {operator_name}",
                "input_schema": {
                    "type": "object",
                    "properties": {inp["name"]: {"type": inp.get("type", "string")}
                                   for inp in info.inputs},
                    "required": [inp["name"] for inp in info.inputs if inp.get("required")],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {out["name"]: {"type": out.get("type", "string")}
                                   for out in info.outputs},
                },
            }
        except Exception as exc:
            logger.warning("Failed to fetch OGE operator config: %s. "
                          "Add it to KNOWN_OPERATORS.", exc)
            raise ValueError(f"Unknown OGE operator: {operator_name}") from exc


def discover_oge_skills(credential: OgeCredential | None = None,
                        audit_callback: Callable[[dict], None] | None = None) -> list[Skill]:
    """便捷函数：发现所有 OGE 算子 → GeoSkill。"""
    return OgeSkillAdapter(credential=credential,
                           audit_callback=audit_callback).discover_skills()