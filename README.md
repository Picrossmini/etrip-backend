# Etrip Backend

Etrip 旅行规划 App 的后端：把「调大模型」这件事从客户端搬到服务端。

Flutter 客户端不再直接持有 DeepSeek 密钥，而是请求本服务；密钥只存在于服务端的环境变量里。目前提供两个接口：`POST /api/itinerary`（生成结构化行程）和 `POST /api/chat`（聊天机器人的单轮补全）。同一份代码里，顺带把这两个接口的**接口自动化测试**也做掉了。

## 为什么要这一层

改造前是 App 直接调 DeepSeek，带来两个问题：

1. **密钥被打包进客户端** —— 任何拿到 APK 的人都能提取出来。密钥一旦进过 git 历史或被人反编译，就只能作废重发。
2. **没有自己的服务端接口** —— 也就没有可以写接口自动化测试的被测系统。

现在的架构：

```
Flutter App  ──HTTP──▶  Etrip Backend  ──▶  DeepSeek API
                          │
                          ├─ 密钥只在服务端（环境变量，绝不入库）
                          ├─ 业务规则校验 + 模型输出的结构校验
                          ├─ 幂等（同一请求不重复调用模型；仅行程生成）
                          └─ 聊天的工具循环留在客户端（天气 API 的密钥也不必上服务端）
```

## 快速开始

```bash
# 1. 建虚拟环境并装依赖
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env：填上真实的 DEEPSEEK_API_KEY，以及允许访问本服务的 ALLOWED_TOKENS

# 3. 启动
uvicorn app.main:app --reload --port 8000
```

`.env` 已在 `.gitignore` 里，**绝不要提交**。

## 接口

### `GET /health`

存活探测。

```json
{ "status": "ok" }
```

### `POST /api/itinerary`

按用户偏好生成一份结构化行程。

**请求头**

| 头 | 必填 | 说明 |
|---|---|---|
| `Authorization` | 是 | `Bearer <ALLOWED_TOKENS 里的任意一个>`；`ALLOWED_TOKENS` 留空则关闭鉴权（仅供本地自测） |
| `Idempotency-Key` | 否 | 带上即启用幂等：同一 key 重复提交会回放首次结果，不二次调用模型 |

**请求体**

| 字段 | 类型 | 约束 |
|---|---|---|
| `user_id` | string | 1~64 字符 |
| `city` | string | 1~64 字符 |
| `tourism_types` | string[] | 不能为空（至少选一个偏好） |
| `days` | int | 1~30 |
| `no_of_people` | int | 1~20，默认 1 |
| `start_date` | date | 可选，`YYYY-MM-DD` |
| `end_date` | date | 可选，不早于 `start_date`，且与 `days` 的天数一致 |
| `catalog` | object[] | 可选，最多 200 条，见下 |

**`catalog`：候选地点目录。** 客户端把本地景点目录的精简版发过来，后端把它写进提示词、要求模型**只能从中挑 `place_id`**：

```json
"catalog": [
  { "place_id": "1", "name": "Great Wall", "city_name": "Beijing", "tourism_type": "历史古迹" }
]
```

只有 `place_id` 是必填的。只放「挑景点时用得上」的四个字段，不放图片、简介那种大文本——目录每次请求都要随提示词发给模型，越短越省钱；客户端拿回 `place_id` 后在本地补全成完整对象。

**不带 `catalog` 时行为完全不变**（模型自由发挥），老客户端不受影响。另外后端**不会**去核对模型返回的 `place_id` 是否真在目录里——编造的 id 由客户端丢弃并兜底，免得模型偶尔不听话就让整个请求失败。

```bash
curl -X POST http://localhost:8000/api/itinerary \
  -H "Authorization: Bearer dev-token-1" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-001" \
  -d '{"user_id":"u-001","city":"成都","tourism_types":["自然风光","美食"],"days":3,"no_of_people":2}'
```

**响应**

```json
{
  "request_id": "7c1e9b1a-...",
  "description": "成都 3 天，主打自然风光与美食……",
  "from_cache": false,
  "days": [
    {
      "day": 1,
      "places": [{ "place_id": "p-101", "name": "青城山" }],
      "summary": "……"
    }
  ]
}
```

带 `Idempotency-Key` 重复提交时，`from_cache` 为 `true`，并附响应头 `X-Idempotent-Replay: true`。

### `POST /api/chat`

聊天机器人的**单轮补全**：客户端把整段对话发上来，后端拿着服务端密钥问一次模型，把模型回的**那一条**助手消息原样还回去。

**请求头**

