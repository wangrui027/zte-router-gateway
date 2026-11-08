"""系统信息页字段：运行时间、WAN IPv4、在线时长、DNS。

数据来自两个接口：
  * home_managreg_lua   -> OBJ_DEVINFO_ID.UpTime
  * home_internetreg_lua -> OBJ_ETHWANCIP_ID / OBJ_ETHWANCPPP_ID
"""

from __future__ import annotations

from typing import Any

from ..protocol.session import SessionManager
from ..protocol.xml import parse_instances
from ..util import now_ms

DEVINFO_PATH = "/?_type=vueData&_tag=home_managreg_lua"
WANINFO_PATH = "/?_type=vueData&_tag=home_internetreg_lua"


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def parse_system_info(devinfo_xml: str, waninfo_xml: str) -> dict[str, Any]:
    device = (parse_instances(devinfo_xml, "OBJ_DEVINFO_ID") or [{}])[0]
    wan_rows = parse_instances(waninfo_xml, "OBJ_ETHWANCIP_ID") or parse_instances(
        waninfo_xml, "OBJ_ETHWANCPPP_ID"
    )
    wan = wan_rows[0] if wan_rows else {}

    if wan.get("DNSOverrideAllowedv4") == "0":
        dns, dns2 = wan.get("OverridedDNS1"), wan.get("OverridedDNS2")
    else:
        dns, dns2 = wan.get("DNS1"), wan.get("DNS2")

    return {
        "uptimeSeconds": _int(device.get("UpTime")),
        "ipv4": wan.get("IPAddress") or None,
        "onlineSeconds": _int(wan.get("UpTime")),
        "dns": dns if dns and dns != "0.0.0.0" else None,
        "dns2": dns2 if dns2 and dns2 != "0.0.0.0" else None,
    }


async def system_info(session: SessionManager) -> dict[str, Any]:
    devinfo = await session.call("GET", f"{DEVINFO_PATH}&_={now_ms()}")
    waninfo = await session.call("GET", f"{WANINFO_PATH}&_={now_ms()}")
    return parse_system_info(devinfo.text, waninfo.text)
