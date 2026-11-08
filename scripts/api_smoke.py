#!/usr/bin/env python
"""对运行中的网关做一轮全量 endpoint 冒烟测试（零依赖，仅用标准库）。

用法：

    # 只读测试（不碰路由器配置）
    uv run python scripts/api_smoke.py --token <APP_TOKEN> --read-only

    # 全量测试：会临时给一台设备设限速，测完自动恢复
    uv run python scripts/api_smoke.py --token <APP_TOKEN> --web-pwd <GW_PWD>

    # 指定目标设备和测试速率（Kbps）
    uv run python scripts/api_smoke.py --token <APP_TOKEN> \
        --mac 64:bc:58:bc:a1:4c --down-kbps 200000 --up-kbps 200000

写测试的选机策略：优先选「离线且当前没有限速规则」的设备；如果所有设备都有
规则，则选最后一台，并在结束时恢复它原来的限速值。任何一步失败都会在 finally
里尝试恢复，并打印原始值，方便人工兜底。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:8099"
DEFAULT_DOWN_KBPS = 100_000
DEFAULT_UP_KBPS = 100_000


# --------------------------------------------------------------------- output


@dataclass
class Report:
    results: list[tuple[str, str, str]] = field(default_factory=list)

    def _add(self, level: str, name: str, detail: str = "") -> None:
        self.results.append((level, name, detail))
        line = f"[{level}] {name}"
        if detail:
            line += f"  --  {detail}"
        print(line)

    def passed(self, name: str, detail: str = "") -> None:
        self._add("PASS", name, detail)

    def failed(self, name: str, detail: str = "") -> None:
        self._add("FAIL", name, detail)

    def skipped(self, name: str, detail: str = "") -> None:
        self._add("SKIP", name, detail)

    def warned(self, name: str, detail: str = "") -> None:
        self._add("WARN", name, detail)

    def summary(self) -> int:
        counts = Counter(level for level, _, _ in self.results)
        print("\n===== 汇总 =====")
        print(
            f"PASS {counts['PASS']}  FAIL {counts['FAIL']}  "
            f"WARN {counts['WARN']}  SKIP {counts['SKIP']}  共 {len(self.results)} 项"
        )
        return 1 if counts["FAIL"] else 0


def expect(report: Report, name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        report.passed(name, detail)
    else:
        report.failed(name, detail)
    return condition


# ----------------------------------------------------------------------- http


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """反代里 302 是路由器行为的一部分，不要自动跟随。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class HttpClient:
    def __init__(
        self,
        base: str,
        token: str,
        timeout: float,
        web_user: str = "",
        web_pwd: str = "",
    ) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.web_user = web_user
        self.web_pwd = web_pwd
        self.web_cookie = ""
        # 本机 127.0.0.1 不能走系统代理
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    def _send(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        data: bytes | None,
    ) -> tuple[int, dict[str, str], str]:
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                return (
                    response.status,
                    {key.lower(): value for key, value in response.headers.items()},
                    response.read().decode("utf-8", "replace"),
                )
        except urllib.error.HTTPError as exc:
            return (
                exc.code,
                {key.lower(): value for key, value in exc.headers.items()}
                if exc.headers
                else {},
                exc.read().decode("utf-8", "replace"),
            )

    def raw(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        cookie: bool = False,
        body: Any | None = None,
    ) -> tuple[int, dict[str, str], str]:
        data = None
        headers: dict[str, str] = {}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        if cookie and self.web_cookie:
            headers["Cookie"] = self.web_cookie
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return self._send(method, path, headers, data)

    def login(self) -> tuple[bool, str]:
        """用 GW_USER / GW_PWD 登录网关，保存会话 cookie。"""
        data = urllib.parse.urlencode(
            {"username": self.web_user, "password": self.web_pwd, "next": "/"}
        ).encode("utf-8")
        status, headers, text = self._send(
            "POST",
            "/__gw/login",
            {"Content-Type": "application/x-www-form-urlencoded"},
            data,
        )
        set_cookie = headers.get("set-cookie", "")
        if status in (302, 303) and set_cookie:
            self.web_cookie = set_cookie.split(";")[0]
            return True, f"{status}"
        return False, f"{status} {text[:120]}"

    def call(self, method: str, path: str, **kwargs: Any) -> tuple[int, str, str]:
        status, headers, text = self.raw(method, path, **kwargs)
        return status, headers.get("content-type", ""), text

    def json(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any, str]:
        status, _headers, text = self.raw(method, path, **kwargs)
        return status, parse_json(text), text


def parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def qos_path(mac: str) -> str:
    return f"/api/devices/{urllib.parse.quote(mac, safe='')}/qos"


def device_path(mac: str) -> str:
    return f"/api/devices/{urllib.parse.quote(mac, safe='')}"


# ---------------------------------------------------------------------- tests


def test_auth(report: Report, http: HttpClient) -> None:
    status, data, text = http.json("GET", "/api/health", auth=False)
    expect(
        report,
        "GET /api/health（无 token）-> 401 unauthorized",
        status == 401 and isinstance(data, dict) and data.get("error") == "unauthorized",
        f"{status} {text[:120]}",
    )

    quoted = urllib.parse.quote(http.token, safe="")
    status, data, text = http.json("GET", f"/api/info?token={quoted}", auth=False)
    expect(
        report,
        "GET /api/info?token=...（query token 鉴权）",
        status == 200 and isinstance(data, dict) and "title" in data,
        f"{status} {text[:120]}",
    )


def test_health(report: Report, http: HttpClient, base: str) -> bool:
    status, data, text = http.json("GET", "/api/health")
    ok = expect(
        report,
        "GET /api/health",
        status == 200 and isinstance(data, dict) and isinstance(data.get("session"), dict),
        f"{status} {text[:120]}",
    )
    if not ok:
        return False

    session = data.get("session") or {}
    if data.get("ok"):
        report.passed(
            "health.ok == true",
            f"logins={session.get('logins')} age={session.get('ageSeconds')}s",
        )
        return True

    report.warned(
        "health.ok == false",
        f"lastError={session.get('lastError')} "
        f"active={session.get('active')} passwordConfigured={session.get('passwordConfigured')}",
    )
    if not session.get("active"):
        print(
            f"\n提示：网关还没有会话。先在浏览器打开 {base}/ 登录一次路由器，然后重跑本脚本。\n"
        )
    return False


def test_info(report: Report, http: HttpClient) -> None:
    status, data, text = http.json("GET", "/api/info")
    expect(
        report,
        "GET /api/info",
        status == 200
        and isinstance(data, dict)
        and {"title", "firmware", "mode"} <= set(data),
        f"{status} {text[:120]}",
    )