| 头 | 必填 | 说明 |
|---|---|---|
| `Authorization` | 是 | 同 `/api/itinerary` |

刻意**不支持 `Idempotency-Key`**：聊天是一问一答地推进，同一条消息再问一次本来就该重新回答，回放上一轮的旧答案反而奇怪。

**请求体**

| 字段 | 类型 | 约束 |
|---|---|---|
| `messages` | object[] | 1~50 条，且最后一条必须是 `user` 或 `tool`（以 `assistant` 结尾时没有要回答的问题） |
| `tools` | object[] | 可选，OpenAI 风格的工具定义；后端**不解释**，只转给模型 |
| `max_tokens` | int | 1~2000，默认 600 |

每条 message 的形状跟 OpenAI / DeepSeek 对齐：

```json
{ "role": "user", "content": "今天成都能去青城山吗" }
```

`role` 取 `system` / `user` / `assistant` / `tool`。两个只在特定角色出现的字段：`tool_calls`（仅 `assistant`，模型要求客户端去执行的工具调用）和 `tool_call_id`（仅 `tool`，指回是哪一次调用的结果）。

还有两条上限，目的都是把「一次能花掉多少 token」框住，免得拿到 token 的客户端把服务端当免费模型代理刷：单条 `content` 不超过 4000 字符，整段 `messages` 的 `content` 合计不超过 20000 字符。

**响应**

```json
{
  "request_id": "b0f3…",
  "message": {
    "role": "assistant",
    "content": null,
    "tool_calls": [
      { "id": "call_abc", "type": "function",
        "function": { "name": "get_weather", "arguments": "{\"city\": \"Chengdu\"}" } }
    ]
  },
  "finish_reason": "tool_calls"
}
```

`message` 已经被后端收成固定形状（只留 `role` / `content` / `tool_calls`），客户端可以**原样**追加进下一轮的 `messages`；模型偶尔多塞的字段（如 `reasoning_content`）会被丢掉。`tool_calls[].id` 一定会保留——客户端要靠它把工具结果接回去。

**工具循环在客户端。** 天气工具的定义由客户端提供、也在客户端执行，所以后端只负责「问一次、把消息还回去」，不自己做循环：

```
客户端 ──messages + tools──▶ 后端 ──▶ 模型
客户端 ◀── assistant.tool_calls ── 后端
客户端：执行工具（查天气）
客户端 ──messages + { role: tool, tool_call_id } ──▶ 后端 ──▶ 模型   ← 下一轮
```

这样天气 API 的密钥不必再往服务端搬一份，后端也不必理解任何一个具体工具。

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer dev-token-1" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"成都有什么好玩的？"}],"max_tokens":600}'
```

## 错误码

所有错误统一成同一个结构：

```json
{ "error": { "code": "invalid_request", "message": "……", "details": [] } }
```

| HTTP | code | 含义 |
|---|---|---|
| 422 | `schema_error` | 请求体形状不对（缺字段、类型错） |
| 400 | `invalid_request` | 形状对，但违反业务规则（天数越界、日期倒置等） |
| 401 | `unauthorized` | 缺失或无效的 Bearer token |
| 409 | `conflict` | 相同 `Idempotency-Key` 的请求正在处理中 |
| 502 | `upstream_error` | 调模型失败（网络错误 / 非 200） |
| 502 | `upstream_bad_response` | 模型通了，但返回内容无法按约定结构解析 |
| 504 | `upstream_timeout` | 调模型超时 |

**422 和 400 的分工是刻意的**：请求体的「形状」问题归 Pydantic（422），「业务规则」归 `service.validate_business_rules`（400）。好处是业务规则不过时**根本不会浪费一次模型调用**——有一条测试专门守这个（`test_business_error_happens_before_calling_model`）。

聊天接口共用同一套错误码（它不做幂等，所以用不到 409）；`invalid_request` 里换成了聊天专属的几条规则：`messages` 为空、超过 50 条、单条或总长超限、最后一条是 `assistant`。这些同样在调模型之前就挡掉，测试里逐条断言了「上游一次都没被调用」。

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```

```
58 passed in ~8s
```

这些是**接口自动化**，不是单元测试：`requests` 打真实 HTTP，请求打到真跑起来的后端，后端再打到本地一个**假的 DeepSeek 服务**。所以整组测试不联网、秒级、结果确定性，覆盖：

**行程生成（32 条）**

