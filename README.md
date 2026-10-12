# Etrip Backend

Etrip 旅行规划 App 的后端：把「调大模型生成行程」这件事从客户端搬到服务端。

Flutter 客户端不再直接持有 DeepSeek 密钥，而是请求本服务；密钥只存在于服务端的环境变量里。同一份代码里，顺带把行程生成的**接口自动化测试**也做掉了。

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
                          └─ 幂等（同一请求不重复调用模型）
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

## 测试

```bash
pip install -r requirements-dev.txt
pytest
```

```
26 passed in ~6s
```

这些是**接口自动化**，不是单元测试：`requests` 打真实 HTTP，请求打到真跑起来的后端，后端再打到本地一个**假的 DeepSeek 服务**。所以整组测试不联网、秒级、结果确定性，覆盖：

- 正常流（含模型返回被 ```json 围栏包裹的脏响应）
- 鉴权（缺 token / 错 token）
- schema 错（缺字段 / 类型错 / 必填字符串为空）
- 业务规则错（空偏好、天数越界、人数越界、日期倒置、日期区间与天数不一致）
- 边界值（`days` 取 0 / -1 / 31 / 999；`no_of_people` 取 0 / 21）
- 上游异常（非法 JSON、空响应、缺少 days、天数不符、上游 500、超时）
- 幂等（同 key 回放且不二次调用模型；不同 key 各自生成）

假 DeepSeek 服务在 `tests/stub_deepseek.py`，按 `mode` 返回不同响应，测试里用 `api.set_mode(...)` 切换。

## 设计要点

- **密钥只在服务端**：`app/config.py` 里所有敏感值只从环境变量读；代码、客户端、git 里都不出现。
- **两层校验分开**：Pydantic 管类型/形状（422），`service` 管业务规则（400）。
- **不信任模型输出**：`extract_json_object` 先剥掉 ``` 围栏、再按括号配对抠出第一个 JSON 对象；`service._build_days` 逐天校验——天数、每天景点数、字段完整性任一对不上就报 `upstream_bad_response`，不让脏数据流到客户端。
- **幂等**：`Idempotency-Key` 复用的是支付回调里常见的那套思路；重复请求回放首次结果，in-flight 的重复请求直接 409。演示用内存实现，重启即清空。

## 相关

Flutter 客户端在 [Picrossmini/Etrip](https://github.com/Picrossmini/Etrip)。客户端目前仍直连 DeepSeek，切到本服务的 `/api/itinerary` 是下一步计划。
