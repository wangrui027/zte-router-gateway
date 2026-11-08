"""浏览器访问网关的认证。

HTTP Basic 没有真正的登出（浏览器会一直重发缓存的凭据），所以这里用网关自己
维护的会话 cookie：

  * `/__gw/login`  网关登录页，校验 `GW_USER` / `GW_PWD`
  * `/__gw/logout` 清除会话 cookie（路由器页面上的退出按钮会跳到这里）
  * 其它网页路径由中间件校验；`/api/*` 仍然只认 `APP_TOKEN`

这套认证和路由器账号完全无关；登出网关不会影响路由器那个共享会话。
"""

from __future__ import annotations

import hmac
import html
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

COOKIE_NAME = "gw_session"

# 不需要网关登录页的路径
_PUBLIC_PATHS = {"/api", "/__gw/login", "/__gw/logout", "/__gw/logo.svg"}


def _equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def verify_credentials(
    username: str, password: str, expected_user: str, expected_password: str
) -> bool:
    return _equal(username, expected_user) and _equal(password, expected_password)


@dataclass(slots=True)
class _Session:
    user: str
    expires_at: float


class WebSessionStore:
    """内存会话表。网关重启后需要重新登录，和被认证的会话一样不落盘。"""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl = ttl_seconds
        self._sessions: dict[str, _Session] = {}

    def create(self, user: str) -> str:
        self.prune()
        token = secrets.token_urlsafe(32)
        self._sessions[token] = _Session(user, time.monotonic() + self.ttl)
        return token

    def validate(self, token: str | None) -> bool:
        if not token:
            return False
        session = self._sessions.get(token)
        if session is None:
            return False
        if session.expires_at <= time.monotonic():
            self._sessions.pop(token, None)
            return False
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def prune(self) -> None:
        now = time.monotonic()
        for token in [k for k, v in self._sessions.items() if v.expires_at <= now]:
            self._sessions.pop(token, None)


def safe_next(value: str | None) -> str:
    """只允许跳回网关内部的路径，避免开放重定向。"""
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _wants_json(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept:
        return True
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    query = request.scope.get("query_string") or b""
    return "_type=" in query.decode("latin-1")


def login_page(next_url: str, error: str = "") -> str:
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    next_value = html.escape(next_url, quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>路由器网关登录</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center;
          justify-content: center; padding: 16px; background: #f5f7fa; color: #303133;
          font-family: "Microsoft YaHei", "PingFang SC", "Helvetica Neue", Arial, sans-serif; }}
  .card {{ width: min(400px, 100%); background: #fff; border-radius: 12px; overflow: hidden;
           box-shadow: 0 12px 40px rgba(31, 45, 61, .12); }}
  .brand {{ background: #3a9cf5; padding: 26px 32px; }}
  .brand img {{ display: block; height: 68px; width: auto; }}
  .body {{ padding: 26px 32px 30px; }}
  h1 {{ margin: 0 0 6px; font-size: 20px; font-weight: 600; letter-spacing: .5px; }}
  .sub {{ margin: 0 0 22px; font-size: 13px; color: #8a94a6; }}
  label {{ display: block; font-size: 13px; color: #5a6472; margin-bottom: 16px; }}
  input[type=text], input[type=password] {{ width: 100%; height: 42px; margin-top: 7px;
      padding: 0 12px; font-size: 15px; color: #303133; background: #fff;
      border: 1px solid #dcdfe6; border-radius: 4px; outline: none;
      transition: border-color .15s, box-shadow .15s; }}
  input:focus {{ border-color: #3a9cf5; box-shadow: 0 0 0 3px rgba(58, 156, 245, .15); }}
  button {{ width: 100%; height: 44px; margin-top: 4px; border: 0; border-radius: 4px;
            background: #3a9cf5; color: #fff; font-size: 15px; cursor: pointer;
            transition: background .15s; }}
  button:hover {{ background: #2b8ae4; }}
  button:active {{ background: #2478cc; }}
  .error {{ margin-bottom: 16px; padding: 9px 12px; font-size: 13px; color: #f56c6c;
            background: #fef0f0; border: 1px solid #fde2e2; border-radius: 4px; }}
  .foot {{ margin-top: 20px; text-align: center; font-size: 12px; color: #a0a6b1; }}
</style>
</head>
<body>
<form class="card" method="post" action="/__gw/login">
  <div class="brand"><img src="/__gw/logo.svg" alt="ZTE"></div>
  <div class="body">
    <h1>智能路由器网关</h1>
    <p class="sub">请使用网关账号登录</p>
    {error_html}
    <label>账号<input name="username" type="text" autocomplete="username" autofocus required></label>
    <label>密码<input name="password" type="password" autocomplete="current-password" required></label>
    <input type="hidden" name="next" value="{next_value}">
    <button type="submit">登录</button>
    <div class="foot">ZTE Router Gateway</div>
  </div>
</form>
</body>
</html>"""


class WebAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith("/api/"):
            return await call_next(request)

        store: WebSessionStore = request.app.state.web_sessions
        if store.validate(request.cookies.get(COOKIE_NAME)):
            return await call_next(request)

        if _wants_json(request):
            return JSONResponse(
                {"error": "gateway auth required", "login": "/__gw/login"},
                status_code=401,
            )

        next_url = path
        query = request.scope.get("query_string") or b""
        if query:
            next_url += "?" + query.decode("latin-1")
        return RedirectResponse(
            f"/__gw/login?next={quote(next_url, safe='')}", status_code=302
        )
