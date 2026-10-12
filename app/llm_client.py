"""封装对 DeepSeek Chat Completions 的调用。

密钥只在这里使用，且只从环境变量读——不进代码、不进客户端、不进 git。
"""

import json
from typing import Any, Dict, List

import httpx

from .config import Settings, get_settings
from .errors import UpstreamBadResponse, UpstreamError, UpstreamTimeout


def extract_json_object(text: str) -> Dict[str, Any]:
    """从模型返回里抠出第一个完整的 JSON 对象。

    大模型经常把 JSON 包在 ```json ... ``` 里，或在前后加一段自然语言，
    所以不能直接 json.loads，要先剥壳、再按括号配对找出对象。
    """
    if not text or not text.strip():
        raise UpstreamBadResponse("模型返回为空")

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped[3:]
        if stripped[:4].lower() == "json":
            stripped = stripped[4:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        stripped = stripped.strip()

    start = stripped.find("{")
    if start == -1:
        raise UpstreamBadResponse("模型返回里找不到 JSON 对象")

    depth = 0
    for index in range(start, len(stripped)):
        char = stripped[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = stripped[start : index + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise UpstreamBadResponse(f"模型返回不是合法 JSON：{exc}") from exc

    raise UpstreamBadResponse("模型返回的 JSON 括号不闭合")


class DeepSeekClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def chat_json(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        settings = self._settings
        if not settings.deepseek_api_key:
            raise UpstreamError("服务端未配置 DEEPSEEK_API_KEY")

        url = f"{settings.deepseek_base_url}/chat/completions"
        payload = {
            "model": settings.deepseek_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
        }
        headers = {
            "Authorization": f"Bearer {settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=settings.deepseek_timeout) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout(f"调用模型超时（>{settings.deepseek_timeout}s）") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"调用模型失败：{exc}") from exc

        if response.status_code != 200:
            raise UpstreamError(f"模型返回 HTTP {response.status_code}")

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise UpstreamBadResponse("模型响应结构异常") from exc

        return extract_json_object(content)


def get_llm_client() -> DeepSeekClient:
    """FastAPI 依赖：每个请求按当前配置构造一个 client。"""
    return DeepSeekClient()