- 正常流（含模型返回被 ```json 围栏包裹的脏响应）
- 鉴权（缺 token / 错 token）
- schema 错（缺字段 / 类型错 / 必填字符串为空）
- 业务规则错（空偏好、天数越界、人数越界、日期倒置、日期区间与天数不一致）
- 边界值（`days` 取 0 / -1 / 31 / 999；`no_of_people` 取 0 / 21）
- 上游异常（非法 JSON、空响应、缺少 days、天数不符、上游 500、超时）
- 幂等（同 key 回放且不二次调用模型；不同 key 各自生成）
- 候选地点目录（目录进得了提示词、超 200 条被挡在 422、带不带目录互不影响、校验顺序不变）

**聊天（26 条）**

- 正常流：普通回复、`system` 提示词与历史原样上去、`max_tokens` 透传与默认值
- 工具：`tools` 定义透传（客户端不发时字段整个不出现）、`tool_calls` 连同 `id` 原样回来、`role=tool` + `tool_call_id` 能回溯上去
- 字段收窄：模型多塞的 `reasoning_content` / `index` 被丢掉；`tool_calls` 缺 `id` 直接 502
- 鉴权、schema 错（缺 `messages` / 非法 `role` / `max_tokens` 越界）
- 业务规则错（空 `messages`、超 50 条、单条超长、总长超限、以 `assistant` 结尾）——每条都断言上游调用次数为 0
- 上游异常（500 / 超时 / 空内容 / 无 choices）
- 幂等刻意不做：同一个 `Idempotency-Key` 提交两次，两次都真的问过模型

有两处断言是靠「假上游会记下它收到的请求体」做到的，直接看不经响应的那一层：**行程**里断言发给模型的提示词确实出现了目录中的 `place_id`；**聊天**里断言最后一轮中 `assistant.tool_calls[0].id` 与 `role=tool` 的 `tool_call_id` 配得上（少一个，真实 DeepSeek 就会直接报错）。

假 DeepSeek 服务在 `tests/stub_deepseek.py`，按 `mode` 返回不同响应，测试里用 `api.set_mode(...)` 切换（`chat_tool_round` 会先要工具、拿到结果后再给回答，专门用来跑通完整的一轮工具调用）。

## 设计要点

- **密钥只在服务端**：`app/config.py` 里所有敏感值只从环境变量读；代码、客户端、git 里都不出现。
- **两层校验分开**：Pydantic 管类型/形状（422），`service` 管业务规则（400）。
- **不信任模型输出**：`extract_json_object` 先剥掉 ``` 围栏、再按括号配对抠出第一个 JSON 对象；`service._build_days` 逐天校验——天数、每天景点数、字段完整性任一对不上就报 `upstream_bad_response`，不让脏数据流到客户端。
- **幂等**：`Idempotency-Key` 复用的是支付回调里常见的那套思路；重复请求回放首次结果，in-flight 的重复请求直接 409。演示用内存实现，重启即清空。
- **目录进提示词，而不是进模型自由发挥**：模型编不出真实 `place_id`，所以让客户端把候选目录带上来，把「自由发挥」收窄成「从目录里挑」。代价是提示词变长（36 条目录约几百 token），换来的是返回的 id 一定能对回客户端里带图片和简介的景点。
- **聊天只做「单轮透传」，编排留在客户端**：后端不理解任何一个具体工具，`tools` 进来什么样、出去什么样。这样天气 API 的密钥不用再往服务端搬一份，后端也不必为每个客户端工具写适配；代价是取一次回答要往返两趟（要工具一趟、带结果再一趟），换来的是两边的密钥各自关在自己的门里。
- **上限就是防线**：聊天请求的条数、单条长度、总长度、`max_tokens` 都有硬上限，且都在调模型之前校验。一个拿到 token 的客户端最多也只能花掉这么多——接口测试里逐条断言了「被挡下时上游一次都没被调用」。

## 相关

Flutter 客户端在 [Picrossmini/Etrip](https://github.com/Picrossmini/Etrip)。客户端的**行程生成**（`BackendTravelPlanGenerator`）和**聊天机器人**（`BackendChatCompleter`）都已经切到本服务，DeepSeek 密钥不再出现在客户端里；客户端只留一个访问令牌，配置项是 `backendBaseUrl` / `backendToken`。

聊天那条链路上，工具循环留在客户端（`ChatApiService`）：它拼系统提示词（本 App 的景点目录、用户收藏与偏好都在客户端，不必上服务端）、发消息、执行工具、再发一轮。测试方面，客户端侧另有 `backend_chat_completer_test.dart` 与 `chat_api_service_test.dart` 覆盖传输与循环，以及一次真客户端 → 真后端 → 假上游的端到端联调。
