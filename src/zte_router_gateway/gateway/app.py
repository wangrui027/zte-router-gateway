"""FastAPI 应用装配。

一个进程只起一个 HTTP 服务：网页反代、监控页、/api/* 同源。
网关自己登录路由器并持有唯一会话；浏览器用网关自己的账号登录（会话 cookie）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..config import Settings
from ..errors import RouterError
from ..router.client import RouterClient
from ..services.monitor import MonitorService
from ..services.qos_scheduler import QosScheduler
from ..storage import Storage
from .api import ApiError, router as api_router
from .branding import LOGO_SVG
from .keeper import keeper_loop
from .monitor_api import router as monitor_router
from .proxy import PROXY_METHODS, proxy
from .schedule_api import router as schedule_router
from .webauth import (
    COOKIE_NAME,
    WebAuthMiddleware,
    WebSessionStore,
    login_page,
    safe_next,
    verify_credentials,
)

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).with_name("static")

_STATIC_FILES = {
    "echarts.min.js": "application/javascript",
    "vue.global.prod.js": "application/javascript",
    "element-plus.full.min.js": "application/javascript",
    "element-plus.css": "text/css",
}


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.payload, status_code=exc.status)

    @app.exception_handler(RouterError)
    async def _router_error(request: Request, exc: RouterError) -> JSONResponse:
        logger.error("[api] %s", exc)
        return JSONResponse(
            {"error": "router error", "detail": str(exc)}, status_code=502
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("[api] 未处理异常")
        return JSONResponse(
            {"error": "router error", "detail": str(exc)}, status_code=502
        )


def _register_web_auth_routes(app: FastAPI, settings: Settings) -> None:
    @app.get("/__gw/login")
    async def login_form(request: Request) -> Response:
        if request.app.state.web_sessions.validate(request.cookies.get(COOKIE_NAME)):
            return RedirectResponse("/", status_code=302)
        return HTMLResponse(login_page(safe_next(request.query_params.get("next"))))

    @app.post("/__gw/login")
    async def login_submit(request: Request) -> Response:
        raw = (await request.body()).decode("utf-8", "replace")
        fields = parse_qs(raw, keep_blank_values=True)

        def field(name: str) -> str:
            return (fields.get(name) or [""])[0]

        username = field("username")
        password = field("password")
        next_url = safe_next(field("next"))

        if verify_credentials(username, password, settings.web_user, settings.web_pwd):
            token = request.app.state.web_sessions.create(username)
            response = RedirectResponse(next_url, status_code=303)
            response.set_cookie(
                COOKIE_NAME,
                token,
                max_age=int(settings.web_session_ttl_hours * 3600),
                httponly=True,
                samesite="lax",
                path="/",
            )
            return response

        return HTMLResponse(
            login_page(next_url, "账号或密码错误"), status_code=401
        )

    @app.get("/__gw/logo.svg")
    async def logo() -> Response:
        return Response(
            LOGO_SVG,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/__gw/logout")
    async def logout(request: Request) -> Response:
        request.app.state.web_sessions.revoke(request.cookies.get(COOKIE_NAME))
        response = RedirectResponse("/__gw/login", status_code=303)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = RouterClient(settings)
        await client.open()

        storage = Storage(settings.db_path)
        await storage.open()
        monitor = MonitorService(client, storage, settings)
        scheduler = QosScheduler(client, storage, settings)

        app.state.settings = settings
        app.state.client = client
        app.state.transport = client.transport
        app.state.session = client.session
        app.state.storage = storage
        app.state.scheduler = scheduler
        app.state.web_sessions = WebSessionStore(
            settings.web_session_ttl_hours * 3600
        )

        if not settings.pwd:
            logger.warning(
                "[init] 未配置 ROUTER_PWD —— 网关无法登录路由器，Web/API 都会不可用"
            )
        logger.info("[init] 目标路由器 %s（用户 %s）", settings.router, settings.user)
        logger.info(
            "[init] 采样间隔 %ss，保留 %s 天，数据库 %s",
            settings.monitor_interval,
            settings.monitor_retention_days,
            settings.db_path,
        )

        tasks = [
            asyncio.create_task(keeper_loop(client), name="keeper"),
            asyncio.create_task(monitor.run(), name="monitor"),
            asyncio.create_task(scheduler.run(), name="scheduler"),
        ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
            await storage.close()
            await client.close()

    app = FastAPI(title="ZTE Router Gateway", version="0.3.0", lifespan=lifespan)
    _register_exception_handlers(app)
    _register_web_auth_routes(app, settings)

    # 顺序很重要：/api/* 先注册，其它固定路由其次，最后才是兜底反代
    app.include_router(api_router)
    app.include_router(monitor_router)
    app.include_router(schedule_router)

    @app.get("/monitor")
    async def monitor_ui() -> Response:
        """每次请求现读文件：改页面不用重启服务。"""
        page = _STATIC_DIR / "monitor.html"
        if not page.exists():
            return Response("monitor.html 缺失", status_code=500)
        return Response(
            page.read_bytes(),
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/__gw/static/{filename}")
    async def static_asset(filename: str) -> Response:
        media_type = _STATIC_FILES.get(filename)
        if media_type is None:
            return Response("not found", status_code=404)
        asset = _STATIC_DIR / filename
        if not asset.exists():
            return Response("not found", status_code=404)
        return Response(
            asset.read_bytes(),
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/__gw/static/devices/{filename}")
    async def device_icon(filename: str) -> Response:
        if not re.fullmatch(r"[a-z0-9_-]+\.svg", filename):
            return Response("not found", status_code=404)
        asset = _STATIC_DIR / "devices" / filename
        if not asset.exists():
            return Response("not found", status_code=404)
        return Response(
            asset.read_bytes(),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    app.api_route("/{full_path:path}", methods=PROXY_METHODS)(proxy)

    # 网页管理要求先登录网关（/api/*、登录/登出、静态资源由中间件放行）
    app.add_middleware(WebAuthMiddleware)
    return app
