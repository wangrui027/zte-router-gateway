"""会话保活：网关自己维持那个唯一会话。

  * 启动后立即尝试登录；
  * 空闲时发一次数据请求，避免路由器空闲超时；
  * 会话掉了自动重新拉起（退避逻辑在 SessionManager.renew_session 里）。
"""

from __future__ import annotations

import asyncio
import logging

from ..errors import ErrorKind, RouterError
from ..protocol.paths import VERIFY_PATH
from ..router.client import RouterClient
from ..util import now_ms

logger = logging.getLogger(__name__)


async def keeper_loop(client: RouterClient) -> None:
    settings = client.settings
    if not settings.pwd:
        logger.warning("[keeper] 未配置 ROUTER_PWD，无法登录路由器")
        return

    interval = max(1.0, settings.heartbeat_ms / 2000)  # every 是毫秒，折半后转成秒

    while True:
        session = client.session
        if not session.sid or not session.authed:
            try:
                await session.renew_session()
            except RouterError:
                # renew_session 已经打印过原因和退避时长
                pass
            except Exception as exc:  # noqa: BLE001
                logger.error("[keeper] %s", exc)
        else:
            try:
                if session.next_retry_at and now_ms() < session.next_retry_at:
                    pass  # 退避中，别去打扰路由器
                elif now_ms() - session.last_use >= settings.heartbeat_ms * 0.75:
                    # 最近没有请求，主动跑一次数据接口保活
                    await session.call("GET", f"{VERIFY_PATH}&_={now_ms()}")
            except RouterError as exc:
                # 会话失效的重新登录过程已经记录过日志，这里只补其它错误
                if exc.kind not in (ErrorKind.THROTTLE, ErrorKind.EXPIRED):
                    logger.error("[keeper] %s", exc)
            except Exception as exc:  # noqa: BLE001
                logger.error("[keeper] %s", exc)

        await asyncio.sleep(interval)
