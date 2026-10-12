"""FastAPI 入口：路由、鉴权、幂等、统一错误处理。

运行：uvicorn app.main:app --reload --port 8000
"""

import threading
from typing import Dict, Optional

from fastapi import Depends, FastAPI, Header
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .errors import AppError, ConflictError
from .llm_client import DeepSeekClient, get_llm_client
from .schemas import ItineraryRequest, ItineraryResponse
from .security import require_token
from .service import generate_plan

app = FastAPI(title="Etrip Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 幂等存储：key = "{token}:{Idempotency-Key}"，演示用内存实现（重启即清空）。
_replay_store: Dict[str, Dict] = {}
_inflight: set = set()
_store_lock = threading.Lock()


@app.exception_handler(AppError)
async def _handle_app_error(_request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(_request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "schema_error",
                "message": "请求体不符合接口约定",
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/itinerary", response_model=ItineraryResponse)
def create_itinerary(
    request: ItineraryRequest,
    token: str = Depends(require_token),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    client: DeepSeekClient = Depends(get_llm_client),
):
    if not idempotency_key:
        return generate_plan(request, client)

    store_key = f"{token}:{idempotency_key}"
    with _store_lock:
        cached = _replay_store.get(store_key)
        if cached is not None:
            replayed = dict(cached)
            replayed["from_cache"] = True
            return JSONResponse(content=replayed, headers={"X-Idempotent-Replay": "true"})
        if store_key in _inflight:
            raise ConflictError("相同 Idempotency-Key 的请求正在处理中")
        _inflight.add(store_key)

    try:
        result = generate_plan(request, client)
    finally:
        with _store_lock:
            _inflight.discard(store_key)

    with _store_lock:
        _replay_store[store_key] = result.model_dump(mode="json")

    return result
