"""接口的数据契约。

分工：Pydantic 负责「类型/形状」校验（缺字段、类型错 -> 422）；
范围、日期一致性这类「业务规则」在 service 里校验（-> 400）。
"""

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

MAX_DAYS = 30
MAX_PEOPLE = 20
MAX_CATALOG = 200

# --- 聊天（POST /api/chat）的上限：把「一次能花掉多少 token」框死 ---
MAX_CHAT_MESSAGES = 50
MAX_MESSAGE_CHARS = 4000
MAX_CHAT_CHARS = 20000
MAX_TOKENS = 2000
DEFAULT_MAX_TOKENS = 600



class CatalogPlace(BaseModel):
    """候选地点目录里的一项。

    只放「挑景点时用得上的」字段，不放图片/简介那种大文本——目录要进提示词，
    越短越省钱。客户端拿回 place_id 后，在本地用自己的目录补全成完整对象。
    """

    place_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    city_name: str = Field(default="", max_length=64)
    tourism_type: str = Field(default="", max_length=64)


class ItineraryRequest(BaseModel):
    """POST /api/itinerary 的请求体。"""

    user_id: str = Field(min_length=1, max_length=64)
    city: str = Field(min_length=1, max_length=64)
    tourism_types: List[str]
    days: int
    no_of_people: int = 1
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    # 可选。带上时，提示词里会列出这些候选地点，模型只能从中挑 place_id，
    # 从而保证返回值能对回客户端的真实景点（有图片、评分、简介）。
    # 不带则退回原来的「模型自由发挥」，老客户端不受影响。
    catalog: List[CatalogPlace] = Field(default_factory=list, max_length=MAX_CATALOG)


class PlaceRef(BaseModel):
    place_id: str
    name: str


class DayPlan(BaseModel):
    day: int
    places: List[PlaceRef]
    summary: str = ""


class ItineraryResponse(BaseModel):
    request_id: str
    description: str
    days: List[DayPlan]
    from_cache: bool = False


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Optional[object] = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# --- 聊天 -----------------------------------------------------------------


class ChatMessage(BaseModel):
    """一条对话消息，形状跟 OpenAI / DeepSeek 的 messages 对齐。

    请求和响应共用它：客户端把整段对话发上来，后端把模型回的**那一条**助手消息
    原样还回去（含 `tool_calls`），客户端才有办法按原样追加进下一轮。
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    # 仅 role=tool 时带：指回是哪一次工具调用的结果。
    tool_call_id: Optional[str] = None
    # 仅 role=assistant 时可能带：模型要求客户端去执行的工具调用。
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ChatRequest(BaseModel):
    """POST /api/chat 的请求体。"""

    messages: List[ChatMessage]
    # 工具定义由客户端提供（工具本身也在客户端执行，比如查天气），
    # 后端不解释它，只是转给模型。
    tools: Optional[List[Dict[str, Any]]] = None
    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, ge=1, le=MAX_TOKENS)


class ChatResponse(BaseModel):
    request_id: str
    message: ChatMessage
    finish_reason: str = "stop"
