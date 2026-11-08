"""会话状态机：自动登录、探活、续约退避、单飞。

网关自己持有路由器凭据，也独占那个唯一会话：

  * 启动后自动登录；会话掉了自动重新拉起。
  * 并发请求同时发现会话失效时，只允许一个登录在飞（单飞）。
  * 每次登录尝试都会占住/延长路由器那个唯一名额，所以失败必须长退避。
  * 登录前先做只读探测（login_entry 的 GET），不收新会话就绝不尝试登录。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Mapping

from ..config import Settings
from ..errors import ErrorKind, RouterError
from ..transport import Response, Transport
from ..util import form_encode, is_session_timeout, now_ms, parse_json, round_half_up
from .login import perform_login
from .paths import LAN_INFO_PATH, LOGIN_PATH, TOKEN_PATH

logger = logging.getLogger(__name__)

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"

# 失败退避：登录尝试会占住路由器那个唯一名额，绝不能高频重试。
_BACKOFF_BASE = 5 * 60 * 1000
_BACKOFF_CAP = 30 * 60 * 1000
_BACKOFF_LOCKED = 60 * 1000
_AUTH_PROBE_TTL = 5_000

# 这些失败说明路由器当前根本不收新会话，直接进入长冷却
_HARD_FAILURES = {
    ErrorKind.AUTH,
    ErrorKind.EXHAUSTED,
    ErrorKind.REJECTED,
    ErrorKind.PREEMPT,
    ErrorKind.NOT_EFFECTIVE,
}


@dataclass(slots=True)
class LoginGate:
    """路由器当前收不收新会话（只读探测结果）。"""

    ok: bool
    reason: str = ""
    msg: str = ""


class SessionManager:
    """持有唯一会话，并把它安全地暴露给 API / 反代 / 后台任务。

    会话只放内存，绝不落盘：SID 等同于密码。密码只来自配置，不写盘、不回显。
    """

    def __init__(self, transport: Transport, settings: Settings) -> None:
        self.transport = transport
        self.settings = settings

        self.sid: str | None = None
        self.sess: str | None = None
        self.at = 0
        self.logins = 0
        self.last_use = 0
        self.last_error: str | None = None

        # 登录用的凭据：只放内存
        self.user = settings.user
        self.password: str | None = settings.pwd or None

        self.renew_attempts = 0
        self.next_retry_at = 0

        # 只有被真实数据接口验证过的会话才允许接管代理请求
        self.verified = False
        # 真正「登录成功」（而不是仅仅拿到一个页面 SID）
        self.authed = False

        self.last_auth_probe = 0
        self.sess_at = 0

        self._login_task: asyncio.Task[str] | None = None
        self._renew_lock = asyncio.Lock()

    # ---------------------------------------------------------------- login

    def login(self) -> asyncio.Task[str]:
        """单飞登录：并发调用共享同一个 task。"""
        if self._login_task is None:
            task = asyncio.create_task(self._login_guarded())
            task.add_done_callback(self._clear_login_task)
            self._login_task = task
        return self._login_task

    def _clear_login_task(self, task: asyncio.Task[str]) -> None:
        self._login_task = None
        if not task.cancelled():
            # 标记异常已被读取，避免出现 "exception was never retrieved"
            task.exception()

    async def _login_guarded(self) -> str:
        try:
            result = await perform_login(
                self.transport,
                username=self.user,
                password=self.password,
            )
        except Exception as exc:  # noqa: BLE001 - 任何失败都要记录分类
            self.last_error = getattr(exc, "kind", ErrorKind.ERROR)
            raise

        now = now_ms()
        self.sid = result.sid
        self.sess = result.sess_token
        self.at = now
        self.last_use = now
        self.logins += 1
        self.last_error = None
        self.verified = True
        self.authed = True
        logger.info("[session] 登录成功 #%s sid=%s…", self.logins, self.sid[:12])
        return self.sid

    async def renew_session(self) -> bool:
        """保证有一个可用会话：没有就登录，失败按指数退避。

        自动登录是网关的职责 —— 浏览器和 APP 都不接触路由器密码。
        """
        async with self._renew_lock:
            if self.sid and self.authed:
                return True

            if not self.password:
                raise RouterError(
                    "未配置路由器管理密码（ROUTER_PWD）", ErrorKind.NO_CREDS
                )

            now = now_ms()
            if now < self.next_retry_at:
                wait = round_half_up((self.next_retry_at - now) / 1000)
                raise RouterError(f"重新登录退避中，{wait}s 后再试", ErrorKind.THROTTLE)

            # 先探测：不收新会话就直接退避，连试都不试
            gate = await self.router_accepting_login()
            if not gate.ok:
                self.next_retry_at = now_ms() + _BACKOFF_BASE
                raise RouterError(
                    f"路由器暂不收新会话（{gate.reason}），5 分钟后再探测",
                    ErrorKind.THROTTLE,
                )

            try:
                await self.login()
            except Exception as exc:  # noqa: BLE001
                self.renew_attempts += 1
                kind = getattr(exc, "kind", ErrorKind.ERROR)
                if kind in _HARD_FAILURES:
                    delay = _BACKOFF_CAP
                elif kind == ErrorKind.LOCKED:
                    delay = _BACKOFF_LOCKED
                else:
                    delay = min(
                        _BACKOFF_CAP,
                        _BACKOFF_BASE * (2 ** (self.renew_attempts - 1)),
                    )
                self.next_retry_at = now_ms() + delay
                logger.error(
                    "[session] 重新登录失败(%s): %s —— %s 分钟后重试",
                    self.renew_attempts,
                    exc,
                    round_half_up(delay / 60000),
                )
                raise

            self.renew_attempts = 0
            self.next_retry_at = 0
            logger.info("[session] 自动登录成功 sid=%s…", (self.sid or "")[:12])
            return True

    # ---------------------------------------------------------------- probes

    async def probe_authed(self, force: bool = False) -> bool:
        """判断当前会话是不是「已登录」。

        用 localnet_lan_info_lua：未登录必返回 404 + SessionTimeout，登录了才有数据。
        force=True 时绕过缓存，强制发一次真实请求。
        """
        if self.authed and not force:
            return True
        if not self.sid:
            return False
        now = now_ms()
        if not force and now - self.last_auth_probe < _AUTH_PROBE_TTL:
            return self.authed
        self.last_auth_probe = now

        try:
            response = await self.transport.request(
                "GET", f"{LAN_INFO_PATH}&_={now_ms()}"
            )
            if is_session_timeout(response.text):
                self.authed = False
            else:
                self.authed = True
                logger.info("[session] 探测确认：当前会话已登录")
        except Exception:  # noqa: BLE001 - 探测失败不影响主流程
            pass
        return self.authed

    async def router_accepting_login(self) -> LoginGate:
        """登录前先问一句：路由器现在收不收新会话？

        这个探测是只读的（登录页自己也会调），不会占名额。
        返回 ok=False 就绝不尝试登录 —— 否则只会把会话表越挤越满。
        """
        try:
            response = await self.transport.request(
                "GET", f"{LOGIN_PATH}&_={now_ms()}"
            )
            payload = parse_json(response.text)
            if not isinstance(payload, dict):
                # 解析失败等同于探测失败，绝不贸然登录
                return LoginGate(
                    False,
                    reason=ErrorKind.PROBE_FAILED,
                    msg=response.text[:80],
                )
            error_type = payload.get("loginErrType")
            if not error_type:
                return LoginGate(True)
            return LoginGate(
                False, reason=str(error_type), msg=str(payload.get("loginErrMsg") or "")
            )
        except Exception as exc:  # noqa: BLE001
            return LoginGate(False, reason=ErrorKind.PROBE_FAILED, msg=str(exc))

    async def session_token(self) -> str:
        """写操作（POST）必须带 _sessionTOKEN，而且是一次性的，每次都现取。"""
        response = await self.transport.request("GET", f"{TOKEN_PATH}&_={now_ms()}")
        payload = parse_json(response.text)
        if isinstance(payload, dict) and payload.get("_sessionToken"):
            self.sess = str(payload["_sessionToken"])
            self.sess_at = now_ms()
        return self.sess or ""

    # ---------------------------------------------------------------- calls

    async def call(
        self, method: str, path: str, form: Mapping[str, Any] | None = None
    ) -> Response:
        """带会话的接口调用：自动补 _sessionTOKEN，超时自动重登并重试一次。"""
        if not self.sid or not self.authed:
            await self.renew_session()

        headers: dict[str, str] = {}
        body: str | None = None
        if form is not None:
            token = await self.session_token()
            payload = dict(form)
            payload["_sessionTOKEN"] = token
            body = form_encode(payload)
            headers["Content-Type"] = FORM_CONTENT_TYPE

        response = await self.transport.request(method, path, headers=headers, body=body)
        if is_session_timeout(response.text):
            # 会话失效 → 标成未认证，重新登录后重试一次
            self.authed = False
            try:
                await self.renew_session()
            except RouterError as exc:
                self.last_error = exc.kind or ErrorKind.RENEW_FAILED
                raise RouterError(
                    "会话已失效且自动重新登录失败: " + str(exc), ErrorKind.EXPIRED
                ) from exc
            response = await self.transport.request(
                method, path, headers=headers, body=body
            )
            if is_session_timeout(response.text):
                self.last_error = ErrorKind.EXPIRED
                raise RouterError(
                    "重新登录后仍然 SessionTimeout", ErrorKind.EXPIRED
                )

        self.last_use = now_ms()
        self.authed = True
        return response

    # ---------------------------------------------------------------- state

    async def health(self) -> dict[str, Any]:
        authed = await self.probe_authed()
        now = now_ms()
        return {
            # ok 只表示「已经真正登录好、可以调接口」，
            # 仅仅拿到一个页面 SID 不算。
            "ok": bool(authed and not self.last_error),
            "router": self.settings.router,
            "session": {
                "active": bool(self.sid),
                "authenticated": authed,
                "logins": self.logins,
                "ageSeconds": round_half_up((now - self.at) / 1000) if self.at else None,
                "idleSeconds": (
                    round_half_up((now - self.last_use) / 1000) if self.last_use else None
                ),
                "lastError": self.last_error,
                "passwordConfigured": bool(self.password),
                "nextRetrySeconds": (
                    max(0, round_half_up((self.next_retry_at - now) / 1000))
                    if self.next_retry_at
                    else None
                ),
            },
        }
