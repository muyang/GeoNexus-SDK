"""提交：把校验通过的卡片送到 registry 或平台（或 dry-run 只打印）。

默认 `status=pending`：登记不等于发布，审批流不变（平台侧 approvals / SDK 侧 review）。
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_REGISTRY = "http://127.0.0.1:8790"
DEFAULT_PLATFORM = "http://127.0.0.1:3302"


def submit(
    card: dict[str, Any],
    *,
    to: str = "dry",
    registry_url: str = DEFAULT_REGISTRY,
    platform_url: str = DEFAULT_PLATFORM,
    token: str | None = None,
    login: tuple[str, str] | None = None,
    status: str = "pending",
    node_url: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    if to == "dry":
        return {"submitted": False, "to": "dry", "card": card}

    if to == "registry":
        r = httpx.post(
            f"{registry_url.rstrip('/')}/cards",
            json={"card": card, "node_url": node_url, "status": status},
            timeout=timeout,
        )
        return {
            "submitted": r.status_code < 300,
            "to": "registry",
            "status_code": r.status_code,
            "response": _safe_json(r),
            "card_id": card.get("id"),
        }

    if to == "platform":
        if not token and login:
            lr = httpx.post(
                f"{platform_url.rstrip('/')}/api/auth/login",
                json={"userName": login[0], "email": login[0], "password": login[1]},
                timeout=timeout,
            )
            if lr.status_code >= 300:
                return {
                    "submitted": False,
                    "to": "platform",
                    "status_code": lr.status_code,
                    "error": _safe_json(lr).get("error", "登录失败"),
                }
            token = (_safe_json(lr) or {}).get("token")
        if not token:
            return {
                "submitted": False,
                "to": "platform",
                "error": "平台登记需要登录：--token 或 --login user:pass",
            }
        body = _platform_body(card, status)
        r = httpx.post(
            f"{platform_url.rstrip('/')}/api/geomcp/register",
            json=body,
            headers={"authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        return {
            "submitted": r.status_code < 300,
            "to": "platform",
            "status_code": r.status_code,
            "response": _safe_json(r),
            "card_id": card.get("id"),
        }

    raise ValueError(f"未知的提交目标：{to}")


def _platform_body(card: dict[str, Any], status: str) -> dict[str, Any]:
    """GeoCard → 平台 asset_cards 的字段映射（平台只认它自己的列）。"""
    spatial = card.get("spatial") or {}
    return {
        "kind": card.get("type", "data"),
        "id": card.get("id"),
        "title": card.get("name"),
        "provider": (card.get("provenance") or {}).get("provider"),
        "accessUrl": (card.get("access") or {}).get("url"),
        "license": (card.get("license") or {}).get("name") or "private",
        "tags": card.get("tags") or [],
        "description": card.get("description"),
        "sensitivity": (card.get("compliance") or {}).get("sensitivity", "public"),
        "bbox": spatial.get("bbox"),
        "region": "Global",
        "status": "available" if status == "approved" else "pending",
    }


def _safe_json(r: httpx.Response) -> dict[str, Any]:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {"data": data}
    except Exception:  # noqa: BLE001
        return {"raw": r.text[:400]}