def test_devices(report: Report, http: HttpClient) -> list[dict[str, Any]]:
    status, data, text = http.json("GET", "/api/devices")
    ok = expect(
        report,
        "GET /api/devices",
        status == 200
        and isinstance(data, dict)
        and isinstance(data.get("devices"), list)
        and data.get("total") == len(data.get("devices") or []),
        f"{status} {text[:120]}",
    )
    if not ok:
        return []

    devices: list[dict[str, Any]] = data["devices"]
    online = sum(1 for device in devices if device.get("online"))
    expect(
        report,
        "devices 统计一致（total/online/offline）",
        data.get("online") == online
        and data.get("offline") == len(devices) - online,
        f"total={data.get('total')} online={data.get('online')} offline={data.get('offline')}",
    )

    status, data, text = http.json("GET", "/api/devices?status=online")
    expect(
        report,
        "GET /api/devices?status=online",
        status == 200
        and isinstance(data, dict)
        and all(device.get("online") for device in data.get("devices") or [])
        and data.get("offline") == 0,
        f"{status} total={(data or {}).get('total')}",
    )

    status, data, text = http.json("GET", "/api/devices?status=offline")
    expect(
        report,
        "GET /api/devices?status=offline",
        status == 200
        and isinstance(data, dict)
        and not any(device.get("online") for device in data.get("devices") or [])
        and data.get("online") == 0,
        f"{status} total={(data or {}).get('total')}",
    )

    status, data, text = http.json("GET", "/api/devices?status=all")
    expect(
        report,
        "GET /api/devices?status=all",
        status == 200 and isinstance(data, dict) and data.get("total") == len(devices),
        f"{status}",
    )

    if not devices:
        report.skipped("单设备 / 限速测试", "设备列表为空")
        return []

    first = devices[0]
    mac = str(first.get("mac") or "")
    if not mac:
        report.skipped("单设备 / 限速测试", "设备缺少 mac 字段")
        return devices

    status, data, text = http.json("GET", device_path(mac))
    expect(
        report,
        f"GET /api/devices/{{mac}}（大小写不敏感: {mac.lower()}）",
        status == 200
        and isinstance(data, dict)
        and str(data.get("mac", "")).lower() == mac.lower(),
        f"{status} {text[:120]}",
    )

    status, data, text = http.json("GET", "/api/devices/00:00:00:00:00:00")
    expect(
        report,
        "GET /api/devices/{不存在的mac} -> 404 device not found",
        status == 404 and isinstance(data, dict) and data.get("error") == "device not found",
        f"{status} {text[:120]}",
    )

    status, data, text = http.json("GET", qos_path(mac))
    expect(
        report,
        "GET /api/devices/{mac}/qos",
        status == 200
        and isinstance(data, dict)
        and {"mac", "limited", "downKbps", "upKbps", "instId"} <= set(data),
        f"{status} limited={(data or {}).get('limited')}",
    )

    return devices


def test_bad_qos_writes(report: Report, http: HttpClient, mac: str) -> None:
    path = qos_path(mac)

    status, data, text = http.json("PUT", path, body={"downKbps": -1, "upKbps": 100})
    expect(
        report,
        "PUT qos（负数）-> 400",
        status == 400 and isinstance(data, dict) and data.get("error") == "bad request",
        f"{status} {text[:120]}",
    )

    status, data, text = http.json("PUT", path, body={"downKbps": "abc", "upKbps": 100})
    expect(
        report,
        "PUT qos（非数字）-> 400",
        status == 400,
        f"{status} {text[:120]}",
    )

    status, data, text = http.json("POST", path, body={"upKbps": -5})
    expect(
        report,
        "POST qos（别名路由 + 缺字段）-> 400",
        status == 400,
        f"{status} {text[:120]}",
    )


def test_api_fallback(report: Report, http: HttpClient) -> None:
    status, data, text = http.json("GET", "/api/does-not-exist")
    expect(
        report,
        "GET /api/does-not-exist -> 404 no such api",
        status == 404 and isinstance(data, dict) and data.get("error") == "no such api",
        f"{status} {text[:120]}",
    )


