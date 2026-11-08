"""中兴 SR6110 单会话复用网关。

路由器只允许 1 个 Web 会话，且新登录会被拒绝（不是抢占）。
所以网关自己绝不登录 —— 你通过网关的网页亲手登录一次，网关截留那次
登录的 cookie，网页和 APP 共用。
"""

from __future__ import annotations

from .cli import main
from .config import Settings
from .router.client import RouterClient

__all__ = ["main", "Settings", "RouterClient"]
