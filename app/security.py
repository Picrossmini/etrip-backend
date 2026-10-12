"""鉴权：一个朴素但够用的 Bearer token 校验（够接口测试演示用）。"""

from fastapi import Header

from .config import get_settings
from .errors import UnauthorizedError


def require_token(authorization: str = Header(default="")) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthorizedError("缺少 Bearer token")

    token = token.strip()
    allowed = get_settings().allowed_tokens
    # 未配置 ALLOWED_TOKENS 时视为本地调试、放行；一旦配置就必须匹配。
    if allowed and token not in allowed:
        raise UnauthorizedError("token 无效")

    return token
