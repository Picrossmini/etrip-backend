"""统一异常类型。每个异常自带 HTTP 状态码与稳定的错误码，便于接口测试断言。"""

from typing import Any, List, Optional


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "", details: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message or self.code
        self.details = details


class InvalidRequestError(AppError):
    """请求体格式对，但违反业务规则（如天数超界、日期倒置）。"""

    status_code = 400
    code = "invalid_request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class UpstreamError(AppError):
    """调用 DeepSeek 失败（网络错误 / 非 200）。"""

    status_code = 502
    code = "upstream_error"


class UpstreamBadResponse(AppError):
    """模型通了，但返回内容无法按约定结构解析。"""

    status_code = 502
    code = "upstream_bad_response"


class UpstreamTimeout(AppError):
    status_code = 504
    code = "upstream_timeout"
