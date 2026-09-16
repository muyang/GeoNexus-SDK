"""OGE 算子执行器 — 执行 OGE 算子 + 轮询状态 + 获取结果。

封装 OGE OpenAPI 的完整执行链路：
  1. 登录 → JWT
  2. 申请 tk
  3. 执行算子 → processId
  4. 轮询状态至终态
  5. 获取结果
  6. 写审计日志
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .oge_client import (
    OgeAuthError,
    OgeClient,
    OgeClientError,
    OgeExecuteResponse,
    OgeExecutionError,
    OgeProcessStatus,
)
from .oge_credential import OgeCredential, OgeCredentialManager

logger = logging.getLogger(__name__)


@dataclass
class OgeExecutionResult:
    """OGE 完整执行结果。"""
    process_id: str
    operator_name: str
    status: str  # "succeeded" | "failed" | "timeout"
    result_data: bytes | None = None
    result_metadata: dict[str, Any] = field(default_factory=dict)
    cog_url: str = ""
    error_message: str = ""
    elapsed_seconds: float = 0.0
    poll_count: int = 0


class OgeTaskPoller:
    """OGE 任务状态轮询器 — 支持同步轮询和异步回调。

    使用方式（同步）：
        poller = OgeTaskPoller(client)
        result = poller.poll_until_done(process_id, tk, timeout=300)

    使用方式（异步回调）：
        def on_done(result: OgeExecutionResult):
            print(f"Task {result.process_id} done: {result.status}")

        poller.start_polling(process_id, tk, on_done=on_done)
    """

    def __init__(self, client: OgeClient, poll_interval: float = 5.0) -> None:
        self.client = client
        self.poll_interval = poll_interval

    def poll_until_done(self, process_id: str, tk: str | None = None,
                        timeout: float = 300.0) -> OgeProcessStatus:
        """轮询直到任务结束或超时。"""
        start = time.time()
        poll_count = 0

        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                raise TimeoutError(
                    f"OGE task {process_id} polling timed out after {timeout}s "
                    f"(polled {poll_count} times)"
                )

            try:
                status = self.client.get_process_status(process_id, tk=tk)
                poll_count += 1
            except OgeClientError as exc:
                logger.warning("OGE poll error (attempt %d): %s", poll_count, exc)
                time.sleep(self.poll_interval)
                continue

            # resultStatus: 0=等待, 1=运行中, 2=成功, 3=失败
            if status.result_status == 2:
                logger.info("OGE task %s succeeded (polled %d times, %.1fs)",
                            process_id, poll_count, elapsed)
                return status
            if status.result_status == 3:
                raise OgeExecutionError(
                    f"OGE task {process_id} failed: {status.status_message}",
                    process_id=process_id,
                )

            time.sleep(self.poll_interval)


class OgeExecutor:
    """OGE 执行器 — 完整执行链路。

    使用方式：
        executor = OgeExecutor(credential=OgeCredential(...))
        result = executor.execute(
            operator="Coverage.terrSlope",
            params={"coverage": "Personal:MyData:myData/dem.tif", "outputName": "slope.tif"},
        )
    """

    def __init__(self, credential: OgeCredential | None = None,
                 poll_interval: float = 5.0, poll_timeout: float = 300.0,
                 audit_callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.credential = credential or OgeCredential()
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.audit_callback = audit_callback

        # 内部组件（延迟初始化）
        self._cred_mgr: OgeCredentialManager | None = None
        self._client: OgeClient | None = None
        self._poller: OgeTaskPoller | None = None

    def execute(self, operator: str, params: dict[str, Any] | None = None,
                request_id: str = "") -> OgeExecutionResult:
        """执行 OGE 算子（同步，含身份验证 + 状态轮询）。"""
        start = time.time()
        operator_name = operator
        params = params or {}

        # 1. 确保凭证
        tk = self._get_cred_mgr().ensure_app_key()

        # 2. 执行算子
        logger.info("OGE execute: %s params=%s request_id=%s", operator_name, params, request_id)
        try:
            resp: OgeExecuteResponse = self._get_client().execute_operator(
                operator_name, tk=tk, params=params,
            )
        except OgeAuthError:
            # 凭证可能过期，刷新后重试一次
            logger.info("OGE auth error, refreshing credentials and retrying...")
            self._get_cred_mgr().refresh()
            tk = self._get_cred_mgr().ensure_app_key()
            resp = self._get_client().execute_operator(operator_name, tk=tk, params=params)

        process_id = resp.process_id

        # 3. 轮询状态
        try:
            self._get_poller().poll_until_done(
                process_id, tk=tk, timeout=self.poll_timeout,
            )
        except TimeoutError as exc:
            result = OgeExecutionResult(
                process_id=process_id,
                operator_name=operator_name,
                status="timeout",
                error_message=str(exc),
                elapsed_seconds=time.time() - start,
            )
            self._audit(result, request_id)
            return result
        except OgeExecutionError as exc:
            result = OgeExecutionResult(
                process_id=process_id,
                operator_name=operator_name,
                status="failed",
                error_message=str(exc.message),
                elapsed_seconds=time.time() - start,
            )
            self._audit(result, request_id)
            return result

        # 4. 获取结果
        try:
            jwt = self._get_cred_mgr().ensure_jwt()
            result_data = self._get_client().get_result(process_id, jwt_token=jwt)
            result_meta = self._get_client().get_result_metadata(process_id, jwt_token=jwt)
            cog_url = self._get_client().get_cog_url(process_id)
        except OgeClientError as exc:
            logger.warning("OGE result fetch failed: %s", exc)
            result_data = None
            result_meta = {}
            cog_url = ""

        result = OgeExecutionResult(
            process_id=process_id,
            operator_name=operator_name,
            status="succeeded",
            result_data=result_data,
            result_metadata=result_meta,
            cog_url=cog_url,
            elapsed_seconds=time.time() - start,
        )
        self._audit(result, request_id)
        return result

    # ── 内部 ──

    def _get_cred_mgr(self) -> OgeCredentialManager:
        if self._cred_mgr is None:
            self._cred_mgr = OgeCredentialManager(self.credential)
        return self._cred_mgr

    def _get_client(self) -> OgeClient:
        if self._client is None:
            self._client = OgeClient(endpoint=self.credential.endpoint)
        return self._client

    def _get_poller(self) -> OgeTaskPoller:
        if self._poller is None:
            self._poller = OgeTaskPoller(self._get_client(), self.poll_interval)
        return self._poller

    def _audit(self, result: OgeExecutionResult, request_id: str) -> None:
        """回写审计日志。"""
        if self.audit_callback:
            self.audit_callback({
                "request_id": request_id,
                "operator": result.operator_name,
                "process_id": result.process_id,
                "status": result.status,
                "elapsed_seconds": result.elapsed_seconds,
                "error": result.error_message or None,
            })

    def close(self) -> None:
        if self._client:
            self._client.close()

    def __enter__(self) -> OgeExecutor:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()