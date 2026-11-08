"""与协议无关的小工具：哈希、表单编码、超时判定。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from typing import Any, Mapping
from urllib.parse import quote

# 表单编码除 [A-Za-z0-9_.-~] 外还保留这些字符（与登录页的编码方式一致）；
# Python 的 quote() 默认会转义它们，必须显式放行。
URL_ENCODE_SAFE = "!~*'()"

_SESSION_TIMEOUT_RE = re.compile(r"SessionTimeout|session\s*timeout", re.IGNORECASE)


def now_ms() -> int:
    """毫秒时间戳。路由器的 `&_=` cache-buster 必须是真实时间戳。"""
    return int(time.time() * 1000)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def form_encode(fields: Mapping[str, Any]) -> str:
    """表单编码，空格编码为 %20 而不是 +。"""
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        encoded_key = quote(str(key), safe=URL_ENCODE_SAFE)
        encoded_value = quote(str(value), safe=URL_ENCODE_SAFE)
        parts.append(f"{encoded_key}={encoded_value}")
    return "&".join(parts)


def parse_json(text: str | bytes) -> Any | None:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def is_session_timeout(text: str) -> bool:
    """路由器判断会话失效的唯一可靠信号。"""
    return bool(_SESSION_TIMEOUT_RE.search(text))


def round_half_up(value: float) -> int:
    """.5 向上取整（Python 的 round() 是银行家舍入）。"""
    return int(math.floor(value + 0.5))
