"""通往路由器的原始 HTTP 传输层。

这是唯一与具体技术栈（aiohttp）耦合的地方，上层只看到 Response。
行为上刻意贴近浏览器的原始请求：
  * 自签证书：不校验（ssl=False）
  * 强制 IPv4（family=AF_INET）
  * 每个请求独立连接（force_close + Connection: close）
  * 不跟随重定向（allow_redirects=False）
  * 不请求压缩（不发送 Accept-Encoding），避免正文被 gzip 后无法做文本判定
  * cookie 由网关自己维护（DummyCookieJar），不用 aiohttp 的隐式 jar
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from typing import Iterable, Mapping

import aiohttp
from aiohttp import hdrs

from .config import DEFAULT_ACCEPT_LANGUAGE, DEFAULT_USER_AGENT, Settings
from .errors import ErrorKind, RouterError


@dataclass(slots=True)
class Response:
    """路由器响应。headers 保留原始字节对，以便透传重复的 Set-Cookie。"""

    status: int
    raw_headers: tuple[tuple[bytes, bytes], ...]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def header(self, name: str) -> str | None:
        wanted = name.lower().encode("latin-1")
        for key, value in self.raw_headers:
            if key.lower() == wanted:
                return value.decode("latin-1")
        return None

    def header_all(self, name: str) -> list[str]:
        wanted = name.lower().encode("latin-1")
        return [
            value.decode("latin-1")
            for key, value in self.raw_headers
            if key.lower() == wanted
        ]


class Transport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # 路由器要求「先有基础 SID cookie，再登录」，所以维护一个真正的 cookie jar
        self.jar: dict[str, str] = {}
        self._client: aiohttp.ClientSession | None = None

    # ---------------------------------------------------------------- lifecycle

    async def open(self) -> None:
        connector = aiohttp.TCPConnector(
            ssl=False,
            family=socket.AF_INET,
            force_close=True,
        )
        self._client = aiohttp.ClientSession(
            connector=connector,
            cookie_jar=aiohttp.DummyCookieJar(),
            auto_decompress=False,
            # 路由器在内网，绝不走系统/环境变量里的代理
            trust_env=False,
            # 这些头一律由调用方显式提供，避免 aiohttp 擅自补上
            skip_auto_headers=frozenset(
                {hdrs.ACCEPT_ENCODING, hdrs.USER_AGENT, hdrs.CONTENT_TYPE}
            ),
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------- jar

    def jar_header(self) -> str:
        return "; ".join(f"{key}={value}" for key, value in self.jar.items())

    def absorb_cookies(self, values: Iterable[str]) -> None:
        for cookie in values:
            first = cookie.split(";", 1)[0]
            name, sep, value = first.partition("=")
            if sep and name:
                self.jar[name] = value

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def merge_headers(*layers: Mapping[str, str]) -> dict[str, str]:
        """大小写不敏感的合并，后者覆盖前者。

        按大小写不敏感比较，从根本上避免同一个头出现两份的问题。
        """
        merged: dict[str, str] = {}
        for layer in layers:
            for key, value in layer.items():
                for existing in [k for k in merged if k.lower() == key.lower()]:
                    del merged[existing]
                merged[key] = value
        return merged

    @staticmethod
    def without(headers: Mapping[str, str], *names: str) -> dict[str, str]:
        drop = {name.lower() for name in names}
        return {key: value for key, value in headers.items() if key.lower() not in drop}

    def _default_headers(self) -> dict[str, str]:
        return {
            "Host": self.settings.netloc,
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self.settings.referer,
            "Origin": self.settings.origin,
            "Connection": "close",
        }

    # ----------------------------------------------------------------- request

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: str | bytes | None = None,
        timeout: float | None = None,
    ) -> Response:
        client = self._client
        if client is None:
            raise RouterError("transport 尚未启动", ErrorKind.NETWORK)

        merged = self.merge_headers(self._default_headers(), headers or {})
        # 只有调用方没显式给 Cookie 时才用网关自己的 jar
        if not any(key.lower() == "cookie" for key in merged):
            cookie = self.jar_header()
            if cookie:
                merged["Cookie"] = cookie
        # Content-Length / Transfer-Encoding 交给 aiohttp 按实际 body 计算
        merged = self.without(merged, "content-length", "transfer-encoding")

        if isinstance(body, str):
            body = body.encode("utf-8")
        if body == b"":
            # 空 body 不设置 Content-Length
            body = None

        try:
            async with client.request(
                method.upper(),
                self.settings.router + path,
                headers=merged,
                data=body,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=timeout if timeout is not None else self.settings.router_timeout
                ),
            ) as response:
                payload = await response.read()
                self.absorb_cookies(response.headers.getall(hdrs.SET_COOKIE, []))
                return Response(
                    status=response.status,
                    raw_headers=tuple(response.raw_headers),
                    body=payload,
                )
        except asyncio.TimeoutError as exc:
            raise RouterError("router timeout", ErrorKind.TIMEOUT) from exc
        except aiohttp.ClientError as exc:
            raise RouterError(f"router request failed: {exc}", ErrorKind.NETWORK) from exc
