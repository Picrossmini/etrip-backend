"""业务编排：先校验业务规则，再调模型，再对模型输出做结构校验。"""

import uuid
from typing import Any, Dict, List

from .errors import InvalidRequestError, UpstreamBadResponse
from .llm_client import DeepSeekClient
from .prompt import build_messages
from .schemas import (
    MAX_DAYS,
    MAX_PEOPLE,
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
