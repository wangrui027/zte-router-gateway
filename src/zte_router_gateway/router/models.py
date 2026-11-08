"""对外数据模型。

字段用 snake_case + alias，对外保持稳定的 camelCase JSON 契约（Android 端不用改）。
FastAPI 默认按 alias 序列化。
"""

from __future__ import annotations

from typing import NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class Device(BaseModel):
    """局域网设备。"""

    model_config = ConfigDict(populate_by_name=True)

    mac: str
    name: str = ""
    hostname: str = ""
    ip: str | None = None
    ipv6: str | None = None
    online: bool
    band: str | None = None
    wired: bool
    rssi: int | None = None
    up_bps: int = Field(0, alias="upBps")
    down_bps: int = Field(0, alias="downBps")
    online_seconds: int = Field(0, alias="onlineSeconds")
    last_seen: str | None = Field(None, alias="lastSeen")
    active_time: str | None = Field(None, alias="activeTime")
    online_times: int = Field(0, alias="onlineTimes")
    brand: str | None = None
    # 注意：字段名 model 不触发 pydantic 的 model_ 保护命名空间
    model: str | None = None
    guest: bool
    parent: str | None = None


class DeviceListOut(BaseModel):
    total: int
    online: int
    offline: int
    devices: list[Device]


class QosState(TypedDict):
    """限速状态。用 TypedDict 保证 JSON 键逐字节稳定。"""

    mac: str
    limited: bool
    downKbps: int | None
    upKbps: int | None
    instId: str | None
    userName: NotRequired[str | None]
