"""服务端配置：所有敏感值都从环境变量读，绝不写进代码或打进客户端。"""

import os
from dataclasses import dataclass
from typing import List

try:  # 本地开发用 .env；没装 python-dotenv 也能跑
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


@dataclass(frozen=True)
class Settings:
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    deepseek_timeout: float
    allowed_tokens: List[str]


def _split_env(name: str) -> List[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def get_settings() -> Settings:
    """每次调用都重新读环境变量，方便测试注入。"""
    return Settings(
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        ).rstrip("/"),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        deepseek_timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "30")),
        allowed_tokens=_split_env("ALLOWED_TOKENS"),
    )
