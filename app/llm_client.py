"""封装对 DeepSeek Chat Completions 的调用。

密钥只在这里使用，且只从环境变量读——不进代码、不进客户端、不进 git。
"""

import json
from typing import Any, Dict, List, Optional

import httpx

from .config import Settings, get_settings
from .errors import UpstreamBadResponse, UpstreamError, UpstreamTimeout
from .schemas import DEFAULT_MAX_TOKENS


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


def _normalize_message(raw: Any) -> Dict[str, Any]:
    """把模型回的 assistant message 收成固定形状。

    客户端会把它原样追加进下一轮，所以要保证：
    - 字段只有 role / content / tool_calls（模型偶尔多塞的字段一律丢掉，
      否则会被当成 messages 的一部分再发回上游）；
    - tool_calls 每一项都齐活（id + function.name + function.arguments），
      客户端拿不到 id 就没法把工具结果关联回去。
    """
    if not isinstance(raw, dict):
        raise UpstreamBadResponse("模型响应里没有 message 对象")

    content = raw.get("content")
    if content is not None and not isinstance(content, str):
        raise UpstreamBadResponse("message.content 不是字符串")

    tool_calls = raw.get("tool_calls")
    normalized: Dict[str, Any] = {
        "role": str(raw.get("role") or "assistant"),
        "content": content,
    }

    if tool_calls:
        if not isinstance(tool_calls, list):
            raise UpstreamBadResponse("message.tool_calls 不是数组")
        calls = []
        for item in tool_calls:
            if not isinstance(item, dict):
                raise UpstreamBadResponse("tool_calls 的元素不是对象")
            function = item.get("function")
            if (
                not item.get("id")
                or not isinstance(function, dict)
                or not function.get("name")
            ):
                raise UpstreamBadResponse("tool_calls 缺少 id 或 function.name")
            calls.append(
                {
                    "id": str(item["id"]),
                    "type": str(item.get("type") or "function"),
                    "function": {
                        "name": str(function["name"]),
                        "arguments": str(function.get("arguments") or "{}"),
                    },
                }
            )
        normalized["tool_calls"] = calls

    if not (content or "").strip() and "tool_calls" not in normalized:
        raise UpstreamBadResponse("模型既没给出内容，也没有要求调用工具")

    return normalized


class DeepSeekClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        settings = self._settings
        if not settings.deepseek_api_key:
            raise UpstreamError("服务端未配置 DEEPSEEK_API_KEY")

        url = f"{settings.deepseek_base_url}/chat/completions"
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
            return response.json()
        except ValueError as exc:
            raise UpstreamBadResponse("模型响应不是合法 JSON") from exc

    def chat_json(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """要一段严格 JSON（行程生成用）。"""
        body = self._post(
            {
                "model": self._settings.deepseek_model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
            }
        )

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise UpstreamBadResponse("模型响应结构异常") from exc

        return extract_json_object(content)

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> tuple[Dict[str, Any], str]:
        """要一条普通的助手消息（聊天用）。返回 (message, finish_reason)。

        这里**不做工具循环**：工具（查天气）在客户端执行，循环也在客户端，
        后端只负责「拿着服务端密钥问一次」。
        """
        payload: Dict[str, Any] = {
            "model": self._settings.deepseek_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }
        if tools:
            payload["tools"] = tools

        body = self._post(payload)

        try:
            choice = body["choices"][0]
            raw_message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise UpstreamBadResponse("模型响应结构异常") from exc

        finish_reason = str(choice.get("finish_reason") or "stop")
        return _normalize_message(raw_message), finish_reason


def get_llm_client() -> DeepSeekClient:
    """FastAPI 依赖：每个请求按当前配置构造一个 client。"""
    return DeepSeekClient()