def test_gateway_endpoints(
    report: Report, http: HttpClient, has_web_credentials: bool
) -> None:
    status, headers, _text = http.raw("GET", "/", auth=False)
    expect(
        report,
        "GET /（未登录）-> 302 登录页",
        status == 302 and "/__gw/login" in headers.get("location", ""),
        f"{status} {headers.get('location', '')}",
    )

    status, _headers, text = http.raw("GET", "/__gw/login", auth=False)
    expect(
        report,
        "GET /__gw/login（登录页）",
        status == 200 and 'name="password"' in text and "/__gw/logo.svg" in text,
        f"{status}",
    )

    status, headers, _headers_text = http.raw("GET", "/__gw/logo.svg", auth=False)
    expect(
        report,
        "GET /__gw/logo.svg",
        status == 200 and "image/svg+xml" in headers.get("content-type", ""),
        f"{status}",
    )

    if not has_web_credentials:
        report.skipped("网页登录 / 反代 / 登出", "未提供 GW_PWD 或 --web-pwd")
        return

    ok, detail = http.login()
    expect(report, "POST /__gw/login", ok, detail)
    if not ok:
        return

    status, headers, text = http.raw("GET", "/", auth=False, cookie=True)
    if status == 200 and "text/html" in headers.get("content-type", "").lower():
        expect(
            report,
            "GET /（网关登录 + 网页反代）",
            "set-cookie" not in headers
            and "#/menu/dashboard" in text
            and "__gw/logout" in text,
            f"{status} {len(text)}B",
        )
    elif status in (301, 302, 303, 307, 308):
        report.skipped("GET /（网页反代）", f"路由器返回 {status}，未跟随重定向")
    else:
        report.failed("GET /（网页反代）", f"{status} {text[:120]}")

    status, data, _text = http.json("GET", "/openapi.json", auth=False, cookie=True)
    expect(
        report,
        "GET /openapi.json",
        status == 200 and isinstance(data, dict) and "openapi" in data,
        f"{status}",
    )

    status, _ctype, text = http.call("GET", "/docs", auth=False, cookie=True)
    expect(report, "GET /docs", status == 200 and "swagger" in text.lower(), f"{status}")

    status, headers, _text = http.raw("GET", "/__gw/logout", auth=False, cookie=True)
    expect(
        report,
        "GET /__gw/logout",
        status == 303 and headers.get("location") == "/__gw/login",
        f"{status} {headers.get('location', '')}",
    )
    http.web_cookie = ""

    status, headers, _text = http.raw("GET", "/", auth=False)
    expect(
        report,
        "GET /（登出后）-> 302 登录页",
        status == 302 and "/__gw/login" in headers.get("location", ""),
        f"{status} {headers.get('location', '')}",
    )


def pick_target(
    args: argparse.Namespace, http: HttpClient, devices: list[dict[str, Any]]
) -> tuple[str | None, dict[str, Any] | None]:
    """选一台设备做写入测试，并返回它当前的限速状态。"""
    if args.mac:
        return args.mac, _get_qos(http, args.mac)

    # 离线优先，尽量不影响正在使用的设备
    ordered = sorted(devices, key=lambda device: 0 if not device.get("online") else 1)
    fallback: tuple[str, dict[str, Any] | None] | None = None
    for device in ordered:
        mac = str(device.get("mac") or "")
        if not mac:
            continue
        current = _get_qos(http, mac)
        if current is not None and current.get("limited") is False:
            return mac, current
        fallback = (mac, current)
    return fallback if fallback else (None, None)


def _get_qos(http: HttpClient, mac: str) -> dict[str, Any] | None:
    status, data, _text = http.json("GET", qos_path(mac))
    return data if status == 200 and isinstance(data, dict) else None


def test_qos_write_cycle(
    report: Report,
    http: HttpClient,
    mac: str,
    original: dict[str, Any] | None,
    down_kbps: int,
    up_kbps: int,
) -> None:
    path = qos_path(mac)
    print(
        f"\n注意：将对 {mac} 做临时限速写入（{down_kbps}/{up_kbps} Kbps），"
        f"测试结束会自动恢复。原状态: {original}\n"
    )

    mutated = False
    try:
        status, data, text = http.json(
            "PUT", path, body={"downKbps": down_kbps, "upKbps": up_kbps}
        )
        applied = expect(
            report,
            f"PUT {path}",
            status == 200
            and isinstance(data, dict)
            and data.get("ok") is True
            and data.get("applied") == {"downKbps": down_kbps, "upKbps": up_kbps},
            f"{status} {text[:160]}",
        )
        if applied:
            mutated = True

        current = _get_qos(http, mac) or {}
        expect(
            report,
            "GET qos 验证写入生效",
            current.get("limited") is True
            and current.get("downKbps") == down_kbps
            and current.get("upKbps") == up_kbps,
            f"limited={current.get('limited')} "
            f"down={current.get('downKbps')} up={current.get('upKbps')}",
        )

        status, data, text = http.json("DELETE", path)
        expect(
            report,
            f"DELETE {path}",
            status == 200 and isinstance(data, dict) and data.get("deleted") is True,
            f"{status} {text[:160]}",
        )

        current = _get_qos(http, mac) or {}
        expect(
            report,
            "GET qos 验证限速已取消",
            current.get("limited") is False,
            f"limited={current.get('limited')} instId={current.get('instId')}",
        )
    finally:
        if mutated:
            if original and original.get("limited"):
                http.call(
                    "PUT",
                    path,
                    body={
                        "downKbps": original.get("downKbps"),
                        "upKbps": original.get("upKbps"),
                    },
                )
            else:
                http.call("DELETE", path)

            current = _get_qos(http, mac) or {}
            if original and original.get("limited"):
                expect(
                    report,
                    "恢复原限速",
                    current.get("limited") is True
                    and current.get("downKbps") == original.get("downKbps")
                    and current.get("upKbps") == original.get("upKbps"),
                    f"原值 down={original.get('downKbps')} up={original.get('upKbps')}",
                )
            else:
                expect(
                    report,
                    "确认已清除测试限速",
                    current.get("limited") is False,
                    f"limited={current.get('limited')}",
                )


