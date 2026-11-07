import pytest
from conftest import FakeResponse, FakeSession

from google_weight_to_sheet.errors import ApiError
from google_weight_to_sheet.http import MAX_ATTEMPTS, request_json


def test_returns_the_json_body():
    session = FakeSession(FakeResponse(200, {"ok": True}))
    assert request_json(session, "GET", "https://x/y", params={"a": 1}) == {"ok": True}
    assert session.calls == [
        {"method": "GET", "url": "https://x/y", "params": {"a": 1}, "data": None, "json": None}
    ]


def test_retries_transient_errors_with_backoff():
    session = FakeSession(FakeResponse(503, text="busy"), FakeResponse(429, text="slow"), FakeResponse(200, {"ok": 1}))
    sleeps = []
    assert request_json(session, "GET", "https://x", sleep=sleeps.append) == {"ok": 1}
    assert sleeps == [2, 4]


def test_gives_up_after_the_last_attempt():
    session = FakeSession(*[FakeResponse(503, text="down")] * MAX_ATTEMPTS)
    with pytest.raises(ApiError) as caught:
        request_json(session, "GET", "https://x", sleep=lambda _: None)
    assert caught.value.status == 503
    assert len(session.calls) == MAX_ATTEMPTS


def test_client_errors_are_not_retried_and_carry_the_body():
    session = FakeSession(FakeResponse(403, text="no access"))
    with pytest.raises(ApiError, match="no access") as caught:
        request_json(session, "GET", "https://x", sleep=lambda _: None)
    assert caught.value.status == 403
    assert len(session.calls) == 1


def test_form_data_is_passed_through():
    session = FakeSession(FakeResponse(200, {"status": 0}))
    request_json(session, "POST", "https://x", data={"action": "getmeas"})
    assert session.calls[0]["data"] == {"action": "getmeas"}
