"""运行配置。"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_ROUTER = "https://router.java-jar.fun:2280"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DEFAULT_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"


def _debug_level(raw: str | None) -> int:
    """DEBUG 为真即打日志，DEBUG=2 再打印完整请求头。"""
    if not raw:
        return 0
    return int(raw) if raw.isdigit() else 1


@dataclass(slots=True)
class Settings:
    """网关配置。所有字段都可以通过环境变量提供。"""

    router: str = DEFAULT_ROUTER
    user: str = "admin"
    pwd: str = ""
    host: str = "0.0.0.0"
    port: int = 8081
    token: str = ""

    # 浏览器访问网关用的账号密码（与路由器账号完全无关）
    web_user: str = "admin"
    web_pwd: str = ""
    web_pwd_generated: bool = field(default=False, init=False)
    # 浏览器打开根路径时自动落到这个 hash 路由（固件默认在 / 显示登录视图）
    web_entry: str = "/#/menu/dashboard"
    # 网关网页会话有效期（小时）
    web_session_ttl_hours: float = 12.0

    debug: int = 0
    heartbeat_ms: int = 60_000
    router_timeout: float = 20.0
    proxy_timeout: float = 30.0

    def __post_init__(self) -> None:
        self.router = self.router.rstrip("/")
        self.user = self.user or "admin"
        if not self.token:
            self.token = secrets.token_hex(12)
        if not self.web_pwd:
            self.web_pwd = secrets.token_urlsafe(12)
            self.web_pwd_generated = True
        self.heartbeat_ms = max(30_000, int(self.heartbeat_ms))

    # ---- 路由器地址拆解 ----

    @property
    def netloc(self) -> str:
        """Host 请求头的值（可能带端口），如 router.java-jar.fun:2280。"""
        return urlsplit(self.router).netloc

    @property
    def origin(self) -> str:
        return self.router

    @property
    def referer(self) -> str:
        return self.router + "/"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            router=os.environ.get("ROUTER_URL") or DEFAULT_ROUTER,
            user=os.environ.get("ROUTER_USER") or "admin",
            pwd=os.environ.get("ROUTER_PWD") or "",
            host=os.environ.get("HOST") or "0.0.0.0",
            port=int(os.environ.get("PORT") or 8081),
            token=os.environ.get("APP_TOKEN") or "",
            web_user=os.environ.get("GW_USER") or "admin",
            web_pwd=os.environ.get("GW_PWD") or "",
            web_entry=os.environ.get("GW_WEB_ENTRY", "/#/menu/dashboard"),
            web_session_ttl_hours=float(
                os.environ.get("GW_SESSION_TTL_HOURS") or 12
            ),
            debug=_debug_level(os.environ.get("DEBUG")),
            heartbeat_ms=int(os.environ.get("HEARTBEAT_MS") or 60_000),
            router_timeout=float(os.environ.get("ROUTER_TIMEOUT") or 20),
            proxy_timeout=float(os.environ.get("PROXY_TIMEOUT") or 30),
        )
