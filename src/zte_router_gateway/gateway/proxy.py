"""HTTP 代理：网页管理走这里。

浏览器只和网关打交道，用的是网关自己持有的路由器会话：
  * 请求里的 Cookie / Authorization 一律剥掉，换成网关 jar 里的 SID；
  * 响应里的 Set-Cookie 一律剥掉，浏览器永远拿不到路由器会话；
  * 所有上游请求使用同一套固定指纹头（UA / Accept-Language）。
"""

from __future__ import annotations

import html
import json
import logging

from fastapi import Request
from starlette.responses import JSONResponse, Response

from ..config import DEFAULT_ACCEPT_LANGUAGE, DEFAULT_USER_AGENT
from ..errors import ErrorKind, RouterError
from ..util import is_session_timeout, now_ms

logger = logging.getLogger(__name__)

PROXY_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]

# 自己会按 payload 重算长度；逐跳头不能原样透传；
# Set-Cookie 必须剥掉，否则浏览器会拿到路由器会话。
_DROP_RESPONSE_HEADERS = {
    b"content-length",
    b"transfer-encoding",
    b"connection",
    b"set-cookie",
}

# 浏览器带上来的这些头不能透给路由器
_DROP_REQUEST_HEADERS = (
    "host",
    "cookie",
    "connection",
    "content-length",
    "accept-encoding",
    "authorization",
    "proxy-authorization",
)


def raw_target(request: Request) -> str:
    """取未经解码的原始请求目标（path + query）。

    `&_=` 必须是真实时间戳，而且编码必须原样保留，所以不能用 request.url。
    """
    raw_path = request.scope.get("raw_path")
    if isinstance(raw_path, bytes):
        target = raw_path.decode("latin-1")
    else:
        target = request.url.path
    query = request.scope.get("query_string") or b""
    if query:
        target += "?" + query.decode("latin-1")
    return target


def _is_navigation(request: Request, target: str) -> bool:
    """页面导航（而不是 SPA 的数据/静态请求）。"""
    if request.method not in ("GET", "HEAD"):
        return False
    if "_type=" in target:
        return False
    return "text/html" in (request.headers.get("accept") or "").lower()


_SHIM_TEMPLATE = r"""<script>(function(){
__ENTRY__
function isLogout(el){
  if(!el||!el.closest)return false;
  if(el.closest('.logout_icon,.menu_bottom_icon.logout_icon'))return true;
  var h=el.closest('span,a,button');
  if(h&&h.closest('.mobile-top')&&((h.textContent||'').replace(/\s+/g,'').indexOf('退出系统')!==-1))return true;
  return false;
}
document.addEventListener('click',function(ev){
  if(!isLogout(ev.target))return;
  ev.preventDefault();ev.stopPropagation();ev.stopImmediatePropagation();
  try{(window.top||window).location.href='/__gw/logout';}catch(x){location.href='/__gw/logout';}
},true);

function addMonitorStyle(){
  if(document.getElementById('gw-monitor-style'))return;
  var style=document.createElement('style');
  style.id='gw-monitor-style';
  style.textContent='#gw-monitor-menu,#gw-monitor-menu *{color:#fff!important;background:transparent!important;}#gw-monitor-menu{cursor:pointer;}#gw-monitor-menu:hover{background:rgba(255,255,255,.12)!important;}';
  (document.head||document.documentElement).appendChild(style);
}
function addMonitorMenu(){
  if(document.getElementById('gw-monitor-menu'))return true;
  var container=document.querySelector('.menu_items')||document.querySelector('.sidebar-container');
  if(!container)return false;
  var items=container.querySelectorAll('.el-menu-item');
  var node;
  if(items.length){
    var source=items[0];
    for(var i=0;i<items.length;i++){
      if(!items[i].classList.contains('is-active')){source=items[i];break;}
    }
    node=source.cloneNode(true);
    node.classList.remove('is-active');
    var actives=node.querySelectorAll('.is-active');
    for(var j=0;j<actives.length;j++){actives[j].classList.remove('is-active');}
    node.style.background='transparent';
    node.style.color='#fff';
    var label=node.querySelector('span');
    if(label){label.textContent='网速监控';}else{node.textContent='网速监控';}
  }else{
    node=document.createElement('div');
    node.style.cssText='display:flex;align-items:center;height:52px;padding:0 20px;color:#fff;font-size:14px;cursor:pointer;';
    node.innerHTML='<span>网速监控</span>';
  }
  node.id='gw-monitor-menu';
  node.addEventListener('click',function(ev){
    ev.preventDefault();ev.stopPropagation();ev.stopImmediatePropagation();
    location.href='/monitor';
  },true);
  addMonitorStyle();
  container.appendChild(node);
  return true;
}
function addMonitorFab(){
  if(document.getElementById('gw-monitor-fab'))return;
  var a=document.createElement('a');
  a.id='gw-monitor-fab';
  a.href='/monitor';
  a.textContent='网速监控';
  a.style.cssText='position:fixed;right:16px;bottom:72px;z-index:9999;background:#3a9cf5;color:#fff;padding:9px 16px;border-radius:20px;text-decoration:none;font-size:13px;box-shadow:0 6px 18px rgba(58,156,245,.4);';
  document.body.appendChild(a);
}
function startShim(){
  addMonitorMenu();
  var pending=false;
  new MutationObserver(function(){
    if(pending)return;pending=true;
    setTimeout(function(){pending=false;addMonitorMenu();},300);
  }).observe(document.body,{childList:true,subtree:true});
  setTimeout(function(){if(!document.getElementById('gw-monitor-menu'))addMonitorFab();},3000);
}
if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',startShim);}else{startShim();}
})();</script>"""


