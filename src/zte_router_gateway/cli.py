"""命令行入口：

  zte-router-gateway              # 等同于 serve
  zte-router-gateway serve
  zte-router-gateway login        # 静默 N 分钟后只登录一次（诊断）
  zte-router-gateway recover      # 低频探测直到路由器收新会话
  zte-router-gateway devices
  zte-router-gateway qos show|set|clear

网关自己持有路由器管理密码并维持会话；浏览器只使用 GW_USER / GW_PWD 访问网关。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
import time
from urllib.parse import urlsplit
from typing import Sequence

from .config import Settings
from .errors import RouterError
from .router.client import RouterClient

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zte-router-gateway",
        description="中兴 SR6110 单会话复用网关",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动网关（默认命令）")
    serve.add_argument("--host", help="监听地址，默认取 HOST")
    serve.add_argument("--port", type=int, help="监听端口，默认取 PORT")

    login = sub.add_parser("login", help="静默等待后只登录一次（诊断用）")
    login.add_argument(
        "--wait-minutes", type=float, default=30, help="登录前静默等待的分钟数"
    )

    recover = sub.add_parser(
        "recover", help="低频探测直到路由器收新会话，然后登录一次"
    )
    recover.add_argument(
        "--max-wait-minutes", type=float, default=90, help="最长等待分钟数"
    )

    devices = sub.add_parser("devices", help="打印设备列表")
    devices.add_argument(
        "--status", choices=["all", "online", "offline"], default="all"
    )

    qos = sub.add_parser("qos", help="查询 / 设置 / 取消限速（单位 Kbps）")
    qsub = qos.add_subparsers(dest="qos_command", required=True)

    show = qsub.add_parser("show", help="查看当前限速")
    show.add_argument("mac")

    setter = qsub.add_parser("set", help="设置限速")
    setter.add_argument("mac")
    setter.add_argument("--down", type=float, required=True, help="下行 Kbps")
    setter.add_argument("--up", type=float, required=True, help="上行 Kbps")

    clear = qsub.add_parser("clear", help="取消限速")
    clear.add_argument("mac")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(message)s",
    )

    if not settings.pwd:
        print(
            "未配置 ROUTER_PWD：网关需要自己持有路由器管理密码才能登录并维持会话",
            file=sys.stderr,
        )
        return 2

    command = args.command or "serve"
    if command == "serve":
        return _serve(settings, args)

    try:
        return asyncio.run(_run_command(command, settings, args))
    except RouterError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _guess_lan_ip(settings: Settings) -> str | None:
    """用一条 UDP 路由查询找出访问路由器所用的本机地址。"""
    host = urlsplit(settings.router).hostname
    if not host:
        return None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((host, 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def _serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from .gateway.app import create_app

    if getattr(args, "host", None):
        settings.host = args.host
    if getattr(args, "port", None):
        settings.port = args.port

    app = create_app(settings)

    if settings.host in ("0.0.0.0", "::"):
        ip = _guess_lan_ip(settings) or "<本机IP>"
    else:
        ip = settings.host
    print("router-gateway 已启动")
    print(f"  Web 管理:  http://{ip}:{settings.port}/")
    print(f"             账号 {settings.web_user}  密码 {settings.web_pwd}")
    if settings.web_pwd_generated:
        print("             （GW_PWD 未设置，本次密码随机生成，重启会变化）")
    print(f"  APP API :  http://{ip}:{settings.port}/api/devices")
    print(f"  APP_TOKEN: {settings.token}")
    print("  路由器凭据只来自 ROUTER_PWD；会话只存内存，掉线后网关自动重新登录")

    try:
        # log_config=None：沿用上面 basicConfig 的格式，避免 uvicorn 覆盖
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            access_log=bool(settings.debug),
            log_config=None,
        )
    except KeyboardInterrupt:
        pass
    return 0


async def _run_command(
    command: str, settings: Settings, args: argparse.Namespace
) -> int:
    client = RouterClient(settings)
    await client.open()
    try:
        if command == "login":
            return await _login(client, args.wait_minutes)
        if command == "recover":
            return await _recover(client, args.max_wait_minutes)
        if command == "devices":
            result = await client.list_devices(args.status)
            print(
                json.dumps(
                    result.model_dump(by_alias=True), ensure_ascii=False, indent=2
                )
            )
            return 0
        if command == "qos":
            return await _qos(client, args)
    finally:
        await client.close()

    print(f"未知命令: {command}", file=sys.stderr)
    return 1


async def _login(client: RouterClient, wait_minutes: float) -> int:
    if wait_minutes > 0:
        logger.info(
            "[login] 开始静默 %s 分钟（期间不要访问路由器）",
            f"{wait_minutes:g}",
        )
        await asyncio.sleep(wait_minutes * 60)

    sid = await client.session.login()
    logger.info("[login] 成功：sid=%s…", sid[:12])
    logger.info("[login] 会话只存在内存，命令结束后不保留")
    return 0


async def _recover(client: RouterClient, max_wait_minutes: float) -> int:
    deadline = time.monotonic() + max_wait_minutes * 60
    last_state: str | None = None

    while time.monotonic() < deadline:
        gate = await client.session.router_accepting_login()
        state = "free" if gate.ok else gate.reason
        if state != last_state:
            logger.info("[recover] 状态: %s", state)
            last_state = state

        if gate.ok:
            try:
                sid = await client.session.login()
            except RouterError as exc:
                logger.warning("[recover] 登录未成功: %s，5 分钟后重试", exc)
                await asyncio.sleep(5 * 60)
                continue
            logger.info("[recover] 成功拿到会话 %s…", sid[:12])
            return 0

        await asyncio.sleep(60)

    logger.error(
        "[recover] 等待超时，路由器仍未恢复。可能需要重启路由器才能清空会话表。"
    )
    return 2


async def _qos(client: RouterClient, args: argparse.Namespace) -> int:
    mac = args.mac.lower()
    if args.qos_command == "show":
        result = await client.get_qos(mac)
    elif args.qos_command == "set":
        result = await client.apply_qos(mac, args.down, args.up)
    else:
        result = await client.delete_qos(mac)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
