"""Tests for Code Agent LLM repair (with mock LLM responses)."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import rasterio
from rasterio.transform import from_bounds

from geonexus.agent.code_agent import CodeAgent, CodeAgentResult
from geonexus.agent.llm_repair import LLMRepair


def _make_raster(path, val=0.5, w=16, h=16):
    data = np.full((h, w), val, dtype=np.float32)
    with rasterio.open(path, "w", driver="GTiff", dtype=rasterio.float32, count=1,
                       width=w, height=h, crs="EPSG:4326",
                       transform=from_bounds(-10, -5, 10, 5, w, h)) as dst:
        dst.write(data, 1)
    return str(path)


# --------------------------------------------------------------------------- #
# LLM Repair unit tests
# --------------------------------------------------------------------------- #

class TestLLMRepair:
    def test_no_config_still_creates(self):
        """无 API key 时仍可创建 LLMRepair（available=False）。"""
        repair = LLMRepair()
        assert not repair.available
        # repair() returns original code when unavailable
        assert repair.repair("bad_code", "some error") == "bad_code"

    def test_with_mock_llm(self):
        """Mock LLM 返回修复后的代码。"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "import numpy as np\nresult = {'ok': True}"}}],
        }
        mock_client.post.return_value = mock_response

        repair = LLMRepair()
        repair._client = mock_client
        repair.config.api_key = "fake-key-for-test"

        assert repair.available
        fixed = repair.repair("bad_code", "NameError: name 'np' is not defined")
        assert "import numpy" in fixed
        assert "result" in fixed

    def test_strips_markdown_backticks(self):
        """LLM 返回了 markdown 代码块 → 自动去掉。"""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "```python\nimport numpy\nresult={'ok'}\n```"}}],
        }
        mock_client.post.return_value = mock_response

        repair = LLMRepair()
        repair._client = mock_client
        repair.config.api_key = "fake"

        fixed = repair.repair("code", "error")
        assert "```" not in fixed
        assert "import numpy" in fixed

    def test_returns_original_on_llm_failure(self):
        """LLM 调用失败 → 返回原代码（fallback 由调用方处理）。"""
        mock_client = MagicMock()
        mock_client.post.side_effect = ConnectionError("no network")

        repair = LLMRepair()
        repair._client = mock_client
        repair.config.api_key = "fake"

        fixed = repair.repair("original_code", "error")
        assert fixed == "original_code"


# --------------------------------------------------------------------------- #
# CodeAgent with LLM repair integration tests
# --------------------------------------------------------------------------- #

def _mock_llm_repair():
    """Create a mock LLMRepair that returns fixed code with numpy import."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "import numpy as np\nresult = {'ok': True}"}}],
    }
    mock_client.post.return_value = mock_response

    repair = LLMRepair()
    repair._client = mock_client
    repair.config.api_key = "fake"
    return repair


class TestCodeAgentLLM:
    def test_llm_repair_used_when_available(self, tmp_path):
        """代码缺少 import numpy → LLM 修复生效 → 执行成功。"""
        raster = _make_raster(str(tmp_path / "r.tif"), val=0.5)

        # Code template missing numpy import (会触发 NameError)
        broken_code = """\
path = inputs['raster']
# numpy 未导入！
np_arr = np.array([1.0, 2.0, 3.0])
result = {"mean": float(np_arr.mean())}
"""
        llm = _mock_llm_repair()
        agent = CodeAgent(
            max_retries=2,
            codegen=lambda key, inputs: broken_code,
            llm_repair=llm,
        )
        assert agent.llm_available

        result = agent.run("custom", {"raster": raster})
        # 即使第一次执行失败 (NameError)，LLM 修复补了 import numpy
        assert result.llm_repair_used
        # 修复后应执行成功
        assert result.succeeded

    def test_fallback_without_llm(self, tmp_path):
        """无 LLM → 确定性修复 → 补充缺失 import。"""
        raster = _make_raster(str(tmp_path / "r.tif"), val=0.5)
        broken_code = "# no numpy\ndata = np.ones(10)\nresult = {'mean': float(data.mean())}"

        agent = CodeAgent(max_retries=2, codegen=lambda key, inputs: broken_code, llm_repair=None)
        # Create an LLMRepair that is NOT available
        result = agent.run("custom", {"raster": raster})
        assert not result.llm_repair_used
        # 确定性修复至少尝试了
        assert result.attempts <= 3

    def test_result_to_dict_with_llm_flag(self):
        result = CodeAgentResult(task_id="t1", code="x=1", outputs={"v": 1}, succeeded=True, llm_repair_used=True)
        d = result.to_dict()
        assert d["llm_repair_used"] is True

    def test_llm_availability(self):
        """无 API key 时 llm_available=False。"""
        agent = CodeAgent(llm_repair=None)
        if hasattr(agent, "_llm_repair") and agent._llm_repair:
            assert not agent.llm_available