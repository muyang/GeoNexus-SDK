"""Code Agent — 合约感知代码合成 + 沙箱执行 + 自修复循环。

Code Agent 职责（对应设计文档"Code Agent原型"）：
1. 合约感知：基于绑定合约的 schema（CRS/bbox/bands）生成适配代码模板
2. 代码执行：在子进程沙箱中执行，带超时与资源约束
3. 自修复循环：执行失败时基于错误信息重试（≤3 次迭代）
   - 优先使用 LLM 修复（需配置 GEONEXUS_LLM_API_KEY）
   - 无 LLM 时回退到确定性修复（FIX_HINTS 字符串匹配）
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


#: 预置代码模板（按操作类型）
TEMPLATES: dict[str, str] = {
    "load_raster_stats": """\
import json
import rasterio
import numpy as np

path = inputs['raster']
with rasterio.open(path) as src:
    data = src.read(1).astype(np.float32)
valid = data[~np.isnan(data)]
result = {
    "mean": float(valid.mean()) if valid.size else None,
    "std": float(valid.std()) if valid.size else None,
    "min": float(valid.min()) if valid.size else None,
    "max": float(valid.max()) if valid.size else None,
    "pixels": int(valid.size),
    "crs": src.crs.to_string(),
    "width": src.width,
    "height": src.height,
    "bands": src.count,
}
""",
}

#: 自修复的语法规则：从错误消息提取修复提示
FIX_HINTS: list[tuple[str, str]] = [
    ("NameError: name 'np' is not defined", "import numpy as np"),
    ("NameError: name 'rasterio' is not defined", "import rasterio"),
    ("division by zero", "guard against zero denominators"),
    ("invalid literal", "coerce value to float"),
]


@dataclass
class CodeAgentResult:
    """Code Agent 单次任务的执行结果。"""

    task_id: str
    code: str
    outputs: Any = None
    error: str | None = None
    attempts: int = 0
    succeeded: bool = False
    elapsed_seconds: float = 0.0
    repair_log: list[str] = field(default_factory=list)
    llm_repair_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "succeeded": self.succeeded,
            "attempts": self.attempts,
            "outputs": self.outputs,
            "error": self.error,
            "repair_log": self.repair_log,
            "llm_repair_used": self.llm_repair_used,
            "elapsed_seconds": round(self.elapsed_seconds, 4),
        }


class CodeAgent:
    """合约感知代码合成 + 沙箱执行 + 自修复（含 LLM 修复）。

    Args:
        max_retries: 自修复最大迭代次数（默认 3，对应考核指标 ≤3 次）。
        codegen: 可选代码生成函数 (template_key, inputs) -> code。
        executor: 可选执行函数 (code, inputs, timeout) -> (outputs, error)。
        llm_repair: 可选 LLM 修复器（优先修复，无配置时回退确定性修复）。
    """

    def __init__(
        self,
        max_retries: int = 3,
        codegen: Callable[[str, dict[str, Any]], str] | None = None,
        executor: Callable[[str, dict[str, Any], float], tuple[Any, str | None]] | None = None,
        timeout_seconds: float = 60.0,
        llm_repair: Any = None,
    ) -> None:
        self.max_retries = max_retries
        self.codegen = codegen or default_codegen
        self.executor = executor or sandbox_execute
        self.timeout_seconds = timeout_seconds
        self._llm_repair = llm_repair
        if llm_repair is None:
            try:
                from .llm_repair import LLMRepair
                self._llm_repair = LLMRepair()
            except Exception:
                self._llm_repair = None

    @property
    def llm_available(self) -> bool:
        return self._llm_repair is not None and self._llm_repair.available

    # ------------------------------------------------------------------ #
    def run(self, template_key: str, inputs: dict[str, Any], task_id: str | None = None) -> CodeAgentResult:
        """执行一次代码合成任务，含自修复循环。"""
        from uuid import uuid4

        tid = task_id or f"code-task-{uuid4().hex[:10]}"
        result = CodeAgentResult(task_id=tid, code="")
        start = time.time()

        code = self.codegen(template_key, inputs)
        result.code = code

        attempt = 0
        while attempt <= self.max_retries:
            attempt += 1
            result.attempts = attempt
            try:
                outputs, error = self.executor(code, inputs, self.timeout_seconds)
            except Exception as exc:
                outputs, error = None, str(exc)

            if error is None:
                result.outputs = outputs
                result.succeeded = True
                break

            result.error = error
            result.repair_log.append(f"attempt {attempt}: {error[:120]}")
            if attempt > self.max_retries:
                break

            code = self._repair(code, error, result)
            result.code = code

        result.elapsed_seconds = time.time() - start
        return result

    def _repair(self, code: str, error: str, result: CodeAgentResult | None = None) -> str:
        """LLM 优先修复，失败回退到确定性修复。"""
        # Try LLM repair first
        if self._llm_repair is not None and self._llm_repair.available:
            try:
                fixed = self._llm_repair.repair(code, error)
                if fixed and fixed != code:
                    logger.info("LLM repair used (%d -> %d chars)", len(code), len(fixed))
                    if result:
                        result.llm_repair_used = True
                        result.repair_log.append(f"LLM repair: {len(fixed)} chars")
                    return fixed
            except Exception as exc:
                logger.warning("LLM repair failed: %s, falling back", exc)

        # Fallback: deterministic repair
        fixed = code
        for hint, _fix in FIX_HINTS:
            if hint in error and hint.split(":")[0] in ("NameError", "division", "invalid"):
                if hint.startswith("NameError: name 'np'") and "import numpy" not in fixed:
                    fixed = "import numpy as np\n" + fixed
                elif hint.startswith("NameError: name 'rasterio'") and "import rasterio" not in fixed:
                    fixed = "import rasterio\n" + fixed
                break
        return fixed


# --------------------------------------------------------------------------- #
# 默认 codegen（基于模板）与 sandbox executor
# --------------------------------------------------------------------------- #

def default_codegen(template_key: str, inputs: dict[str, Any]) -> str:
    """合约感知模板选择：根据输入/操作类型挑选代码模板。"""
    if template_key in TEMPLATES:
        return TEMPLATES[template_key]
    # 兜底模板：加载栅格并输出统计
    return TEMPLATES["load_raster_stats"]


def sandbox_execute(
    code: str,
    inputs: dict[str, Any],
    timeout_seconds: float,
) -> tuple[Any, str | None]:
    """在子进程沙箱中执行代码，返回 (outputs, error)。

    NOTE: ``code`` 被逐字放置在脚本的 0 列，保持其自身缩进不变。
    """
    import json as _json

    script = (
        "import json\n"
        f"inputs = json.loads({json.dumps(inputs)!r})\n"
        f"{code}\n"
        "print(json.dumps(result, default=str))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return None, "execution timed out"

    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout).strip()
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return None, "no output"
    try:
        return _json.loads(lines[-1]), None
    except _json.JSONDecodeError as exc:
        return None, f"bad JSON output: {exc}"