def _web_shim(entry: str) -> bytes:
    """注入到路由器页面的小脚本。

    * 根路径默认是登录视图，把浏览器带到管理页的 hash 路由
      （fragment 不会发给服务器，无法用 302 表达）。
    * 接管两类退出按钮，登出网关而不是调用路由器的登出接口
      （那会把 APP 和后台任务共用的唯一会话一起踢掉）：
        - 桌面左下角的 .logout_icon
        - 移动端 .mobile-top 里文字为“退出系统”的 span（文字两边可能有空格）
    """
    entry_js = ""
    if entry:
        entry_js = (
            "if(window.top===window&&location.pathname==='/'"
            "&&(!location.hash||location.hash==='#')){location.replace("
            + json.dumps(entry)
            + ");}"
        )
    return _SHIM_TEMPLATE.replace("__ENTRY__", entry_js).encode("utf-8")


def _is_data_target(target: str, upstream) -> bool:
    """只有数据接口才做 SessionTimeout 判定。

    SPA 的 JS 包里本身就有 "SessionTimeout" 字样，拿它做全文匹配会把正常的
    静态资源误判成会话失效（正是它导致主包 502、页面空白）。
    """
    if "_type=" in target:
        return True
    content_type = (upstream.header("content-type") or "").lower()
    return "json" in content_type or "xml" in content_type


def _failure(request: Request, exc: RouterError | Exception) -> Response:
    """登录失败时给浏览器一个可读的页面，而不是路由器的登录页/超时 JSON。"""
    detail = str(exc)
    wants_json = (
        "application/json" in (request.headers.get("accept") or "").lower()
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
    )
    if wants_json:
        return JSONResponse(
            {"error": "router login failed", "detail": detail}, status_code=502
        )

    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>网关无法登录路由器</title></head>
