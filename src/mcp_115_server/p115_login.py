"""账号 / 手机号 + 密码登录。

这里没有直接用 p115client 的 ``login_with_password``：那个高层函数一旦碰到
「已开启两步验证」，就会 ``input("请输入短信验证码: ")`` **阻塞在终端上**，
放进 MCP 服务器里会把进程卡死。所以按它的实现拆成可以分步调用的几个函数。
"""
from __future__ import annotations

from base64 import b64decode, b64encode
from hashlib import sha1
from time import time
from typing import Any
from uuid import UUID, uuid5

from p115client import P115Client

# 115 登录相关错误码
ERRNO_BAD_ACCOUNT = 40101006    # 账号格式不对（不是邮箱/手机号/数字账号）
ERRNO_TWO_STEP = 40101010       # 已开启两步验证登录
ERRNO_BAD_PASSWORD = 40101045   # 用户名或密码错误

# 响应里出现这些字样，说明需要提交图形验证码
CAPTCHA_KEYWORDS = ("验证码", "短信验证次数超过限制")

# 登录只走**设备（app）方式**：默认登录成 115 安卓端（F1 会话）。
DEFAULT_LOGIN_APP = "android"

# 拒绝 web 方式登录的理由（见 README「登录」一节）
WEB_LOGIN_REJECTED = (
    '登录只支持设备（app）方式，不接受 app="web"：'
    "web 方式会把你自己浏览器端的登录顶掉，而且 web 不是一台「设备」。"
    '请改用设备 app（例如 app="android"）。'
)

# 由账号名推导 device_id 用的固定命名空间（UUIDv5）。
# 这个值是**接口的一部分**：一旦改了，同一个账号会被 115 当成另一台新设备。
DEVICE_ID_NAMESPACE = UUID("3f7c1a52-9d84-4c6e-b0a3-5e21d8f4c907")


def normalize_app(app: str) -> str:
    """与 p115client 内部保持一致：desktop -> web，windows/mac/linux -> os_*。"""
    if app == "desktop":
        return "web"
    if app in ("windows", "mac", "linux"):
        return "os_" + app
    return app


def resolve_login_app(app: str) -> str:
    """规整登录用的 ``app``；``web`` 方式直接拒绝，空值取默认设备。"""
    resolved = normalize_app(app.strip() or DEFAULT_LOGIN_APP)
    if resolved == "web":
        raise ValueError(WEB_LOGIN_REJECTED)
    return resolved


def derive_device_id(account: str) -> str:
    """按固定算法由账号名推出 device_id：同一个账号永远得到同一台设备。

    用 UUIDv5（名字空间 + SHA-1），输出正好是 115 安卓端 ``device_id`` 的 UUID 形式，
    所以不需要任何存储，也不需要用户提供。账号名大小写不敏感。
    """
    return str(uuid5(DEVICE_ID_NAMESPACE, account.strip().casefold()))


def encrypt_password(password: str) -> str:
    """115 要求的密码密文：``base64(RSA(sha1(password)_时间戳))``。"""
    from p115cipher.util import parse_rsa_perm, rsa_encrypt_with_pubkey

    resp = P115Client.app_publick_key()
    pub_pem = b64decode(resp["data"]["key"]).decode()
    plain = f"{sha1(password.encode('utf-8')).hexdigest()}_{int(time())}"
    cipher = rsa_encrypt_with_pubkey(plain.encode(), 512, *parse_rsa_perm(pub_pem))
    return b64encode(cipher).decode()


def submit_password(
    *,
    app: str = DEFAULT_LOGIN_APP,
    account: str,
    password: str,
    device_id: str = "",
    code: str = "",
    code_id: str = "",
) -> dict[str, Any]:
    """第一步：提交账号 + 密码（可选验证码）。"""
    return P115Client.login_login(
        {
            "account": account,
            "passwd": encrypt_password(password),
            "device_id": device_id,
            "code": code,
            "code_id": code_id,
        },
        app=normalize_app(app),
    )


def send_login_sms(user_id: int | str, *, app: str = DEFAULT_LOGIN_APP) -> dict[str, Any]:
    """两步验证：发送短信验证码。"""
    return P115Client.login_two_step_sms(user_id, app=normalize_app(app))


def submit_login_sms_code(*, account: str, code: str, app: str = DEFAULT_LOGIN_APP) -> dict[str, Any]:
    """两步验证：提交短信验证码。"""
    return P115Client.login_two_step_sms_login(
        {"account": account, "code": code},
        app=normalize_app(app),
    )


def needs_captcha(response: dict[str, Any]) -> bool:
    text = str(response.get("error") or response.get("message") or "")
    return any(keyword in text for keyword in CAPTCHA_KEYWORDS)
