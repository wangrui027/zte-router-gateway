"""后台采样服务：定时拉设备列表，把网速写进 SQLite。"""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings
from ..errors import RouterError
from ..router.client import RouterClient
from ..storage import Storage
from ..util import now_ms

logger = logging.getLogger(__name__)

_PRUNE_INTERVAL = 60 * 60 * 1000


class MonitorService:
    def __init__(
        self, client: RouterClient, storage: Storage, settings: Settings
    ) -> None:
        self.client = client
        self.storage = storage
        self.settings = settings
        self._last_error: str | None = None
        # mac -> (ts_ms, BytesReceived, BytesSend)
        self._counters: dict[str, tuple[int, int, int]] = {}

    def _samples(self, devices, ts: int) -> list[tuple[str, int, int, int]]:
        """用累计字节差分算速率（B/s）。

        固件的 DownloadSpeed / UploadSpeed 单位不可靠，不能直接用。
        """
        samples: list[tuple[str, int, int, int]] = []
        seen: set[str] = set()
        for device in devices:
            if not device.mac:
                continue
            key = device.mac.lower()
            seen.add(key)
            if not device.online:
                self._counters.pop(key, None)
                continue

            received = device.bytes_received
            sent = device.bytes_sent
            previous = self._counters.get(key)
            self._counters[key] = (ts, received, sent)
            if previous is None:
                continue

            previous_ts, previous_received, previous_sent = previous
            elapsed = (ts - previous_ts) / 1000.0
            if elapsed <= 0 or elapsed > 60:
                continue
            delta_received = received - previous_received
            delta_sent = sent - previous_sent
            if delta_received < 0 or delta_sent < 0:
                continue  # 计数器重置
            samples.append(
                (
                    key,
                    ts,
                    round(delta_received / elapsed),
                    round(delta_sent / elapsed),
                )
            )

        for key in list(self._counters):
            if key not in seen:
                self._counters.pop(key, None)
        return samples

    async def run(self) -> None:
        interval = max(0.2, self.settings.monitor_interval)
        retention_ms = int(self.settings.monitor_retention_days * 86400_000)
        last_prune = 0

        while True:
            try:
                result = await self.client.list_devices()
                ts = now_ms()
                samples = self._samples(result.devices, ts)
                await self.storage.record_snapshot(result.devices, samples, ts)
                if self._last_error is not None:
                    logger.info("[monitor] 采样恢复")
                    self._last_error = None
            except RouterError as exc:
                message = str(exc)
                if message != self._last_error:
                    logger.error("[monitor] 采样失败: %s", message)
                    self._last_error = message
            except Exception as exc:  # noqa: BLE001
                logger.exception("[monitor] 采样异常: %s", exc)
                self._last_error = str(exc)

            now = now_ms()
            if retention_ms > 0 and now - last_prune >= _PRUNE_INTERVAL:
                last_prune = now
                try:
                    removed = await self.storage.prune_samples(now - retention_ms)
                    if removed:
                        logger.info("[monitor] 清理 %s 条过期采样", removed)
                except Exception as exc:  # noqa: BLE001
                    logger.error("[monitor] 清理过期采样失败: %s", exc)

            await asyncio.sleep(interval)
