"""APP 侧 REST API。"""

from __future__ import annotations

import hmac
import math
import re
from typing import Any

from fastapi import APIRouter, Depends, Request

from ..router.client import RouterClient
from ..router.models import Device, DeviceListOut, QosState
from ..util import parse_json

_BEARER_RE = re.compile(r"^Bearer\s+", re.IGNORECASE)


class ApiError(Exception):
    """返回给客户端的业务错误，payload 就是最终 JSON。"""

    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("error", "api error"))
        self.status = status
        self.payload = payload


def _constant_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def authorized(request: Request) -> bool:
    token = request.app.state.settings.token
    header = request.headers.get("authorization") or ""
    bearer = _BEARER_RE.sub("", header)
    query_token = request.query_params.get("token")
    if _constant_eq(bearer, token):
        return True
    return query_token is not None and _constant_eq(query_token, token)


async def require_token(request: Request) -> None:
    if not authorized(request):
        raise ApiError(
            401,
            {
                "error": "unauthorized",
                "hint": "Authorization: Bearer <APP_TOKEN>",
            },
        )


async def require_session(request: Request) -> None:
    """除了 health，其它接口都要求已经截获到会话（或者至少缓存了密码，能自动续约）。"""
    session = request.app.state.session
    if not session.sid and not session.password:
        raise ApiError(
            401,
            {
                "error": "no session",
                "hint": "网关尚未登录路由器：检查 ROUTER_PWD 和路由器可达性，或看 /api/health 的 lastError",
            },
        )


def _client(request: Request) -> RouterClient:
    return request.app.state.client


router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    return await _client(request).health()


@router.get("/info", dependencies=[Depends(require_session)])
async def info(request: Request) -> dict[str, str | None]:
    return await _client(request).router_info()


@router.get("/devices", dependencies=[Depends(require_session)])
async def devices(request: Request, status: str = "all") -> DeviceListOut:
    return await _client(request).list_devices(status)


@router.get("/devices/{mac}", dependencies=[Depends(require_session)])
async def device(mac: str, request: Request) -> Device:
    found = await _client(request).get_device(mac.lower())
    if found is None:
        raise ApiError(404, {"error": "device not found"})
    return found


@router.get("/devices/{mac}/qos", dependencies=[Depends(require_session)])
async def get_qos(mac: str, request: Request) -> QosState:
    return await _client(request).get_qos(mac.lower())


def _number(payload: dict[str, Any], *keys: str) -> float | None:
    value: Any = None
    for key in keys:
        if payload.get(key) is not None:
            value = payload[key]
            break
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        # 空字符串按 0 处理（表单里可能出现）
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_number(number: float) -> int | float:
    return int(number) if number.is_integer() else number


@router.put("/devices/{mac}/qos", dependencies=[Depends(require_session)])
@router.post("/devices/{mac}/qos", dependencies=[Depends(require_session)])
async def set_qos(mac: str, request: Request) -> dict[str, Any]:
    raw = await request.body()
    payload = parse_json(raw)
    if not isinstance(payload, dict):
        payload = {}
    down = _number(payload, "downKbps", "down")
    up = _number(payload, "upKbps", "up")
    if down is None or up is None or down < 0 or up < 0:
        raise ApiError(
            400,
            {
                "error": "bad request",
                "hint": '{"downKbps":10000,"upKbps":2000}，单位 Kbps',
            },
        )

    result = await _client(request).apply_qos(mac.lower(), down, up)
    return {
        "ok": result.get("errorStr") == "SUCC",
        **result,
        "applied": {
            "downKbps": _json_number(down),
            "upKbps": _json_number(up),
        },
    }


@router.delete("/devices/{mac}/qos", dependencies=[Depends(require_session)])
async def delete_qos(mac: str, request: Request) -> dict[str, Any]:
    return await _client(request).delete_qos(mac.lower())
