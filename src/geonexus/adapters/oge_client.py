"""OGE OpenAPI 客户端 — JWT/tk 双凭证鉴权 + HTTP 封装。

OGE（Open Earth Engine）计算中心提供标准 REST API。本客户端封装：
- 登录鉴权（JWT + tk 双凭证）
- 文件上传
- 算子执行
- 状态查询
- 结果获取

参考接口文档：docs/OGE 计算中心 · OpenAPI 计算案例（开发者手册）.docx
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── 错误类型 ──

class OgeClientError(Exception):
    """OGE API 调用异常。"""
    def __init__(self, message: str, status_code: int | None = None,
                 response_body: str | None = None) -> None:
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class OgeAuthError(OgeClientError):
    """OGE 鉴权异常（JWT 过期、tk 无效等）。"""
    pass


class OgeExecutionError(OgeClientError):
    """OGE 执行异常。"""
    def __init__(self, message: str, process_id: str | None = None,
                 status_code: int | None = None) -> None:
        self.process_id = process_id
        super().__init__(message, status_code)


# ── 响应模型 ──

@dataclass
class OgeTokenResponse:
    """OGE 登录响应。"""
    token: str
    expires_in: int = 3600


@dataclass
class OgeAppKeyResponse:
    """OGE 应用凭证响应。"""
    app_key: str  # 格式 apk.xxx


@dataclass
class OgeUploadResponse:
    """OGE 文件上传响应。"""
    asset_uuid: str
    reference: str  # 如 Personal:MyData:myData/文件名


@dataclass
class OgeExecuteResponse:
    """OGE 算子执行响应。"""
    process_id: str  # 格式 job-xxx


@dataclass
class OgeProcessStatus:
    """OGE 任务状态。"""
    process_id: str
    result_status: int  # 0=等待, 1=运行中, 2=成功, 3=失败
    status_message: str = ""


@dataclass
class OgeProcessInfo:
    """OGE 算子定义。"""
    process_name: str  # 如 Coverage.terrSlope
    inputs: list[dict[str, Any]]
    outputs: list[dict[str, Any]]


# ── 客户端 ──

class OgeClient:
    """OGE OpenAPI HTTP 客户端。

    使用方式：
        client = OgeClient(endpoint="http://openge.org.cn/api")
        client.login(username="...", password="...", client_id="test", client_secret="123456")
        tk = client.apply_app_key("my-app")
        result = client.execute_operator(
            "Coverage.terrSlope",
            tk=tk,
            params={"coverage": "Personal:MyData:myData/dem.tif", "outputName": "slope.tif"},
        )
    """

    def __init__(self, endpoint: str = "http://openge.org.cn/api",
                 timeout: float = 30.0) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout)

    # ── 1. 鉴权 ──

    def login(self, username: str, password: str,
              client_id: str = "test", client_secret: str = "123456") -> OgeTokenResponse:
        """登录获取 JWT（password 需为 MD5 值）。"""
        import hashlib
        md5_pwd = hashlib.md5(password.encode()).hexdigest()

        resp = self._http.post(
            f"{self.endpoint}/oauth/token",
            data={
                "grant_type": "password",
                "username": username,
                "password": md5_pwd,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        self._check_response(resp)
        data = resp.json().get("data", {})
        token = data.get("token", "")
        if not token:
            raise OgeAuthError("Login failed: no token in response", resp.status_code, resp.text)
        return OgeTokenResponse(token=token, expires_in=data.get("expiresIn", 3600))

    def apply_app_key(self, app_name: str, jwt_token: str) -> OgeAppKeyResponse:
        """申请应用凭证 tk（格式 apk.xxx，长期有效）。"""
        resp = self._http.post(
            f"{self.endpoint}/open/app/key/create",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"appName": app_name},
        )
        self._check_response(resp)
        data = resp.json().get("data", {})
        app_key = data.get("appKey", "")
        if not app_key:
            raise OgeAuthError("Apply app key failed: no appKey in response",
                               resp.status_code, resp.text)
        return OgeAppKeyResponse(app_key=app_key)

    # ── 2. 数据上传 ──

    def upload_file(self, file_path: str, asset_name: str,
                    data_type: str = "grid", jwt_token: str | None = None) -> OgeUploadResponse:
        """上传文件到 OGE 平台。

        Args:
            file_path: 本地文件路径
            asset_name: 资产名称（用于引用）
            data_type: 数据类型（默认 grid）
            jwt_token: JWT token（如果未提供，使用已登录的 token）

        Returns:
            OgeUploadResponse: 包含 asset_uuid 和引用路径
        """
        headers = {}
        if jwt_token:
            headers["Authorization"] = f"Bearer {jwt_token}"

        with open(file_path, "rb") as f:
            resp = self._http.post(
                f"{self.endpoint}/asset/myData/upload",
                headers=headers,
                files={"file": (asset_name, f)},
                data={"assetName": asset_name, "dataType": data_type},
            )
        self._check_response(resp)
        data = resp.json().get("data", {})
        return OgeUploadResponse(
            asset_uuid=data.get("assetUuid", ""),
            reference=data.get("reference", f"Personal:MyData:myData/{asset_name}"),
        )

    # ── 3. 算子执行 ──

    def execute_operator(self, operator_name: str, tk: str,
                         params: dict[str, Any] | None = None) -> OgeExecuteResponse:
        """执行 OGE 算子。

        Args:
            operator_name: 算子全名，如 "Coverage.terrSlope"
            tk: 应用凭证（apk.xxx）
            params: 算子参数

        Returns:
            OgeExecuteResponse: 包含 processId
        """
        # 解析包名和算子名
        package, op_name = operator_name.split(".", 1)

        resp = self._http.post(
            f"{self.endpoint}/openapi/algorithm/{package}/{op_name}/execute",
            params={"tk": tk},
            json=params or {},
        )
        self._check_response(resp)
        data = resp.json().get("data", {})
        process_id = data.get("processId", "")
        if not process_id:
            raise OgeExecutionError("Execute operator failed: no processId in response",
                                    resp.status_code)
        return OgeExecuteResponse(process_id=process_id)

    # ── 4. 状态查询 ──

    def get_process_status(self, process_id: str, tk: str | None = None) -> OgeProcessStatus:
        """查询任务执行状态。

        resultStatus: 0=等待, 1=运行中, 2=成功, 3=失败
        """
        params: dict[str, str] = {}
        if tk:
            params["tk"] = tk

        resp = self._http.get(
            f"{self.endpoint}/computation-api/process/{process_id}",
            params=params,
        )
        self._check_response(resp)
        data = resp.json().get("data", {})
        return OgeProcessStatus(
            process_id=process_id,
            result_status=data.get("resultStatus", -1),
            status_message=data.get("statusMessage", ""),
        )

    # ── 5. 结果获取 ──

    def get_result(self, process_id: str, jwt_token: str | None = None) -> bytes:
        """下载执行结果文件。"""
        headers = {}
        if jwt_token:
            headers["Authorization"] = f"Bearer {jwt_token}"

        resp = self._http.get(
            f"{self.endpoint}/computation-api/process/result/download/{process_id}",
            headers=headers,
        )
        self._check_response(resp)
        return resp.content

    def get_result_metadata(self, process_id: str, jwt_token: str | None = None) -> dict[str, Any]:
        """获取结果元数据。"""
        headers = {}
        if jwt_token:
            headers["Authorization"] = f"Bearer {jwt_token}"

        resp = self._http.get(
            f"{self.endpoint}/computation-api/process/result/{process_id}",
            headers=headers,
        )
        self._check_response(resp)
        return resp.json()

    def get_cog_url(self, process_id: str) -> str:
        """获取 COG 预览 URL。"""
        return f"{self.endpoint}/computation-api/styles/cog/{process_id}"

    # ── 6. 算子定义查询 ──

    def get_process_info(self, process_name: str) -> OgeProcessInfo:
        """查询算子定义（输入/输出参数）。"""
        resp = self._http.get(
            f"{self.endpoint}/computation-api/process/info",
            params={"processName": process_name},
        )
        self._check_response(resp)
        data = resp.json().get("data", {})
        return OgeProcessInfo(
            process_name=process_name,
            inputs=data.get("inputs", data.get("args", [])),
            outputs=data.get("outputs", []),
        )

    # ── 内部 ──

    def _check_response(self, resp: httpx.Response) -> None:
        """检查 HTTP 响应，抛出标准异常。"""
        if resp.status_code == 401:
            raise OgeAuthError(
                f"OGE auth failed (401): {resp.text[:200]}",
                resp.status_code, resp.text,
            )
        if resp.status_code == 403:
            # 管理后台未发布的服务
            raise OgeClientError(
                f"OGE service not published (403): {resp.text[:200]}",
                resp.status_code, resp.text,
            )
        if resp.status_code >= 400:
            raise OgeClientError(
                f"OGE API error ({resp.status_code}): {resp.text[:300]}",
                resp.status_code, resp.text,
            )

        # 检查业务响应码
        try:
            body = resp.json()
            code = body.get("code", 200)
            if code != 200 and code != 0:
                raise OgeClientError(
                    f"OGE biz error (code={code}): {body.get('msg', body.get('message', 'unknown'))}",
                    resp.status_code, resp.text,
                )
        except (ValueError, KeyError):
            pass  # 非 JSON 或非标准格式，留给上层处理

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> OgeClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()