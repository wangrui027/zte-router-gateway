"""端到端测试：假路由器 + 真实传输层/会话/API/反代。"""

from __future__ import annotations

import httpx
import pytest
from aiohttp import web

from zte_router_gateway.config import Settings
from zte_router_gateway.gateway.app import create_app
from zte_router_gateway.router.client import RouterClient

from .fake_router import AUTH_SID, PASSWORD, create_fake_app


@pytest.fixture
async def fake_router():
    app, state = create_fake_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        yield f"http://127.0.0.1:{port}", state
    finally:
        await runner.cleanup()


def _app_client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    )


async def test_router_client_end_to_end(fake_router) -> None:
    base, state = fake_router
    settings = Settings(router=base, pwd=PASSWORD, token="t")
    client = RouterClient(settings)
    await client.open()
    try:
        devices = await client.list_devices()
        assert devices.total == 2
        assert devices.online == 1
        assert devices.devices[0].mac == "AA:BB:CC:DD:EE:FF"
        assert devices.devices[0].online is True
        assert devices.devices[0].down_bps == 393
        assert devices.devices[1].ip is None
        assert devices.devices[1].wired is True

        mac = "aa:bb:cc:dd:ee:ff"
        assert await client.get_qos(mac) == {
            "mac": mac,
            "limited": False,
            "downKbps": None,
            "upKbps": None,
            "instId": None,
        }

        applied = await client.apply_qos(mac, 10000, 2000)
        assert applied["errorStr"] == "SUCC"
        assert applied["instId"] == "-1"

        current = await client.get_qos(mac)
        assert current["limited"] is True
        assert current["downKbps"] == 10000
        assert current["upKbps"] == 2000

        deleted = await client.delete_qos(mac)
        assert deleted["deleted"] is True
        assert (await client.get_qos(mac))["limited"] is False

        info = await client.router_info()
        assert info == {"title": "SR6110 & Co", "firmware": "V1.0.0", "mode": "Gateway"}

        # 整个流程只登录一次
        assert state.login_calls == 1
        assert client.session.logins == 1

        # 模拟路由器侧会话失效：网关应自己重新登录并重试
        client.transport.jar["SID"] = "expired-sid"
        devices = await client.list_devices()
        assert devices.total == 2
        assert state.login_calls == 2
        assert client.session.logins == 2
    finally:
        await client.close()


async def test_gateway_api(fake_router) -> None:
    base, state = fake_router
    settings = Settings(router=base, pwd=PASSWORD, token="secret")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        async with _app_client(app) as http:
            response = await http.get("/api/devices")
            assert response.status_code == 401
            assert response.json()["error"] == "unauthorized"
            # /api/* 不弹 Basic 认证
            assert response.headers.get("www-authenticate") is None

            headers = {"Authorization": "Bearer secret"}

            response = await http.get("/api/devices", headers=headers)
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 2
            assert payload["online"] == 1
            assert {"mac", "name", "ip", "online", "band", "rssi", "downBps"} <= set(
                payload["devices"][0]
            )

            response = await http.get(
                "/api/devices/AA:BB:CC:DD:EE:FF", headers=headers
            )
            assert response.status_code == 200
            assert response.json()["name"] == "yoga14s"

            response = await http.get("/api/devices/00:00:00:00:00:00", headers=headers)
            assert response.status_code == 404

            response = await http.put(
                "/api/devices/AA:BB:CC:DD:EE:FF/qos",
                json={"downKbps": 5000, "upKbps": 500},
                headers=headers,
            )
            assert response.status_code == 200
            assert response.json()["ok"] is True
            assert response.json()["applied"] == {"downKbps": 5000, "upKbps": 500}

            response = await http.get(
                "/api/devices/aa:bb:cc:dd:ee:ff/qos", headers=headers
            )
            assert response.json()["downKbps"] == 5000

            response = await http.put(
                "/api/devices/aa:bb:cc:dd:ee:ff/qos",
                json={"downKbps": -1, "upKbps": 1},
                headers=headers,
            )
            assert response.status_code == 400

            response = await http.delete(
                "/api/devices/aa:bb:cc:dd:ee:ff/qos", headers=headers
            )
            assert response.json()["deleted"] is True

            response = await http.get("/api/info", headers=headers)
            assert response.json()["title"] == "SR6110 & Co"

            response = await http.get("/api/health", headers=headers)
            body = response.json()
            assert body["ok"] is True
            assert body["session"]["authenticated"] is True
            assert body["session"]["passwordConfigured"] is True

            response = await http.get("/api/does-not-exist", headers=headers)
            assert response.status_code == 404
            assert response.json()["error"] == "no such api"

            # query token 也认
            response = await http.get("/api/info?token=secret")
            assert response.status_code == 200


