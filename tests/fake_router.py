"""测试用的假路由器：模拟中兴固件的行为，不依赖真机。

所有接口都在 `/?_type=...&_tag=...` 上，靠 query 分派。
"""

from __future__ import annotations

import hashlib
from typing import Any

from aiohttp import web

PASSWORD = "secret"
LOGIN_TOKEN = "tok-abc"
SESSION_TOKEN = "sess-token-1"
BASIC_SID = "basic-sid"
AUTH_SID = "authed-sid"
RULE_NAME = "IGD.QoSBandwidthRule2"

ADMIN_HTML = "<html><body><div id='app'>router admin</div></body></html>"
LOGIN_HTML = "<html><body><div class='login'>login</div></body></html>"

LAN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ajax_response_xml_root>
<OBJ_GLOBAL_ENV>
<Instance>
<ParaName>WEBTitle</ParaName><ParaValue>SR6110 &amp; Co</ParaValue>
<ParaName>SoftwareVer</ParaName><ParaValue>V1.0.0</ParaValue>
<ParaName>Mode</ParaName><ParaValue>Gateway</ParaValue>
</Instance>
</OBJ_GLOBAL_ENV>
<OBJ_LAN_INFO_ID>
<Instance>
<ParaName>MACAddress</ParaName><ParaValue>AA:BB:CC:DD:EE:FF</ParaValue>
<ParaName>DevName</ParaName><ParaValue>yoga14s</ParaValue>
<ParaName>HostName</ParaName><ParaValue>yoga14s</ParaValue>
<ParaName>IPAddress</ParaName><ParaValue>192.168.100.54</ParaValue>
<ParaName>Active</ParaName><ParaValue>1</ParaValue>
<ParaName>Band</ParaName><ParaValue>5G</ParaValue>
<ParaName>InterfaceType</ParaName><ParaValue>0</ParaValue>
<ParaName>Rssi</ParaName><ParaValue>-70</ParaValue>
<ParaName>UploadSpeed</ParaName><ParaValue>0</ParaValue>
<ParaName>DownloadSpeed</ParaName><ParaValue>393</ParaValue>
<ParaName>OnlineTime</ParaName><ParaValue>2822400</ParaValue>
</Instance>
<Instance>
<ParaName>MACAddress</ParaName><ParaValue>AA:BB:CC:DD:EE:00</ParaValue>
<ParaName>HostName</ParaName><ParaValue>tv</ParaValue>
<ParaName>IPAddress</ParaName><ParaValue>0.0.0.0</ParaValue>
<ParaName>Active</ParaName><ParaValue>0</ParaValue>
<ParaName>InterfaceType</ParaName><ParaValue>1</ParaValue>
<ParaName>Rssi</ParaName><ParaValue>0</ParaValue>
<ParaName>OnlineTime</ParaName><ParaValue>0</ParaValue>
<ParaName>InactiveTime</ParaName><ParaValue>2026-11-07 10:00:00</ParaValue>
</Instance>
</OBJ_LAN_INFO_ID>
</ajax_response_xml_root>"""


class FakeState:
    def __init__(self) -> None:
        self.login_calls = 0
        self.token_calls = 0
        self.qos: dict[str, dict[str, str]] = {}
        self.router_saw_authorization = False


def _is_authed(request: web.Request) -> bool:
    return request.cookies.get("SID") == AUTH_SID


def create_fake_app() -> tuple[web.Application, FakeState]:
    app = web.Application()
    state = FakeState()

    async def index(request: web.Request) -> web.StreamResponse:
        if request.headers.get("Authorization"):
            # 网关绝不能把浏览器的 Basic 凭据转给路由器
            state.router_saw_authorization = True

        tag = request.query.get("_tag")
        method = request.method

        if tag == "login_token_json":
            state.token_calls += 1
            return web.json_response(
                {"logintoken": LOGIN_TOKEN, "_sessionToken": SESSION_TOKEN}
            )

        if tag == "login_entry":
            if method == "GET":  # 只读探测
                return web.json_response({})
            form = await request.post()
            state.login_calls += 1
            expected = hashlib.sha256(
                (PASSWORD + LOGIN_TOKEN).encode("utf-8")
            ).hexdigest()
            if form.get("Password") != expected:
                return web.json_response({"loginErrType": "e_invalid_user_pwd"})
            response = web.json_response(
                {"login_need_refresh": True, "sess_token": SESSION_TOKEN}
            )
            response.set_cookie("SID", AUTH_SID)
            return response

        if tag == "aclmode_data":
            if not _is_authed(request):
                return web.json_response({"SessionTimeout": True}, status=404)
            return web.json_response({"mode": "gateway"})

        if tag == "localnet_lan_info_lua":
            if not _is_authed(request):
                return web.json_response({"SessionTimeout": True}, status=404)
            return web.Response(text=LAN_XML, content_type="text/xml")

        if tag == "localnet_qosdyn_rulebymac":
            if not _is_authed(request):
                return web.json_response({"SessionTimeout": True}, status=404)
            mac = request.query.get("_InstID") or ""
            rule = state.qos.get(mac)
            rules: list[dict[str, Any]] = []
            if rule:
                rules.append(
                    {
                        "ViewName": RULE_NAME,
                        "UserName": mac,
                        "BandwithRateMaxDown": rule["down"],
                        "BandwithRateMaxUp": rule["up"],
                    }
                )
            return web.json_response({"data": {"OBJ_QOS_BCRULE_BYMAC": rules}})

        if tag == "localnet_qosdyn_bandwithrule_lua":
            if not _is_authed(request):
                return web.json_response({"SessionTimeout": True}, status=404)
            form = await request.post()
            if not form.get("_sessionTOKEN"):
                return web.Response(
                    text="<Response><IF_ERRORID>-1452</IF_ERRORID>"
                    "<IF_ERRORSTR>页面过期</IF_ERRORSTR></Response>",
                    content_type="text/xml",
                )
            if form.get("IF_ACTION") == "Delete":
                # 固件按 InstID 删除；假路由器只有一条规则，直接清空
                state.qos.clear()
            else:
                state.qos[form.get("MACDev", "")] = {
                    "down": form.get("BandwithRateMaxDown", ""),
                    "up": form.get("BandwithRateMaxUp", ""),
                }
            return web.Response(
                text="<Response><IF_ERRORID>0</IF_ERRORID>"
                "<IF_ERRORSTR>SUCC</IF_ERRORSTR></Response>",
                content_type="text/xml",
            )

        # 登录页 / 管理页
        if _is_authed(request):
            response = web.Response(text=ADMIN_HTML, content_type="text/html")
            # 路由器可能下发 cookie；网关必须把它挡在浏览器之外
            response.set_cookie("ROUTER_COOKIE", "should-not-leak")
            return response
        response = web.Response(text=LOGIN_HTML, content_type="text/html")
        response.set_cookie("SID", BASIC_SID)
        return response

    async def app_asset(request: web.Request) -> web.StreamResponse:
        # 真实的 SPA 主包里就含有 SessionTimeout 字样
        return web.Response(
            text="function f(){ return 'SessionTimeout'; }",
            content_type="application/javascript",
        )

    app.router.add_get("/static/app.js", app_asset)
    app.router.add_route("*", "/", index)
    return app, state
