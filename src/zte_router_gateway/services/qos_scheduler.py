"""限速计划执行器。

路由器原生只有「开启/关闭限速」，没有时间控制。这里维护 qos_schedules 表：
  * pending  到点应用限速，记录应用前的原规则；
  * active   到点恢复原规则（原来没有限速就删除规则）；
  * 失败按 30s → 60s → … 最长 5 分钟退避重试；
  * 启动时和每 5 分钟做一次对账，路由器重启导致规则丢失时自动补回来。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import Settings
from ..errors import ErrorKind, RouterError
from ..router.client import RouterClient
from ..storage import Storage
from ..util import now_ms

logger = logging.getLogger(__name__)

_RETRY_BASE = 30_000
_RETRY_CAP = 5 * 60 * 1000
_RECONCILE_INTERVAL = 5 * 60 * 1000


class QosScheduler:
    def __init__(
        self, client: RouterClient, storage: Storage, settings: Settings
    ) -> None:
        self.client = client
        self.storage = storage
        self.settings = settings
        self._lock = asyncio.Lock()

    async def run(self) -> None:
        interval = max(0.2, self.settings.scheduler_interval)
        await self._reconcile()
        next_reconcile = now_ms() + _RECONCILE_INTERVAL

        while True:
            try:
                await self.tick()
                if now_ms() >= next_reconcile:
                    await self._reconcile()
                    next_reconcile = now_ms() + _RECONCILE_INTERVAL
            except Exception as exc:  # noqa: BLE001
                logger.exception("[scheduler] 执行异常: %s", exc)
            await asyncio.sleep(interval)

    async def tick(self) -> None:
        now = now_ms()
        for row in await self.storage.due_pending(now):
            await self._start(row["id"])
        for row in await self.storage.due_active(now):
            await self._expire(row["id"])

    async def cancel(self, sid: int) -> dict[str, Any] | None:
        """取消计划。已生效的会立即恢复原规则。"""
        async with self._lock:
            row = await self.storage.get_schedule(sid)
            if row is None:
                return None

            if row["state"] == "active":
                error: str | None = None
                try:
                    restored = await self._restore(row)
                except RouterError as exc:
                    restored = False
                    error = str(exc)
                if not restored and error is None:
                    error = "恢复原限速返回值异常"
                await self.storage.update_schedule(
                    sid,
                    state="cancelled",
                    attempts=0,
                    next_attempt_at=None,
                    last_error=error,
                )
                logger.info("[scheduler] 取消并解除 #%s %s", sid, row["mac"])
            elif row["state"] == "pending":
                await self.storage.update_schedule(
                    sid,
                    state="cancelled",
                    attempts=0,
                    next_attempt_at=None,
                    last_error=None,
                )
                logger.info("[scheduler] 取消待生效计划 #%s %s", sid, row["mac"])

            return await self.storage.get_schedule(sid)

    # ---------------------------------------------------------------- interns

    async def _start(self, sid: int) -> None:
        async with self._lock:
            row = await self.storage.get_schedule(sid)
            if row is None or row["state"] != "pending":
                return

            try:
                current = await self.client.get_qos(row["mac"])
            except RouterError as exc:
                await self._backoff(row, exc, "读取当前限速失败")
                return

            await self.storage.update_schedule(
                sid,
                had_limit=1 if current.get("limited") else 0,
                prev_down_kbps=current.get("downKbps"),
                prev_up_kbps=current.get("upKbps"),
                prev_inst_id=current.get("instId"),
            )

            try:
                result = await self.client.apply_qos(
                    row["mac"], row["down_kbps"], row["up_kbps"]
                )
            except RouterError as exc:
                await self._backoff(row, exc, "应用限速失败")
                return

            if result.get("errorStr") == "SUCC":
                await self.storage.update_schedule(
                    sid,
                    state="active",
                    applied_at=now_ms(),
                    attempts=0,
                    next_attempt_at=None,
                    last_error=None,
                )
                logger.info(
                    "[scheduler] 生效 #%s %s %s/%s Kbps",
                    sid,
                    row["mac"],
                    row["down_kbps"],
                    row["up_kbps"],
                )
            else:
                await self._backoff(
                    row,
                    RouterError(f"应用限速失败: {result}", ErrorKind.ERROR),
                    "应用限速失败",
                )

    async def _expire(self, sid: int) -> None:
        async with self._lock:
            row = await self.storage.get_schedule(sid)
            if row is None or row["state"] != "active":
                return
            if not row["ends_at"]:
                return  # 永久限速

            try:
                restored = await self._restore(row)
            except RouterError as exc:
                await self._backoff(row, exc, "解除限速失败")
                return

            if not restored:
                await self._backoff(
                    row,
                    RouterError("解除限速返回值异常", ErrorKind.ERROR),
                    "解除限速失败",
                )
                return

            await self.storage.update_schedule(
                sid,
                state="expired",
                attempts=0,
                next_attempt_at=None,
                last_error=None,
            )
            logger.info("[scheduler] 到期解除 #%s %s", sid, row["mac"])

    async def _restore(self, row: dict[str, Any]) -> bool:
        if row["had_limit"]:
            result = await self.client.apply_qos(
                row["mac"],
                row["prev_down_kbps"] or 0,
                row["prev_up_kbps"] or 0,
            )
            return result.get("errorStr") == "SUCC"

        result = await self.client.delete_qos(row["mac"])
        if result.get("deleted") is True:
            return True
        return "没有限速规则" in str(result.get("reason") or "")

    async def _backoff(
        self, row: dict[str, Any], exc: Exception, prefix: str
    ) -> None:
        attempts = int(row["attempts"]) + 1
        delay = min(_RETRY_CAP, _RETRY_BASE * (2 ** (attempts - 1)))
        await self.storage.update_schedule(
            row["id"],
            attempts=attempts,
            next_attempt_at=now_ms() + delay,
            last_error=f"{prefix}: {exc}",
        )
        logger.warning(
            "[scheduler] #%s %s，%s 秒后重试",
            row["id"],
            exc,
            delay // 1000,
        )

    async def _reconcile(self) -> None:
        """路由器重启后规则会丢，给仍在生效的计划补回来。"""
        async with self._lock:
            for row in await self.storage.active_schedules():
                try:
                    current = await self.client.get_qos(row["mac"])
                    if current.get("limited"):
                        continue
                    result = await self.client.apply_qos(
                        row["mac"], row["down_kbps"], row["up_kbps"]
                    )
                    if result.get("errorStr") == "SUCC":
                        logger.info(
                            "[scheduler] 对账补回 #%s %s", row["id"], row["mac"]
                        )
                    else:
                        logger.warning(
                            "[scheduler] 对账补回失败 #%s: %s", row["id"], result
                        )
                except RouterError as exc:
                    logger.warning(
                        "[scheduler] 对账失败 #%s: %s", row["id"], exc
                    )