async def test_web_login_logout_and_proxy(fake_router) -> None:
    base, state = fake_router
    settings = Settings(
        router=base,
        pwd=PASSWORD,
        token="t",
        web_user="admin",
        web_pwd="web-secret",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        async with _app_client(app) as http:
            # 未登录：网页路径重定向到网关登录页
            response = await http.get("/")
            assert response.status_code == 302
            assert "/__gw/login" in response.headers["location"]

            response = await http.get("/__gw/login")
            assert response.status_code == 200
            assert 'name="password"' in response.text
            assert "/__gw/logo.svg" in response.text

            response = await http.get("/__gw/logo.svg")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("image/svg+xml")

            # 密码错误
            response = await http.post(
                "/__gw/login",
                data={"username": "admin", "password": "wrong", "next": "/"},
            )
            assert response.status_code == 401
            assert "账号或密码错误" in response.text

            # 登录成功：拿到网关会话 cookie
            response = await http.post(
                "/__gw/login",
                data={"username": "admin", "password": "web-secret", "next": "/"},
            )
            assert response.status_code == 303
            assert response.headers["location"] == "/"
            assert http.cookies.get("gw_session")

            response = await http.get("/")
            assert response.status_code == 200
            assert "router admin" in response.text
            # 根路径被带去管理页 hash 路由
            assert "location.replace" in response.text
            assert "#/menu/dashboard" in response.text
            # 路由器页面的退出按钮改为登出网关（桌面 + 移动端）
            assert "__gw/logout" in response.text
            assert ".logout_icon" in response.text
            assert "mobile-top" in response.text
            assert "退出系统" in response.text
            # 浏览器不能拿到路由器的 Set-Cookie
            assert response.headers.get("set-cookie") is None
            # 反代使用的是网关自己登录得到的会话
            assert app.state.transport.jar["SID"] == AUTH_SID
            # 网关凭据不会被转发给路由器
            assert state.router_saw_authorization is False

            # 静态 JS 里含 SessionTimeout 也不能被误判成会话失效
            response = await http.get("/static/app.js")
            assert response.status_code == 200
            assert "SessionTimeout" in response.text

            # 模拟路由器侧会话失效：页面导航应强制探活并自动重新登录
            app.state.transport.jar["SID"] = "expired-sid"
            response = await http.get("/", headers={"Accept": "text/html"})
            assert response.status_code == 200
            assert "router admin" in response.text
            assert app.state.transport.jar["SID"] == AUTH_SID

            # 退出按钮：清掉网关 cookie，回到登录页
            response = await http.get("/__gw/logout")
            assert response.status_code == 303
            assert response.headers["location"] == "/__gw/login"
            assert not http.cookies.get("gw_session")

            response = await http.get("/")
            assert response.status_code == 302
            assert "/__gw/login" in response.headers["location"]


async def test_proxy_reports_login_failure(fake_router) -> None:
    base, _state = fake_router
    settings = Settings(
        router=base,
        pwd="wrong-password",
        token="t",
        web_user="admin",
        web_pwd="web-secret",
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        async with _app_client(app) as http:
            response = await http.post(
                "/__gw/login",
                data={"username": "admin", "password": "web-secret", "next": "/"},
            )
            assert response.status_code == 303

            response = await http.get("/", headers={"Accept": "text/html"})
            assert response.status_code == 502
            assert "网关无法登录路由器" in response.text


async def test_api_requires_session(fake_router) -> None:
    base, _state = fake_router
    app = create_app(Settings(router=base, pwd="", token="t"))

    async with app.router.lifespan_context(app):
        async with _app_client(app) as http:
            headers = {"Authorization": "Bearer t"}

            # 没有凭据也没有会话：401 no session（health 除外）
            response = await http.get("/api/devices", headers=headers)
            assert response.status_code == 401
            assert response.json()["error"] == "no session"

            response = await http.get("/api/health", headers=headers)
            assert response.status_code == 200
            assert response.json()["ok"] is False
            assert response.json()["session"]["active"] is False
