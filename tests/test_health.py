from datetime import date, datetime

import pytest
from conftest import FakeResponse, FakeSession, make_point

from google_weight_to_sheet import health
from google_weight_to_sheet.errors import DataError


def test_filter_covers_a_half_open_civil_date_range():
    assert health.civil_filter(date(2026, 11, 1), date(2026, 11, 7)) == (
        'weight.sample_time.civil_time >= "2026-11-01" '
        'AND weight.sample_time.civil_time < "2026-11-07"'
    )


def test_listing_follows_next_page_tokens():
    first = make_point("1", 2026, 11, 2, 7, 1, 79400)
    second = make_point("2", 2026, 11, 1, 7, 0, 79500)
    session = FakeSession(
        FakeResponse(200, {"dataPoints": [first], "nextPageToken": "page2"}),
        FakeResponse(200, {"dataPoints": [second]}),
    )
    points = list(health.list_weight_points(session, date(2026, 11, 1), date(2026, 11, 4)))
    assert points == [first, second]

    page1, page2 = session.calls
    assert page1["url"] == "https://health.googleapis.com/v4/users/me/dataTypes/weight/dataPoints"
    assert page1["params"]["filter"].startswith("weight.sample_time.civil_time >= ")
    assert "pageToken" not in page1["params"]
    assert page2["params"]["pageToken"] == "page2"
    assert page2["params"]["filter"] == page1["params"]["filter"]


def test_listing_an_empty_response():
    session = FakeSession(FakeResponse(200, {}))
    assert list(health.list_weight_points(session, date(2026, 11, 1), date(2026, 11, 4))) == []


def test_parse_reading_uses_the_civil_time():
    reading = health.parse_reading(make_point("abc", 2026, 11, 2, 7, 1, 79400))
    assert reading.point_id == "abc"
    assert reading.local == datetime(2026, 11, 2, 7, 1)
    assert reading.grams == 79400.0


def test_parse_reading_midnight_has_no_time_fields():
    reading = health.parse_reading(make_point("m", 2026, 11, 2, 0, 0, 80000))
    assert reading.local == datetime(2026, 11, 2, 0, 0)


def test_parse_reading_with_zero_minutes_omitted():
    reading = health.parse_reading(make_point("h", 2026, 11, 2, 9, 0, 80000))
    assert reading.local == datetime(2026, 11, 2, 9, 0)


def test_parse_reading_falls_back_to_physical_time_and_offset():
    point = make_point("p", 2026, 11, 2, 15, 1, 79400, utc_offset="-28800s")
    del point["weight"]["sampleTime"]["civilTime"]
    reading = health.parse_reading(point)
    assert reading.local == datetime(2026, 11, 2, 7, 1)


def test_parse_reading_rejects_an_unknown_shape():
    with pytest.raises(DataError, match="cannot read weight data point"):
        health.parse_reading({"name": "users/1/dataTypes/weight/dataPoints/9", "steps": {}})


def test_parse_reading_rejects_a_point_with_no_usable_time():
    point = make_point("t", 2026, 11, 2, 7, 1, 79400)
    del point["weight"]["sampleTime"]["civilTime"]
    del point["weight"]["sampleTime"]["utcOffset"]
    with pytest.raises(DataError):
        health.parse_reading(point)
