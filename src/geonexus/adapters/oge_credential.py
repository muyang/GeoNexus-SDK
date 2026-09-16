"""OGE 凭证管理器 — JWT/tk 生命周期管理。

负责：
- 登录获取 JWT（含 MD5 密码）
- 申请/缓存 tk（应用凭证）
- 到期前自动续期

设计为无状态组件，凭证由外部（Java mgbackend）管理，
Python 执行面不持久化凭证，仅用于当前会话。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .oge_client import OgeAppKeyResponse, OgeClient, OgeTokenResponse

logger = logging.getLogger(__name__)


@dataclass
class OgeCredential:
    """OGE 凭证快照 — 由外部（Java mgbackend）配置和管理。"""
    endpoint: str = "http://openge.org.cn/api"
    username: str = ""
    password: str = ""
    client_id: str = "test"
    client_secret: str = "123456"
    app_name: str = "geonexus-execution-plane"


@dataclass
class OgeCredentialCache:
    """OGE 凭证缓存 — 运行时状态，不持久化。"""
    jwt_token: str = ""
    jwt_expire_at: float = 0
    app_key: str = ""  # tk (apk.xxx)
    app_key_expire_at: float = 0
    last_refresh_error: str = ""


class OgeCredentialManager:
    """OGE 凭证管理器 — 自动登录、续期、缓存。

    使用方式：
        mgr = OgeCredentialManager(cred)
        tk = mgr.ensure_app_key()  # 自动登录 + 申请 tk（若已过期）
    """

    def __init__(self, credential: OgeCredential | None = None) -> None:
        self.cred = credential or OgeCredential()
        self._cache = OgeCredentialCache()

    # ── 公共方法 ──

    def ensure_jwt(self) -> str:
        """确保 JWT 有效，过期则自动续期。"""
        if self._cache.jwt_token and time.time() < self._cache.jwt_expire_at - 60:
            return self._cache.jwt_token
        return self._login()

    def ensure_app_key(self) -> str:
        """确保 tk 有效，过期则自动申请。"""
        if self._cache.app_key and time.time() < self._cache.app_key_expire_at - 3600:
            return self._cache.app_key
        # 先确保 JWT
        jwt = self.ensure_jwt()
        return self._apply_key(jwt)

    def refresh(self) -> None:
        """强制刷新所有凭证。"""
        self._cache = OgeCredentialCache()
        self.ensure_app_key()

    @property
    def is_configured(self) -> bool:
        """是否已配置 OGE 凭证。"""
        return bool(self.cred.endpoint and self.cred.username and self.cred.password)

    # ── 内部 ──

    def _new_client(self) -> OgeClient:
        return OgeClient(endpoint=self.cred.endpoint)

    def _login(self) -> str:
        try:
            client = self._new_client()
            resp: OgeTokenResponse = client.login(
                username=self.cred.username,
                password=self.cred.password,
                client_id=self.cred.client_id,
                client_secret=self.cred.client_secret,
            )
            self._cache.jwt_token = resp.token
            self._cache.jwt_expire_at = time.time() + resp.expires_in
            self._cache.last_refresh_error = ""
            logger.info("OGE JWT refreshed (expires in %ds)", resp.expires_in)
            return resp.token
        except Exception as exc:
            self._cache.last_refresh_error = str(exc)
            if self._cache.jwt_token:
                logger.warning("OGE JWT refresh failed, using cached: %s", exc)
                return self._cache.jwt_token
            raise

    def _apply_key(self, jwt: str) -> str:
        try:
            client = self._new_client()
            resp: OgeAppKeyResponse = client.apply_app_key(
                app_name=self.cred.app_name,
                jwt_token=jwt,
            )
            self._cache.app_key = resp.app_key
            # tk 长期有效，设置 7 天过期以防止无限使用
            self._cache.app_key_expire_at = time.time() + 7 * 24 * 3600
            self._cache.last_refresh_error = ""
            logger.info("OGE AppKey refreshed: %s...", resp.app_key[:12])
            return resp.app_key
        except Exception as exc:
            self._cache.last_refresh_error = str(exc)
            if self._cache.app_key:
                logger.warning("OGE AppKey refresh failed, using cached: %s", exc)
                return self._cache.app_key
            raise

    def get_status(self) -> dict:
        """获取凭证状态（用于 health check）。"""
        return {
            "configured": self.is_configured,
            "jwt_valid": bool(self._cache.jwt_token) and time.time() < self._cache.jwt_expire_at,
            "app_key_valid": bool(self._cache.app_key),
            "last_error": self._cache.last_refresh_error or None,
        }