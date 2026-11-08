"""网速历史 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ..util import now_ms
from .api import ApiError, require_token

router = APIRouter(prefix="/api/monitor", dependencies=[Depends(require_token)])

_MAX_MACS = 12
_TARGET_POINTS = 600


@router.get("/devices")
async def monitor_devices(request: Request) -> list[dict]:
    """采样库里见过的设备（含最后速率），路由器暂时不可达时也能看。"""
    return await request.app.state.storage.known_devices()


@router.get("/series")
async def monitor_series(
    request: Request,
    macs: str = Query(..., description="逗号分隔的 MAC"),
    from_ts: int | None = Query(None, alias="from"),
    to_ts: int | None = Query(None, alias="to"),
    step: int | None = Query(None, description="聚合步长（毫秒）"),
) -> dict:
    storage = request.app.state.storage
    settings = request.app.state.settings

    wanted = [item.strip().lower() for item in macs.split(",") if item.strip()]
    wanted = list(dict.fromkeys(wanted))[:_MAX_MACS]
    if not wanted:
        raise ApiError(400, {"error": "bad request", "hint": "macs 不能为空"})

    now = now_ms()
    end = to_ts if to_ts is not None else now
    start = from_ts if from_ts is not None else end - 3_600_000
    if end <= start:
        raise ApiError(400, {"error": "bad request", "hint": "from 必须早于 to"})

    retention_ms = int(settings.monitor_retention_days * 86_400_000)
    if retention_ms > 0 and start < now - retention_ms:
        start = now - retention_ms

    interval = max(1000, int(settings.monitor_interval * 1000))
    if step is not None and step >= 1000:
        step_ms = int(step)
    else:
        span = end - start
        step_ms = max(interval, -(-span // _TARGET_POINTS))  # ceil
        step_ms = max(1000, (step_ms // 1000) * 1000)

    data = await storage.series(wanted, start, end, step_ms)
    names = await storage.device_names()

    series = []
    for mac in wanted:
        points = data.get(mac, {"down": [], "up": []})
        series.append(
            {
                "mac": mac,
                "name": names.get(mac) or mac,
                "down": points.get("down", []),
                "up": points.get("up", []),
            }
        )

    return {
        "from": start,
        "to": end,
        "stepMs": step_ms,
        "series": series,
    }
