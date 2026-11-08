"""限速计划 API：时间窗口 + 到期自动解除。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..util import now_ms
from .api import ApiError, require_token

router = APIRouter(
    prefix="/api/qos/schedules", dependencies=[Depends(require_token)]
)

_MAX_KBPS = 10_000_000


class ScheduleCreate(BaseModel):
    mac: str = Field(min_length=1)
    downKbps: int = Field(ge=0, le=_MAX_KBPS)
    upKbps: int = Field(ge=0, le=_MAX_KBPS)
    startsAt: int | None = Field(default=None, ge=0)
    endsAt: int | None = Field(default=None, ge=0)
    durationMinutes: float | None = Field(default=None, gt=0, le=43_200)
    permanent: bool = False


def _out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "mac": row["mac"],
        "downKbps": row["down_kbps"],
        "upKbps": row["up_kbps"],
        "startsAt": row["starts_at"],
        "endsAt": row["ends_at"],
        "state": row["state"],
        "hadLimit": bool(row["had_limit"]),
        "prevDownKbps": row["prev_down_kbps"],
        "prevUpKbps": row["prev_up_kbps"],
        "attempts": row["attempts"],
        "lastError": row["last_error"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "appliedAt": row["applied_at"],
    }


@router.get("")
async def list_schedules(request: Request, state: str | None = None) -> dict:
    rows = await request.app.state.storage.list_schedules(state)
    return {"schedules": [_out(row) for row in rows]}


@router.post("")
async def create_schedule(payload: ScheduleCreate, request: Request) -> dict:
    mac = payload.mac.strip().lower()
    if not mac:
        raise ApiError(400, {"error": "bad request", "hint": "mac 不能为空"})

    now = now_ms()
    starts_at = payload.startsAt if payload.startsAt is not None else now
    if payload.permanent:
        # 永久限速：ends_at = 0，后台任务不会到期解除
        ends_at = 0
    elif payload.endsAt is not None:
        ends_at = payload.endsAt
    elif payload.durationMinutes is not None:
        ends_at = starts_at + int(payload.durationMinutes * 60_000)
    else:
        raise ApiError(
            400,
            {
                "error": "bad request",
                "hint": "需要 permanent=true、endsAt 或 durationMinutes",
            },
        )

    if ends_at and ends_at <= starts_at:
        raise ApiError(
            400,
            {"error": "bad request", "hint": "endsAt 必须晚于 startsAt"},
        )

    # 同一设备只保留一个有效限速
    await request.app.state.storage.cancel_open_schedules(mac)

    row = await request.app.state.storage.create_schedule(
        mac=mac,
        down_kbps=payload.downKbps,
        up_kbps=payload.upKbps,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    return _out(row)


@router.delete("/{sid}")
async def cancel_schedule(sid: int, request: Request) -> dict:
    row = await request.app.state.scheduler.cancel(sid)
    if row is None:
        raise ApiError(404, {"error": "schedule not found"})
    return _out(row)
