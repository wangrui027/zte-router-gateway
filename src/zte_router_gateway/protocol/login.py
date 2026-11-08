"""登录流程。

成功判据有两条，缺一不可：
  1) login_need_refresh === true 且 loginErrType 为空
  2) SID cookie 发生了变化（服务端把会话升级了）
只满足其一就当成成功，会拿到一个假会话，后面全是 SessionTimeout。
最后还必须用真实数据接口验货 —— 这个固件会出现「登录返回成功但会话没建起来」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import ErrorKind, RouterError
from ..transport import Response, Transport
from ..util import form_encode, is_session_timeout, now_ms, parse_json, sha256_hex
from .paths import LOGIN_PATH, TOKEN_PATH, VERIFY_PATH

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"


@dataclass(slots=True)
class LoginResult:
    sid: str
    sess_token: str | None


def _parse_object(response: Response, prefix: str) -> dict[str, Any]:
    data = parse_json(response.text)
    if not isinstance(data, dict):
        raise RouterError(f"{prefix}: {response.text[:120]}", ErrorKind.PARSE)
    return data


async def perform_login(
    transport: Transport, *, username: str, password: str | None
) -> LoginResult:
    if not password:
        raise RouterError("未配置路由器管理密码（ROUTER_PWD）", ErrorKind.NO_CREDS)

    before = transport.jar.get("SID")

    # 1) 首页：拿到基础 SID + _TESTCOOKIESUPPORT
    await transport.request("GET", "/")

    # 2) 登录 token
    token_response = await transport.request("GET", f"{TOKEN_PATH}&_={now_ms()}")
    info = _parse_object(token_response, "login token 解析失败")

    # 3) 提交
    body = form_encode(
        {
            "Username": username,
            "Password": sha256_hex(password + str(info.get("logintoken") or "")),
            "action": "login",
            "Frm_Logintoken": "",
            "captchaCode": "",
            "_sessionTOKEN": info.get("_sessionToken") or "",
        }
    )
    login_response = await transport.request(
        "POST",
        LOGIN_PATH,
        headers={"Content-Type": FORM_CONTENT_TYPE},
        body=body,
    )
    payload = _parse_object(login_response, "登录响应解析失败")

    error_type = str(payload.get("loginErrType") or "")
    if error_type == "e_exceed_max_session":
        raise RouterError(
            "路由器会话表已满（e_exceed_max_session），需等待过期或重启路由器",
            ErrorKind.EXHAUSTED,
        )
    if error_type == "e_exceed_max_user_preempt":
        raise RouterError(
            "路由器预占次数超限（e_exceed_max_user_preempt），需等待冷却",
            ErrorKind.PREEMPT,
        )
    if error_type == "e_login_locked_tips":
        raise RouterError(
            f"登录被锁定 {payload.get('lockingTime')}s（连续失败保护）", ErrorKind.LOCKED
        )
    if error_type in ("e_invalid_user_pwd", "e_invalid_user", "e_invalid_pwd"):
        raise RouterError(
            "路由器用户名或密码错误（e_invalid_user_pwd）：检查 ROUTER_USER / ROUTER_PWD；"
            "密码里有 $ 等字符时启动命令必须用单引号，避免被 shell 展开",
            ErrorKind.AUTH,
        )
    # 注意：内网 80/443 在会话池满时不会报 e_exceed_max_session，
    # 而是返回 login_need_refresh=true + loginErrMsg，看起来像成功。
    # 所以只认 login_need_refresh，后面再用真实数据接口验货。
    if payload.get("login_need_refresh") is not True:
        reason = error_type or payload.get("loginErrMsg") or "no refresh"
        raise RouterError(f"登录被拒绝: {reason}", ErrorKind.REJECTED)

    sid = transport.jar.get("SID")
    if not sid or sid == before:
        raise RouterError("登录后 SID 未更新，会话无效", ErrorKind.STALE)

    # 最关键的一步：这个固件会出现「登录返回成功但会话根本没建起来」的假成功
    # （LAN 口尤其明显），所以必须拿一次真实数据接口来验货。
    verify = await transport.request("GET", f"{VERIFY_PATH}&_={now_ms()}")
    if is_session_timeout(verify.text):
        raise RouterError(
            "登录返回成功但数据接口仍 SessionTimeout（假会话）",
            ErrorKind.NOT_EFFECTIVE,
        )

    return LoginResult(sid=sid, sess_token=payload.get("sess_token") or None)
