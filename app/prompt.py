"""构造发给模型的消息。"""

from typing import Dict, List

from .schemas import ItineraryRequest

SYSTEM_PROMPT = (
    "你是一名旅行行程规划助手。只输出符合要求的 JSON，"
    "不要输出解释、不要输出 markdown 代码块。"
)

OUTPUT_SHAPE = (
    '{"description": "一句话概述", "days": ['
    '{"day": 1, "summary": "当天安排", "places": ['
    '{"place_id": "p1", "name": "景点名"}]}]}'
)


def _catalog_block(request: ItineraryRequest) -> str:
    """把候选地点目录写成提示词里的一段。

    只列 place_id / 名字 / 城市 / 类别：模型只需要从中挑 id，不关心图片和简介，
    而目录会随每次请求发给模型，字段越少提示词越短。
    """
    lines = [
        "可选地点目录（格式：place_id | 名字 | 城市 | 类别）：",
        *(
            f"{item.place_id} | {item.name} | {item.city_name} | {item.tourism_type}"
            for item in request.catalog
        ),
        f"places 里的 place_id 只能从上面这 {len(request.catalog)} 个里选，"
        "不要自己编造新的 id；name 必须与目录中的写法完全一致。",
    ]
    return "\n".join(lines)


def build_messages(request: ItineraryRequest) -> List[Dict[str, str]]:
    preferences = "、".join(request.tourism_types)
    parts = [
        f"为「{request.city}」规划 {request.days} 天的旅行行程，"
        f"同行 {request.no_of_people} 人，偏好：{preferences}。"
    ]

    # 有目录就约束模型只能从目录里挑；没有则维持「自由发挥」的老行为。
    if request.catalog:
        parts.append(_catalog_block(request))

    parts.append(
        f"严格按下面的 JSON 结构输出，days 数组长度必须恰好等于 {request.days}，"
        f"每天至少 1 个地点：\n{OUTPUT_SHAPE}"
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
