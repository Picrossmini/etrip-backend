"""接口的数据契约。

分工：Pydantic 负责「类型/形状」校验（缺字段、类型错 -> 422）；
范围、日期一致性这类「业务规则」在 service 里校验（-> 400）。
"""

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field

MAX_DAYS = 30
MAX_PEOPLE = 20


class ItineraryRequest(BaseModel):
    """POST /api/itinerary 的请求体。"""

    user_id: str = Field(min_length=1, max_length=64)
    city: str = Field(min_length=1, max_length=64)
    tourism_types: List[str]
    days: int
    no_of_people: int = 1
    start_date: Optional[date] = None
    end_date: Optional[date] = None


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
