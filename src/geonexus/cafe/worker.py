"""CAFE WorkerNode — sovereign local execution of pushdown tasks.

一个 Worker 管理本地数据分片（本地路径/URI），接收 Central Server 下推的
Python 代码并在受控子进程沙箱中执行，最后只返回聚合结果（原始数据不出域）。
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .protocol import PushdownRequest, PushdownResult, TaskStatus

logger = logging.getLogger(__name__)


def worker_function(sandbox_script: str, timeout_seconds: float) -> tuple[Any, str | None]:
    """Execute a sandbox script in a subprocess, returning (result, error).

    使用当前解释器的子进程，通过 JSON 序列化交换数据。
    """
    proc = subprocess.run(
        [sys.executable, "-c", sandbox_script],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if proc.returncode != 0:
        return None, proc.stderr.strip() or proc.stdout.strip() or "worker failed"
    # Script prints JSON on the last line.
    import json

    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return None, "no output produced"
    try:
        return json.loads(lines[-1]), None
    except json.JSONDecodeError as exc:
        return None, f"bad result JSON: {exc}"


def _build_sandbox_script(request: PushdownRequest) -> str:
    """Generate the subprocess script: inject inputs, run user code, emit JSON.

    NOTE: user code is placed at column 0. The f-string wrapper lines are
    joined explicitly so the embedded code keeps its own indentation.
    """
    import json

    inputs_json = json.dumps(request.inputs)
    code = request.code
    returns = request.returns
    return (
        "import json\n"
        f"inputs = json.loads({inputs_json!r})\n"
        f"{code}\n"
        "try:\n"
        f"    _result = {returns}\n"
        "except NameError:\n"
        "    _result = None\n"
        'print(json.dumps(_result, default=str))\n'
    )


class WorkerNode:
    """A sovereign worker that owns local data and executes pushdown tasks.

    Attributes:
        worker_id: Unique worker identifier (e.g. "worker-mekong-data").
        data_assets: Mapping of registered local asset name -> local path.
        tags: Advertised capabilities (e.g. {"gpu": False, "region": "mekong"}).
    """

    def __init__(
        self,
        worker_id: str,
        data_assets: dict[str, str] | None = None,
        tags: dict[str, Any] | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.data_assets: dict[str, str] = dict(data_assets or {})
        self.tags: dict[str, Any] = dict(tags or {})
        self._jobs: dict[str, PushdownResult] = {}

    # ------------------------------------------------------------------ #
    # Data management
    # ------------------------------------------------------------------ #
    def register_asset(self, name: str, local_path: str) -> WorkerNode:
        """Register a local data asset this worker owns."""
        self.data_assets[name] = local_path
        return self

    def resolve_input(self, name: str) -> str | None:
        """Resolve an input reference to a local path, or None if not local."""
        return self.data_assets.get(name)

    # ------------------------------------------------------------------ #
    # Task execution
    # ------------------------------------------------------------------ #
    def execute(self, request: PushdownRequest) -> PushdownResult:
        """Execute a pushdown task locally.

        Inputs are resolved against this worker's local assets: any input key
        that matches a registered asset name points to the local file.
        """
        start = time.time()
        resolved_inputs: dict[str, Any] = {}
        missing: list[str] = []
        for key, value in request.inputs.items():
            local = self.resolve_input(value)
            if local is not None:
                resolved_inputs[key] = local
            elif isinstance(value, str) and Path(value).exists():
                resolved_inputs[key] = value
            else:
                missing.append(f"{key}={value}")

        if missing:
            self._jobs[request.task_id] = PushdownResult(
                task_id=request.task_id,
                status=TaskStatus.FAILED,
                error=f"worker {self.worker_id} cannot resolve inputs: {missing}",
                worker_id=self.worker_id,
                elapsed_seconds=time.time() - start,
            )
            return self._jobs[request.task_id]

        # Rebuild request with resolved inputs, then execute.
        resolved_request = PushdownRequest(
            code=request.code,
            inputs=resolved_inputs,
            returns=request.returns,
            task_id=request.task_id,
            timeout_seconds=request.timeout_seconds,
            metadata=request.metadata,
        )
        script = _build_sandbox_script(resolved_request)

        try:
            result, error = worker_function(script, resolved_request.timeout_seconds)
            status = TaskStatus.SUCCEEDED if error is None else TaskStatus.FAILED
        except subprocess.TimeoutExpired:
            result, error, status = None, "worker execution timed out", TaskStatus.TIMEOUT

        outcome = PushdownResult(
            task_id=request.task_id,
            status=status,
            result=result,
            error=error,
            worker_id=self.worker_id,
            elapsed_seconds=time.time() - start,
        )
        self._jobs[request.task_id] = outcome
        return outcome

    def get_job(self, task_id: str) -> PushdownResult | None:
        return self._jobs.get(task_id)