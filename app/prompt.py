"""构造发给模型的消息。"""

from typing import Dict, List

from .schemas import ItineraryRequest

SYSTEM_PROMPT = (
    "你是一名旅行行程规划助手。只输出符合要求的 JSON，"
    "不要输出解释、不要输出 markdown 代码块。"
)


def build_messages(request: ItineraryRequest) -> List[Dict[str, str]]:
    preferences = "、".join(request.tourism_types)
    user_prompt = (
        f"为「{request.city}」规划 {request.days} 天的旅行行程，"
        f"同行 {request.no_of_people} 人，偏好：{preferences}。\n"
        f"严格按下面的 JSON 结构输出，days 数组长度必须恰好等于 {request.days}，"
        f"每天至少 1 个地点：\n"
        '{"description": "一句话概述", "days": ['
        '{"day": 1, "summary": "当天安排", "places": ['
        '{"place_id": "p1", "name": "景点名"}]}]}'
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
