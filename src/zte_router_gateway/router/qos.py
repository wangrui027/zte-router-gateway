"""限速（QoS）读写。

单位是 Kbps（网页上的 Mbps × 1000），底层是 localnet_qosdyn_bandwithrule_lua。
注意固件把 Bandwidth 拼成了 Bandwith，字段名必须照抄。
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import quote

from ..protocol.paths import QOS_BY_MAC_PATH, QOS_RULE_PATH
from ..protocol.session import SessionManager
from ..protocol.xml import xml_field
from ..util import round_half_up, now_ms, parse_json
from .models import QosState

# URL 组件编码
_URI_SAFE = "!~*'()"


def _to_int(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if math.isfinite(number) else 0


def _rule_by_mac(payload: Any) -> dict[str, Any] | None:
    data = payload if isinstance(payload, dict) else {}
    # 兼容 {"data": {...}} 与裸 {OBJ_QOS_BCRULE_BYMAC: [...]} 两种形态
    if isinstance(data.get("data"), dict):
        data = data["data"]
    rules = data.get("OBJ_QOS_BCRULE_BYMAC") or []
    if not isinstance(rules, list):
        return None
    for rule in rules:
        if isinstance(rule, dict) and rule.get("ViewName"):
            return rule
    return None


async def get_qos(session: SessionManager, mac: str) -> QosState:
    response = await session.call(
        "GET",
        f"{QOS_BY_MAC_PATH}&_InstID={quote(mac, safe=_URI_SAFE)}&_={now_ms()}",
    )
    rule = _rule_by_mac(parse_json(response.text))
    if not rule:
        return {
            "mac": mac,
            "limited": False,
            "downKbps": None,
            "upKbps": None,
            "instId": None,
        }
    return {
        "mac": mac,
        "limited": True,
        "downKbps": _to_int(rule.get("BandwithRateMaxDown")),
        "upKbps": _to_int(rule.get("BandwithRateMaxUp")),
        "instId": str(rule["ViewName"]),
        "userName": rule.get("UserName") or None,
    }


async def apply_qos(
    session: SessionManager, mac: str, down_kbps: float, up_kbps: float
) -> dict[str, Any]:
    current = await get_qos(session, mac)
    inst = current.get("instId") or "-1"
    response = await session.call(
        "POST",
        QOS_RULE_PATH,
        {
            "MACDev": mac,
            "BandwithRateMaxDown": str(round_half_up(down_kbps)),
            "BandwithRateMaxUp": str(round_half_up(up_kbps)),
            "UserName": mac,
            "IF_ACTION": "Apply",
            "_InstID": inst,
        },
    )
    return {
        "errorId": xml_field(response.text, "IF_ERRORID"),
        "errorStr": xml_field(response.text, "IF_ERRORSTR"),
        "instId": inst,
    }


async def delete_qos(session: SessionManager, mac: str) -> dict[str, Any]:
    current = await get_qos(session, mac)
    inst = current.get("instId")
    if not inst:
        return {"deleted": False, "reason": "该设备当前没有限速规则"}
    response = await session.call(
        "POST",
        QOS_RULE_PATH,
        {"IF_ACTION": "Delete", "_InstID": inst},
    )
    error_str = xml_field(response.text, "IF_ERRORSTR")
    return {
        "deleted": error_str == "SUCC",
        "errorId": xml_field(response.text, "IF_ERRORID"),
        "errorStr": error_str,
    }
