"""FastAPI 应用装配。

一个进程只起一个 HTTP 服务：网页反代和 /api/* 同源。
网关自己登录路由器并持有唯一会话；浏览器用网关自己的账号登录（会话 cookie）。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..config import Settings
from ..errors import RouterError
from ..router.client import RouterClient
from .api import ApiError, router as api_router
from .branding import LOGO_SVG
from .keeper import keeper_loop
from .proxy import PROXY_METHODS, proxy
from .webauth import (
    COOKIE_NAME,
    WebAuthMiddleware,
    WebSessionStore,
    login_page,
    safe_next,
    verify_credentials,
)

logger = logging.getLogger(__name__)


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
        app.state.settings = settings
        app.state.client = client
        app.state.transport = client.transport
        app.state.session = client.session
        app.state.web_sessions = WebSessionStore(
            settings.web_session_ttl_hours * 3600
        )

        if not settings.pwd:
            logger.warning(
                "[init] 未配置 ROUTER_PWD —— 网关无法登录路由器，Web/API 都会不可用"
            )
        logger.info("[init] 目标路由器 %s（用户 %s）", settings.router, settings.user)

        keeper = asyncio.create_task(keeper_loop(client))
        try:
            yield
        finally:
            keeper.cancel()
            with suppress(asyncio.CancelledError):
                await keeper
            await client.close()

    app = FastAPI(title="ZTE Router Gateway", version="0.2.0", lifespan=lifespan)
    _register_exception_handlers(app)
    _register_web_auth_routes(app, settings)

    # 顺序很重要：/api/* 先注册，登录/登出其次，最后才是兜底反代
    app.include_router(api_router)
    app.api_route("/{full_path:path}", methods=PROXY_METHODS)(proxy)

    # 网页管理要求先登录网关（/api/* 与登录/登出由中间件放行）
    app.add_middleware(WebAuthMiddleware)
    return app
