"""设备列表与路由器信息。"""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..protocol.paths import LAN_INFO_PATH
from ..protocol.session import SessionManager
from ..protocol.xml import parse_instances
from ..util import now_ms
from .models import Device


def _to_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_int(value: Any) -> int:
    number = _to_float(value)
    return int(number) if number is not None else 0


def normalize(row: Mapping[str, str]) -> Device:
    """把路由器返回的 ParaName/ParaValue 行映射成设备对象。"""
    ip = row.get("IPAddress")
    rssi = row.get("Rssi")
    return Device(
        mac=row.get("MACAddress") or "",
        name=row.get("DevName") or row.get("HostName") or row.get("DevNameElink") or "",
        hostname=row.get("HostName") or "",
        ip=ip if ip and ip != "0.0.0.0" else None,
        ipv6=row.get("IPV6Address") or None,
        online=row.get("Active") == "1",
        band=row.get("Band") or None,
        wired=row.get("InterfaceType") == "1",
        rssi=_to_int(rssi) if rssi and rssi != "0" else None,
        up_bps=_to_int(row.get("UploadSpeed")),
        down_bps=_to_int(row.get("DownloadSpeed")),
        online_seconds=_to_int(row.get("OnlineTime")),
        last_seen=row.get("InactiveTime") or None,
        active_time=row.get("ActiveTime") or None,
        online_times=_to_int(row.get("OnlineTimes")),
        brand=row.get("Brand") or None,
        model=row.get("Model") or None,
        guest=row.get("isGuestAcessDev") == "1",
        parent=row.get("ParentDeviceName") or None,
    )


async def list_devices(session: SessionManager) -> list[Device]:
    response = await session.call("GET", f"{LAN_INFO_PATH}&_={now_ms()}")
    devices = [
        normalize(row)
        for row in parse_instances(response.text, "OBJ_LAN_INFO_ID")
    ]
    devices = [device for device in devices if device.mac]
    # 在线设备排前面（稳定排序，保持路由器原有顺序）
    devices.sort(key=lambda device: 0 if device.online else 1)
    return devices


async def router_info(session: SessionManager) -> dict[str, str | None]:
    response = await session.call("GET", f"{LAN_INFO_PATH}&_={now_ms()}")
    instances = parse_instances(response.text, "OBJ_GLOBAL_ENV")
    env = instances[0] if instances else {}
    return {
        "title": env.get("WEBTitle") or None,
        "firmware": env.get("SoftwareVer") or None,
        "mode": env.get("Mode") or None,
    }