<body>
<h1>网关无法登录路由器</h1>
<p>{html.escape(detail)}</p>
<p>请检查 <code>ROUTER_URL</code> / <code>ROUTER_USER</code> / <code>ROUTER_PWD</code>，
以及网关到路由器是否可达。网关会按退避策略自动重试。</p>
</body></html>"""
    return Response(page, status_code=502, media_type="text/html; charset=utf-8")


async def proxy(request: Request) -> Response:
    settings = request.app.state.settings
    session = request.app.state.session
    transport = request.app.state.transport

    try:
        # /api/* 里没匹配上的路径不要丢给路由器
        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            return JSONResponse({"error": "no such api"}, status_code=404)

        target = raw_target(request)
        body = await request.body()

        # 统一转小写键，后面按小写查找
        headers = {key.lower(): value for key, value in request.headers.items()}
        for name in _DROP_REQUEST_HEADERS:
            headers.pop(name, None)
        headers["host"] = settings.netloc
        headers["connection"] = "close"
        # 固定指纹：登录、API、反代必须表现得像同一个客户端
        headers["user-agent"] = DEFAULT_USER_AGENT
        headers["accept-language"] = DEFAULT_ACCEPT_LANGUAGE
        if "origin" in headers:
            headers["origin"] = settings.origin
        if "referer" in headers:
            headers["referer"] = settings.referer

        # 会话没了就先自己登录；失败就直接给浏览器报错
        if not transport.jar.get("SID") or not session.authed:
            try:
                await session.renew_session()
            except RouterError as exc:
                return _failure(request, exc)

        # 页面导航前先探活：否则路由器会把登录页当作 200 返回给浏览器
        if _is_navigation(request, target) and transport.jar.get("SID"):
            if not await session.probe_authed(force=True):
                try:
                    await session.renew_session()
                except RouterError as exc:
                    return _failure(request, exc)

        cookie = transport.jar_header()
        if cookie:
            headers["cookie"] = cookie

        upstream = await transport.request(
            request.method,
            target,
            headers=headers,
            body=body,
            timeout=settings.proxy_timeout,
        )

        # 会话失效时自动重新登录，然后重试一次（只针对数据接口，静态资源不折腾）
        if _is_data_target(target, upstream) and is_session_timeout(upstream.text):
            session.authed = False
            try:
                await session.renew_session()
            except RouterError as exc:
                return _failure(request, exc)
            cookie = transport.jar_header()
            if cookie:
                headers["cookie"] = cookie
            upstream = await transport.request(
                request.method,
                target,
                headers=headers,
                body=body,
                timeout=settings.proxy_timeout,
            )
            if (
                _is_data_target(target, upstream)
                and is_session_timeout(upstream.text)
            ):
                return _failure(
                    request,
                    RouterError("重新登录后仍然 SessionTimeout", ErrorKind.EXPIRED),
                )

        if settings.debug:
            logger.debug(
                "[proxy] %s %s -> %s body=%dB resp=%dB",
                request.method,
                target[:90],
                upstream.status,
                len(body),
                len(upstream.body),
            )

        # 会话是网关自己的：把响应里可能轮换的 SID 记下来
        sid = transport.jar.get("SID")
        if sid:
            session.sid = sid
            session.at = now_ms()
            session.last_error = None
            session.verified = True
            session.authed = True
        session.last_use = now_ms()

        payload = upstream.body
        # 给所有 HTML 页面注入：根路径跳转 + 退出按钮接管
        if (
            request.method == "GET"
            and "_type=" not in target
            and upstream.status == 200
            and "text/html" in (upstream.header("content-type") or "").lower()
        ):
            script = _web_shim(settings.web_entry)
            # 尽量早执行，让 SPA 启动时读到的就是目标 hash
            if b"<head>" in payload:
                payload = payload.replace(b"<head>", b"<head>" + script, 1)
            elif b"</body>" in payload:
                payload = payload.replace(b"</body>", script + b"</body>", 1)
            else:
                payload = payload + script

        out_headers: list[tuple[bytes, bytes]] = []
        for key, value in upstream.raw_headers:
            lower = key.lower()
            if lower in _DROP_RESPONSE_HEADERS:
                continue
            if lower == b"location":
                # 路由器的重定向不能把浏览器带离网关
                location = value.decode("latin-1")
                if location.startswith(settings.origin):
                    relocated = location[len(settings.origin) :] or "/"
                    value = relocated.encode("latin-1")
            out_headers.append((key, value))
        # 204/304 不允许带正文，也不该带 Content-Length
        if upstream.status not in (204, 304):
            out_headers.append(
                (b"content-length", str(len(payload)).encode("ascii"))
            )

        response = Response(content=payload, status_code=upstream.status)
        # 直接接管 raw_headers，保留重复的响应头
        response.raw_headers = out_headers
        return response
    except RouterError as exc:
        logger.error("[proxy] %s %s -> %s", request.method, request.url.path, exc)
        return _failure(request, exc)
    except Exception as exc:  # noqa: BLE001 - 反代错误一律 502
        logger.exception("[proxy] 未处理异常")
        return Response(
            f"gateway error: {exc}",
            status_code=502,
            media_type="text/plain; charset=utf-8",
        )
