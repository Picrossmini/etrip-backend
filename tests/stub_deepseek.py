"""一个假的 DeepSeek 服务，只在接口测试里用。

它冒充 DeepSeek 的 /v1/chat/completions，按当前 mode 返回不同的响应，
这样接口测试既不联网、不花钱，也完全确定。
"""

import asyncio
import json
import re
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

state: Dict[str, Any] = {"mode": "ok", "calls": 0, "last_body": None}


def reset() -> None:
    state["mode"] = "ok"
    state["calls"] = 0
    state["last_body"] = None


def last_prompt() -> str:
    """上游最后一次收到的完整提示词（system + user 拼在一起）。

    用来断言「后端到底把什么发给了模型」——比如候选地点目录有没有进提示词。
    """
    body = state.get("last_body") or {}
    messages = body.get("messages") or []
    return "\n".join(
        str(message.get("content", ""))
        for message in messages
        if isinstance(message, dict)
    )


def _requested_days(body: Dict[str, Any]) -> int:
    texts = [
        str(message.get("content", ""))
        for message in body.get("messages", [])
        if isinstance(message, dict)
    ]
    match = re.search(r"规划\s*(\d+)\s*天", " ".join(texts))
    return int(match.group(1)) if match else 3


def _plan(days: int) -> Dict[str, Any]:
    return {
        "description": "成都行程示例",
        "days": [
            {
                "day": i,
                "summary": f"第 {i} 天安排",
                "places": [{"place_id": f"p{i}", "name": f"景点{i}"}],
            }
            for i in range(1, days + 1)
        ],
    }


def _message_envelope(message: Dict[str, Any], finish_reason: str = "stop") -> Dict[str, Any]:
    """把一条 assistant 消息包成 Chat Completions 的响应外壳。"""
    return {
        "id": "stub",
        "choices": [
            {"index": 0, "message": message, "finish_reason": finish_reason}
        ],
    }


def _envelope(content: str, finish_reason: str = "stop") -> Dict[str, Any]:
    return _message_envelope(
        {"role": "assistant", "content": content}, finish_reason
    )


def _has_tool_result(body: Dict[str, Any]) -> bool:
    """这次请求里是否已经带回了工具结果（role=tool）。"""
    return any(
        isinstance(message, dict) and message.get("role") == "tool"
        for message in body.get("messages", [])
    )


# 聊天专用 mode：返回的是一条普通的助手消息（可能带 tool_calls），
# 而不是行程那种 JSON 文本。
_CHAT_MODES: Dict[str, Dict[str, Any]] = {
    "chat_ok": {
        "message": {"role": "assistant", "content": "成都很好玩，建议先去宽窄巷子。"},
        "finish_reason": "stop",
    },
    "tool_call": {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_weather_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": '{"city": "成都"}',
                    },
                }
            ],
        },
        "finish_reason": "tool_calls",
    },
    # 缺 id 的 tool_call：客户端拿不到 id 就没法把工具结果接回去，后端该拦下。
    "bad_tool_call": {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"type": "function", "function": {"name": "get_weather"}}
            ],
        },
        "finish_reason": "tool_calls",
    },
    # 既没内容也不要调工具：没什么可回给客户端的，算上游响应不合格。
    "no_content": {
        "message": {"role": "assistant", "content": ""},
        "finish_reason": "stop",
    },
}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/__mode/{mode}")
def set_mode(mode: str):
    """给测试用的控制口：把假上游切到某个 mode。

    进程内的用例直接改 `state`，但端到端联调（比如真客户端打真后端）里
    假上游是另一个进程，只能这样从外面切。
    """
    state["mode"] = mode
    return {"mode": mode, "calls": state["calls"]}


@app.get("/__last")
def last_request():
    """给测试用的控制口：假上游最后一次收到的请求体。"""
    return {"calls": state["calls"], "body": state["last_body"]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    state["calls"] += 1
    body = await request.json()
    state["last_body"] = body
    mode = state["mode"]

    if mode == "server_error":
        return JSONResponse({"error": {"message": "upstream boom"}}, status_code=500)
    if mode == "timeout":
        await asyncio.sleep(5)
    if mode == "no_choices":
        return JSONResponse({"id": "stub", "choices": []})
    if mode == "chat_tool_round":
        # 模拟一轮完整的工具调用：先要工具，拿到结果后再给最终回答。
        # 客户端的两轮补全因此可以对着一个真的假上游跑通。
        if _has_tool_result(body):
            spec = _CHAT_MODES["chat_ok"]
        else:
            spec = _CHAT_MODES["tool_call"]
        return JSONResponse(
            _message_envelope(spec["message"], spec["finish_reason"])
        )
    if mode in _CHAT_MODES:
        spec = _CHAT_MODES[mode]
        return JSONResponse(
            _message_envelope(spec["message"], spec["finish_reason"])
        )

    if mode == "empty":
        content = ""
    elif mode == "dirty":
        content = (
            "好的，这是你的行程：\n```json\n"
            + json.dumps(_plan(_requested_days(body)), ensure_ascii=False)
            + "\n```\n希望有帮助！"
        )
    elif mode == "bad_json":
        content = "抱歉，我暂时无法生成行程。"
    elif mode == "missing_days":
        content = json.dumps({"description": "只有描述，没有 days"}, ensure_ascii=False)
    elif mode == "wrong_count":
        content = json.dumps(_plan(1), ensure_ascii=False)
    else:  # ok
        content = json.dumps(_plan(_requested_days(body)), ensure_ascii=False)

    return JSONResponse(_envelope(content))
