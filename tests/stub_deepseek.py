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

state: Dict[str, Any] = {"mode": "ok", "calls": 0}


def reset() -> None:
    state["mode"] = "ok"
    state["calls"] = 0


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


def _envelope(content: str) -> Dict[str, Any]:
    return {
        "id": "stub",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}}
        ],
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    state["calls"] += 1
    body = await request.json()
    mode = state["mode"]

    if mode == "server_error":
        return JSONResponse({"error": {"message": "upstream boom"}}, status_code=500)
    if mode == "timeout":
        await asyncio.sleep(5)

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
