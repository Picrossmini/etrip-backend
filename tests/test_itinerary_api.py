"""POST /api/itinerary 的接口自动化测试。

全部走真实 HTTP（requests -> 后端 -> 假 DeepSeek），不联网、秒级、确定性。
覆盖：正常流、鉴权、schema 错、业务规则错、边界值、上游异常、幂等重复提交。
"""

import uuid

import pytest


def _body(**overrides):
    body = {
        "user_id": "u-001",
        "city": "成都",
        "tourism_types": ["自然风光", "美食"],
        "days": 3,
        "no_of_people": 2,
    }
    body.update(overrides)
    return body


# --- 正常流 ---------------------------------------------------------------


def test_health(api):
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_generate_ok_returns_structured_plan(api):
    response = api.post(_body())
    assert response.status_code == 200, response.text

    data = response.json()
    assert data["request_id"]
    assert data["description"].strip()
    assert data["from_cache"] is False

    assert len(data["days"]) == 3
    for index, day in enumerate(data["days"], start=1):
        assert day["day"] == index
        assert day["places"], "每天至少要有 1 个景点"
        for place in day["places"]:
            assert place["place_id"]
            assert place["name"]


def test_dirty_markdown_response_is_parsed(api):
    """模型把 JSON 包在 ```json 围栏里，后端应能剥壳后解析。"""
    api.set_mode("dirty")
    response = api.post(_body())
    assert response.status_code == 200, response.text
    assert len(response.json()["days"]) == 3


# --- 鉴权 -----------------------------------------------------------------


def test_missing_token_is_rejected(api):
    response = api.post(_body(), token=None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_wrong_token_is_rejected(api):
    response = api.post(_body(), token="definitely-not-valid")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


# --- 请求体 schema 错（应为 422） -----------------------------------------


def test_missing_required_field_is_schema_error(api):
    body = _body()
    body.pop("days")
    response = api.post(body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schema_error"


def test_wrong_field_type_is_schema_error(api):
    response = api.post(_body(days="three"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schema_error"


def test_empty_required_string_is_schema_error(api):
    response = api.post(_body(city=""))
    assert response.status_code == 422


# --- 业务规则错（应为 400） -----------------------------------------------


def test_empty_tourism_types_is_business_error(api):
    response = api.post(_body(tourism_types=[]))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("days", [0, -1, 31, 999])
def test_days_out_of_range_is_business_error(api, days):
    response = api.post(_body(days=days))
    assert response.status_code == 400
    assert "days" in response.json()["error"]["message"]


@pytest.mark.parametrize("people", [0, 21])
def test_people_out_of_range_is_business_error(api, people):
    response = api.post(_body(no_of_people=people))
    assert response.status_code == 400
    assert "no_of_people" in response.json()["error"]["message"]


def test_end_date_before_start_date_is_rejected(api):
    response = api.post(
        _body(days=2, start_date="2026-10-20", end_date="2026-10-19")
    )
    assert response.status_code == 400
    assert "end_date" in response.json()["error"]["message"]


def test_date_span_must_match_days(api):
    response = api.post(
        _body(days=3, start_date="2026-10-20", end_date="2026-10-21")
    )
    assert response.status_code == 400
    assert "不一致" in response.json()["error"]["message"]


def test_business_error_happens_before_calling_model(api):
    """业务校验不过时，不应该浪费一次模型调用。"""
    response = api.post(_body(days=99))
    assert response.status_code == 400
    assert api.upstream_calls == 0


# --- 上游（模型）异常 -----------------------------------------------------


def test_upstream_non_json_is_bad_response(api):
    api.set_mode("bad_json")
    response = api.post(_body())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_bad_response"


def test_upstream_empty_is_bad_response(api):
    api.set_mode("empty")
    response = api.post(_body())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_bad_response"


def test_upstream_missing_days_is_bad_response(api):
    api.set_mode("missing_days")
    response = api.post(_body())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_bad_response"


def test_upstream_wrong_day_count_is_bad_response(api):
    api.set_mode("wrong_count")
    response = api.post(_body(days=3))
    assert response.status_code == 502
    assert "不一致" in response.json()["error"]["message"]


def test_upstream_server_error_maps_to_502(api):
    api.set_mode("server_error")
    response = api.post(_body())
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"


def test_upstream_timeout_maps_to_504(api):
    api.set_mode("timeout")
    response = api.post(_body())
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "upstream_timeout"


# --- 幂等（防重复提交） ---------------------------------------------------


def test_idempotency_key_replays_same_response(api):
    key = f"idem-{uuid.uuid4()}"
    body = _body()

    first = api.post(body, idempotency_key=key)
    assert first.status_code == 200, first.text
    calls_after_first = api.upstream_calls
    assert calls_after_first == 1

    second = api.post(body, idempotency_key=key)
    assert second.status_code == 200
    assert second.json()["from_cache"] is True
    assert second.json()["request_id"] == first.json()["request_id"]
    assert api.upstream_calls == calls_after_first, "重复请求不应再次调用模型"


def test_different_idempotency_keys_generate_separately(api):
    body = _body()
    first = api.post(body, idempotency_key=f"a-{uuid.uuid4()}")
    second = api.post(body, idempotency_key=f"b-{uuid.uuid4()}")
    assert first.status_code == second.status_code == 200
    assert first.json()["request_id"] != second.json()["request_id"]
    assert api.upstream_calls == 2
