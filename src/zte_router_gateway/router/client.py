"""路由器门面：上层（API / CLI / 以后的限速策略层）只依赖这个类。

这里刻意不 import FastAPI，方便脱离 Web 层单测，也方便以后把限速策略
做成独立进程/任务时直接复用。
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..protocol.session import SessionManager
from ..transport import Transport
from . import devices as devices_mod
from . import qos as qos_mod
from .models import Device, DeviceListOut, QosState


class RouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.transport = Transport(settings)
        self.session = SessionManager(self.transport, settings)

    async def open(self) -> None:
        await self.transport.open()

    async def close(self) -> None:
        await self.transport.close()

    # ------------------------------------------------------------------ health

    async def health(self) -> dict[str, Any]:
        return await self.session.health()

    # ----------------------------------------------------------------- devices

    async def list_devices(self, status: str = "all") -> DeviceListOut:
        items = await devices_mod.list_devices(self.session)
        if status == "online":
            items = [device for device in items if device.online]
        elif status == "offline":
            items = [device for device in items if not device.online]
        return DeviceListOut(
            total=len(items),
            online=sum(1 for device in items if device.online),
            offline=sum(1 for device in items if not device.online),
            devices=items,
        )

    async def get_device(self, mac: str) -> Device | None:
        wanted = mac.lower()
        for device in await devices_mod.list_devices(self.session):
            if device.mac.lower() == wanted:
                return device
        return None

    async def router_info(self) -> dict[str, str | None]:
        return await devices_mod.router_info(self.session)

    # --------------------------------------------------------------------- qos

    async def get_qos(self, mac: str) -> QosState:
        return await qos_mod.get_qos(self.session, mac)

    async def apply_qos(
        self, mac: str, down_kbps: float, up_kbps: float
    ) -> dict[str, Any]:
        return await qos_mod.apply_qos(self.session, mac, down_kbps, up_kbps)

    async def delete_qos(self, mac: str) -> dict[str, Any]:
        return await qos_mod.delete_qos(self.session, mac)
