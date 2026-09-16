"""Code Agent LLM 修复器 — 调用 OpenAI 兼容 API 修复沙箱执行失败的代码。

使用方式：
- 配置环境变量 ``GEONEXUS_LLM_API_KEY`` 启用（与 llm_planner 共享）
- 未配置时回退到确定性修复（仅补缺失 import）
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .llm_planner import LLMConfig

logger = logging.getLogger(__name__)

CODE_REPAIR_PROMPT = """You are a Python code debugger for geospatial analysis.
You are given a piece of Python code that failed during execution inside a sandbox.
Your job is to return ONLY the corrected code — no explanations, no markdown.

The sandbox has these libraries available: numpy, rasterio, shapely, pyproj, json.

Rules:
1. Output ONLY the fixed Python code. No markdown, no backticks, no commentary.
2. Fix the specific error described.
3. Keep the variable named "result" that holds the final output dict.
4. If the code needs an import that's missing, add it at the top.
5. If there's a type error, add type conversion.
6. If there's a divide-by-zero, add a guard.
7. Do NOT change the overall structure unless necessary to fix the error.

Failed code:
```
{code}
```

Error message:
{error}

Fixed code (ONLY the code, no backticks):"""


class LLMRepair:
    """LLM-powered code repair — sends failed code + error to LLM, gets fix back."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        client: Any = None,
    ) -> None:
        self.config = config or LLMConfig.from_env()
        if not self.config.is_configured():
            self._client = None
            logger.warning("LLMRepair: no LLM API key configured, using fallback repair")
            return
        import httpx
        self._client = client or httpx.Client(
            base_url=self.config.base_url.rstrip("/"), timeout=30.0,
        )

    @property
    def available(self) -> bool:
        return self._client is not None

    def repair(self, code: str, error: str) -> str:
        """Send code + error to LLM, get fixed code back.

        Returns the original code if LLM unavailable or call fails.
        """
        if not self.available:
            return code  # caller falls back to deterministic repair

        prompt = CODE_REPAIR_PROMPT.format(code=code, error=error)[:8000]

        try:
            response = self._client.post(
                "/chat/completions",
                json={
                    "model": self.config.model,
                    "messages": [
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
            )
            response.raise_for_status()
            body = response.json()
            fixed = body["choices"][0]["message"]["content"].strip()
            # Strip markdown code blocks if present
            if fixed.startswith("```"):
                fixed = fixed.split("\n", 1)[1]
                if fixed.endswith("```"):
                    fixed = fixed[:-3]
                fixed = fixed.strip()
            # Validate: must contain some Python
            if "import" in fixed or "def " in fixed or "=" in fixed:
                logger.info("LLM repair: generated %d chars of fixed code", len(fixed))
                return fixed
            else:
                logger.warning("LLM repair: output not valid code (%d chars)", len(fixed))
                return code
        except Exception as exc:
            logger.warning("LLM repair call failed: %s", exc)
            return code


def llm_repair_available() -> bool:
    """Check if LLM-powered repair is configured."""
    return bool(os.environ.get("GEONEXUS_LLM_API_KEY"))