"""业务编排：先校验业务规则，再调模型，再对模型输出做结构校验。"""

import uuid
from typing import Any, Dict, List

from .errors import InvalidRequestError, UpstreamBadResponse
from .llm_client import DeepSeekClient
from .prompt import build_messages
from .schemas import (
    MAX_CHAT_CHARS,
    MAX_CHAT_MESSAGES,
    MAX_DAYS,
    MAX_MESSAGE_CHARS,
    MAX_PEOPLE,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    DayPlan,
    ItineraryRequest,
    ItineraryResponse,
    PlaceRef,
)


def validate_business_rules(request: ItineraryRequest) -> None:
    """类型校验已由 Pydantic 完成，这里只管业务规则，违反则 400。"""
    problems: List[str] = []

    if not request.tourism_types:
        problems.append("tourism_types 不能为空，请至少选择一个偏好")
    if not 1 <= request.days <= MAX_DAYS:
        problems.append(f"days 必须在 1..{MAX_DAYS} 之间，收到 {request.days}")
    if not 1 <= request.no_of_people <= MAX_PEOPLE:
        problems.append(
            f"no_of_people 必须在 1..{MAX_PEOPLE} 之间，收到 {request.no_of_people}"
        )

    if request.start_date and request.end_date:
        if request.end_date < request.start_date:
            problems.append("end_date 不能早于 start_date")
        else:
            span = (request.end_date - request.start_date).days + 1
            if span != request.days:
                problems.append(f"日期区间共 {span} 天，与 days={request.days} 不一致")

    if problems:
        raise InvalidRequestError("；".join(problems), details=problems)


def _build_days(payload: Dict[str, Any], request: ItineraryRequest) -> List[DayPlan]:
    raw_days = payload.get("days")
    if not isinstance(raw_days, list) or len(raw_days) != request.days:
        actual = len(raw_days) if isinstance(raw_days, list) else "非数组"
        raise UpstreamBadResponse(
            f"生成结果的 days 数量与请求不一致（期望 {request.days}，实际 {actual}）"
        )

    days: List[DayPlan] = []
    for index, raw_day in enumerate(raw_days, start=1):
        if not isinstance(raw_day, dict):
            raise UpstreamBadResponse(f"第 {index} 天不是对象")

        raw_places = raw_day.get("places")
        if not isinstance(raw_places, list) or not raw_places:
            raise UpstreamBadResponse(f"第 {index} 天没有景点")

        places: List[PlaceRef] = []
        for item in raw_places:
            if not isinstance(item, dict) or not item.get("place_id") or not item.get("name"):
                raise UpstreamBadResponse(f"第 {index} 天的景点缺少 place_id 或 name")
            places.append(
                PlaceRef(place_id=str(item["place_id"]), name=str(item["name"]))
            )

        days.append(
            DayPlan(day=index, places=places, summary=str(raw_day.get("summary") or ""))
        )

    return days


def generate_plan(
    request: ItineraryRequest, client: DeepSeekClient
) -> ItineraryResponse:
    validate_business_rules(request)

    payload = client.chat_json(build_messages(request))

    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        raise UpstreamBadResponse("生成结果缺少 description 字段")

    days = _build_days(payload, request)
    return ItineraryResponse(
        request_id=str(uuid.uuid4()), description=description, days=days
    )


# --- 聊天 -----------------------------------------------------------------


def validate_chat_rules(request: ChatRequest) -> None:
    """聊天请求的业务规则（形状问题已由 Pydantic 挡掉，这里一律 400）。

    重点是把「一次能花掉多少 token」框住：条数、单条长度、总长度都设上限，
    免得一个拿到了 token 的客户端把服务端当成免费的模型代理随便刷。
    """
    problems: List[str] = []

    if not request.messages:
        problems.append("messages 不能为空")
    elif len(request.messages) > MAX_CHAT_MESSAGES:
        problems.append(
            f"messages 最多 {MAX_CHAT_MESSAGES} 条，收到 {len(request.messages)}"
        )

    for index, message in enumerate(request.messages, start=1):
        if len(message.content or "") > MAX_MESSAGE_CHARS:
            problems.append(f"第 {index} 条消息过长（上限 {MAX_MESSAGE_CHARS} 字符）")

    total = sum(len(message.content or "") for message in request.messages)
    if total > MAX_CHAT_CHARS:
        problems.append(f"messages 总长度 {total} 超过上限 {MAX_CHAT_CHARS}")

    if request.messages and request.messages[-1].role not in ("user", "tool"):
        problems.append(
            "messages 最后一条必须是 user 或 tool：以 assistant 结尾时没有要回答的问题"
        )

    if problems:
        raise InvalidRequestError("；".join(problems), details=problems)


def chat(request: ChatRequest, client: DeepSeekClient) -> ChatResponse:
    """问模型一次，返回它这一条助手消息。

    刻意**不做工具循环**：工具（查天气）在客户端执行，循环也在客户端。
    后端只负责「拿着服务端密钥问一次、把消息原样还回去」，
    这样天气 API 的密钥不必再往服务端搬一份。
    """
    validate_chat_rules(request)

    message, finish_reason = client.chat_completion(
        [m.model_dump(exclude_none=True) for m in request.messages],
        tools=request.tools,
        max_tokens=request.max_tokens,
    )

    return ChatResponse(
        request_id=str(uuid.uuid4()),
        message=ChatMessage(**message),
        finish_reason=finish_reason,
    )
