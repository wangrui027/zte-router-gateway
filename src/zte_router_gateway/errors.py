"""领域错误类型。

kind 用于日志与健康检查分类。
"""

from __future__ import annotations


class ErrorKind:
    NO_CREDS = "no-creds"
    PARSE = "parse"
    EXHAUSTED = "exhausted"
    PREEMPT = "preempt"
    LOCKED = "locked"
    AUTH = "auth"
    REJECTED = "rejected"
    STALE = "stale"
    NOT_EFFECTIVE = "not_effective"
    DISABLED = "disabled"
    THROTTLE = "throttle"
    EXPIRED = "expired"
    RENEW_FAILED = "renew-failed"
    PROBE_FAILED = "probe-failed"
    TIMEOUT = "timeout"
    NETWORK = "network"
    ERROR = "error"


class RouterError(Exception):
    """路由器调用失败。kind 用于退避/日志分类。"""

    def __init__(self, message: str, kind: str = ErrorKind.ERROR) -> None:
        super().__init__(message)
        self.kind = kind