# ----------------------------------------------------------------------- main


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对运行中的 zte-router-gateway 做全量 endpoint 冒烟测试"
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help=f"网关地址，默认 {DEFAULT_BASE}")
    parser.add_argument(
        "--token",
        default=os.environ.get("APP_TOKEN", ""),
        help="APP_TOKEN（默认读环境变量 APP_TOKEN）",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="单请求超时秒数")
    parser.add_argument(
        "--web-user",
        default=os.environ.get("GW_USER") or "admin",
        help="网关网页账号（默认 GW_USER）",
    )
    parser.add_argument(
        "--web-pwd",
        default=os.environ.get("GW_PWD", ""),
        help="网关网页密码（默认 GW_PWD），用于测试网页反代",
    )
    parser.add_argument(
        "--read-only", action="store_true", help="跳过所有写操作（PUT/DELETE 限速）"
    )
    parser.add_argument("--mac", help="指定写入测试的目标设备 MAC")
    parser.add_argument(
        "--down-kbps", type=int, default=DEFAULT_DOWN_KBPS, help="测试用下行限速"
    )
    parser.add_argument(
        "--up-kbps", type=int, default=DEFAULT_UP_KBPS, help="测试用上行限速"
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    token = args.token or os.environ.get("APP_TOKEN", "")
    if not token:
        print("缺少 token：用 --token 或环境变量 APP_TOKEN 提供", file=sys.stderr)
        return 2

    http = HttpClient(args.base, token, args.timeout, args.web_user, args.web_pwd)
    report = Report()

    print(f"网关: {args.base}\n")
    try:
        status, _ctype, text = http.call("GET", "/api/health", auth=False)
    except (urllib.error.URLError, OSError) as exc:
        print(f"无法连接 {args.base}: {exc}", file=sys.stderr)
        return 2

    test_auth(report, http)
    logged_in = test_health(report, http, args.base)
    test_info(report, http)
    devices = test_devices(report, http)
    test_api_fallback(report, http)
    test_gateway_endpoints(report, http, bool(args.web_pwd))

    if devices:
        first_mac = str(devices[0].get("mac") or "")
        if first_mac:
            test_bad_qos_writes(report, http, first_mac)
        else:
            report.skipped("PUT/POST qos 参数校验", "设备缺少 mac 字段")

        if args.read_only:
            report.skipped("PUT/DELETE 限速写入测试", "--read-only")
        else:
            mac, original = pick_target(args, http, devices)
            if mac is None:
                report.skipped("PUT/DELETE 限速写入测试", "没有可用设备")
            else:
                test_qos_write_cycle(
                    report, http, mac, original, args.down_kbps, args.up_kbps
                )
    else:
        report.skipped("PUT/DELETE 限速写入测试", "设备列表为空")

    if not logged_in:
        print("\n提示：网关当前未登录，/api/* 中依赖会话的用例失败是预期内的。")

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(main())
