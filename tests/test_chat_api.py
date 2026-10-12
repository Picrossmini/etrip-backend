"""POST /api/chat 的接口自动化测试。

同样走真实 HTTP（requests -> 后端 -> 假 DeepSeek），不联网、确定性。

这个接口的定位是「单轮补全透传」：客户端把整段对话（含 system 提示词、工具定义、
历史里的 tool_calls / tool 消息）发上来，后端拿着服务端密钥问一次模型，
把这一条 assistant 消息原样还回去。所以测试的重点有两块：
1. 透传是否忠实——发上去的东西有没有被丢字段（尤其 tool_calls 的 id，丢了客户端就接不上工具结果）；
2. 挡住不该花 token 的请求——空消息、超长、以 assistant 结尾这类一律在调模型之前打回。
"""

import pytest


def _messages(**overrides):
    body = {"messages": [{"role": "user", "content": "成都有什么好玩的？"}]}
    body.update(overrides)
    return body


def _weather_tool():
    """客户端定义、客户端执行的工具（后端不解释，只转给模型）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "查询某城市当前天气",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]


def _chat(api, body, **kwargs):
    return api.post(body, path="/api/chat", **kwargs)


# --- 正常流 ---------------------------------------------------------------


def test_chat_ok_returns_assistant_message(api):
    api.set_mode("chat_ok")
    response = _chat(api, _messages())
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["request_id"]
    assert data["message"]["role"] == "assistant"
    assert data["message"]["content"] == "成都很好玩，建议先去宽窄巷子。"
    assert data["finish_reason"] == "stop"


def test_system_and_history_are_sent_upstream_unchanged(api):
    """后端不能自作主张改 messages：system 提示词和历史必须原样上去。"""
    api.set_mode("chat_ok")
    messages = [
        {"role": "system", "content": "你是 Etrip 的旅行助手。"},
        {"role": "user", "content": "成都有什么好玩的？"},
        {"role": "assistant", "content": "宽窄巷子、熊猫基地。"},
        {"role": "user", "content": "那吃的呢？"},
    ]
    response = _chat(api, _messages(messages=messages))
    assert response.status_code == 200, response.text

    sent = api.stub.state["last_body"]["messages"]
    assert [item["role"] for item in sent] == ["system", "user", "assistant", "user"]
    assert sent == messages


def test_tools_are_passed_through(api):
    api.set_mode("chat_ok")
    tools = _weather_tool()
    response = _chat(api, _messages(tools=tools))
    assert response.status_code == 200, response.text
    assert api.stub.state["last_body"]["tools"] == tools


def test_tools_omitted_when_client_sends_none(api):
    api.set_mode("chat_ok")
    response = _chat(api, _messages())
    assert response.status_code == 200, response.text
    assert "tools" not in api.stub.state["last_body"]


def test_tool_calls_are_returned_with_ids(api):
    """模型要求调工具时，tool_calls 和其中的 id 必须整只回给客户端。"""
    api.set_mode("tool_call")
    response = _chat(api, _messages(tools=_weather_tool()))
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["finish_reason"] == "tool_calls"
    assert data["message"]["content"] is None

    calls = data["message"]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["id"] == "call_weather_1"
    assert calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "get_weather"
    assert calls[0]["function"]["arguments"] == '{"city": "成都"}'


def test_tool_result_round_trip(api):
    """客户端执行完工具后把结果发回来（role=tool + tool_call_id），要能回溯上去。"""
    api.set_mode("chat_ok")
    messages = [
        {"role": "user", "content": "成都没今天天气怎么样？"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_weather_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "成都"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_weather_1",
            "content": '{"temperature": "22", "weather": "多云"}',
        },
    ]
    response = _chat(api, _messages(messages=messages))
    assert response.status_code == 200, response.text

    sent = api.stub.state["last_body"]["messages"]
    assert [item["role"] for item in sent] == ["user", "assistant", "tool"]
    assert sent[1]["tool_calls"][0]["id"] == "call_weather_1"
    assert sent[2]["tool_call_id"] == "call_weather_1"


def test_max_tokens_forwarded(api):
    api.set_mode("chat_ok")
    response = _chat(api, _messages(max_tokens=1234))
    assert response.status_code == 200, response.text
    assert api.stub.state["last_body"]["max_tokens"] == 1234


def test_default_max_tokens_applied(api):
    api.set_mode("chat_ok")
    response = _chat(api, _messages())
    assert response.status_code == 200, response.text
    assert api.stub.state["last_body"]["max_tokens"] == 600


def test_idempotency_key_is_ignored(api):
    """聊天刻意不做幂等：同一条消息再问一次本来就该重新回答。"""
    api.set_mode("chat_ok")
    first = _chat(api, _messages(), idempotency_key="same-key")
    second = _chat(api, _messages(), idempotency_key="same-key")

    assert first.status_code == 200 and second.status_code == 200
    assert "from_cache" not in second.json()
    assert second.headers.get("X-Idempotent-Replay") is None
    assert api.upstream_calls == 2, "两次提交都应该真的问过模型"


# --- 鉴权 -----------------------------------------------------------------


def test_missing_token_is_401(api):
    response = _chat(api, _messages(), token=None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_bad_token_is_401(api):
    response = _chat(api, _messages(), token="nope")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert api.upstream_calls == 0


# --- schema 错（422）------------------------------------------------------


def test_missing_messages_is_422(api):
    response = _chat(api, {})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schema_error"


def test_unknown_role_is_422(api):
    response = _chat(api, _messages(messages=[{"role": "robot", "content": "hi"}]))
    assert response.status_code == 422
    assert api.upstream_calls == 0


@pytest.mark.parametrize("value", [0, -1, 2001])
def test_max_tokens_out_of_range_is_422(api, value):
    response = _chat(api, _messages(max_tokens=value))
    assert response.status_code == 422
    assert api.upstream_calls == 0


# --- 业务规则错（400）：一个 token 都不该花 ---------------------------------


def test_empty_messages_is_400(api):
    response = _chat(api, _messages(messages=[]))
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert any("不能为空" in item for item in error["details"])
    assert api.upstream_calls == 0


def test_too_many_messages_is_400(api):
    messages = [{"role": "user", "content": "hi"} for _ in range(51)]
    response = _chat(api, _messages(messages=messages))
    assert response.status_code == 400
    assert any("最多 50 条" in item for item in response.json()["error"]["details"])
    assert api.upstream_calls == 0


def test_single_message_too_long_is_400(api):
    messages = [{"role": "user", "content": "啊" * 4001}]
    response = _chat(api, _messages(messages=messages))
    assert response.status_code == 400
    assert any("过长" in item for item in response.json()["error"]["details"])
    assert api.upstream_calls == 0


def test_total_length_too_long_is_400(api):
    # 每条 4000（没超单条上限），6 条一共 24000，超总上限 20000。
    messages = [{"role": "user", "content": "啊" * 4000} for _ in range(6)]
    response = _chat(api, _messages(messages=messages))
    assert response.status_code == 400
    assert any("总长度" in item for item in response.json()["error"]["details"])
    assert api.upstream_calls == 0


def test_last_message_assistant_is_400(api):
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
    ]
    response = _chat(api, _messages(messages=messages))
    assert response.status_code == 400
    assert any("最后一条" in item for item in response.json()["error"]["details"])
    assert api.upstream_calls == 0


# --- 上游异常 -------------------------------------------------------------


def test_upstream_500_is_502(api):
    api.set_mode("server_error")
    response = _chat(api, _messages())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


def test_upstream_timeout_is_504(api):
    api.set_mode("timeout")
    response = _chat(api, _messages())
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"


def test_bad_tool_call_is_502(api):
    """tool_calls 缺 id：客户端没法把工具结果接回去，宁可 502 也别放行。"""
    api.set_mode("bad_tool_call")
    response = _chat(api, _messages(tools=_weather_tool()))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_bad_response"


def test_empty_model_output_is_502(api):
    api.set_mode("no_content")
    response = _chat(api, _messages())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_bad_response"


def test_no_choices_is_502(api):
    api.set_mode("no_choices")
    response = _chat(api, _messages())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_bad_response"
