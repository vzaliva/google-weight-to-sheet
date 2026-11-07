from datetime import UTC, date, datetime

import pytest
from conftest import FakeResponse, FakeSession, make_group, measure_answer

from google_weight_to_sheet import withings
from google_weight_to_sheet.errors import DataError, WithingsError


def test_window_has_a_day_of_margin_for_every_time_zone():
    start, end = withings.window_timestamps(date(2026, 11, 1), date(2026, 11, 7))
    assert start == int(datetime(2026, 10, 31, tzinfo=UTC).timestamp())
    assert end == int(datetime(2026, 11, 8, tzinfo=UTC).timestamp())


def test_listing_posts_a_getmeas_form_for_weights_only():
    session = FakeSession(FakeResponse(200, measure_answer([])))
    assert withings.list_weight_groups(session, 100, 200) == ([], "America/Los_Angeles")
    [call] = session.calls
    assert call["method"] == "POST"
    assert call["url"] == "https://wbsapi.withings.net/measure"
    assert call["data"] == {
        "action": "getmeas",
        "meastype": 1,
        "category": 1,
        "startdate": 100,
        "enddate": 200,
    }


def test_listing_follows_more_and_offset():
    first = make_group(1, 2026, 11, 2, 7, 1, 79400)
    second = make_group(2, 2026, 11, 1, 7, 0, 79500)
    session = FakeSession(
        FakeResponse(200, measure_answer([first], more=1, offset=1)),
        FakeResponse(200, measure_answer([second], zone=None)),
    )
    groups, zone = withings.list_weight_groups(session, 100, 200)
    assert groups == [first, second]
    assert zone == "America/Los_Angeles"
    page1, page2 = session.calls
    assert "offset" not in page1["data"]
    assert page2["data"]["offset"] == 1
    assert page2["data"]["startdate"] == 100


def test_listing_stops_if_the_offset_never_moves():
    stuck = FakeResponse(200, measure_answer([], more=1, offset=5))
    with pytest.raises(DataError, match="same offset"):
        withings.list_weight_groups(FakeSession(stuck, stuck), 100, 200)


def test_listing_ignores_groups_without_a_weight():
    fat_only = make_group(3, 2026, 11, 2, 7, 1, 0)
    fat_only["measures"] = [{"value": 215, "type": 6, "unit": -1}]
    weight = make_group(4, 2026, 11, 2, 7, 1, 79400)
    session = FakeSession(FakeResponse(200, measure_answer([fat_only, weight])))
    assert withings.list_weight_groups(session, 100, 200)[0] == [weight]


def test_a_non_zero_status_is_an_error_even_with_http_200():
    session = FakeSession(FakeResponse(200, {"status": 601, "error": "Too Many Requests"}))
    with pytest.raises(WithingsError, match="status 601 .*Too Many Requests") as caught:
        withings.list_weight_groups(session, 100, 200)
    assert caught.value.status == 601


def test_parse_reading_uses_the_account_time_zone():
    group = make_group(5765432101, 2026, 11, 2, 7, 1, 79400, zone="America/Los_Angeles")
    reading = withings.parse_reading(group, "America/Los_Angeles")
    assert reading == withings.Reading("5765432101", datetime(2026, 11, 2, 7, 1), 79400.0)


def test_parse_reading_prefers_a_zone_on_the_group_itself():
    group = make_group(1, 2026, 11, 2, 7, 1, 79400, zone="Europe/Paris")
    group["timezone"] = "Europe/Paris"
    assert withings.parse_reading(group, "America/Los_Angeles").local == datetime(2026, 11, 2, 7, 1)


def test_parse_reading_follows_daylight_saving_time():
    # US clocks went back on 1 November 2026: 07:00 is UTC-7 before and UTC-8 after.
    before = make_group(1, 2026, 10, 31, 7, 0, 79400)
    after = make_group(2, 2026, 11, 2, 7, 0, 79400)
    assert after["date"] - before["date"] == 2 * 86400 + 3600
    assert withings.parse_reading(before, "America/Los_Angeles").local.hour == 7
    assert withings.parse_reading(after, "America/Los_Angeles").local.hour == 7


def test_parse_reading_without_any_zone_uses_this_machine():
    group = make_group(1, 2026, 11, 2, 7, 1, 79400)
    assert withings.parse_reading(group, None).local == datetime.fromtimestamp(group["date"])


@pytest.mark.parametrize(
    ("value", "unit", "grams"),
    [(79400, -3, 79400.0), (7940, -2, 79400.0), (794, -1, 79400.0), (79, 0, 79000.0)],
)
def test_parse_reading_scales_value_by_unit(value, unit, grams):
    group = make_group(1, 2026, 11, 2, 7, 1, 0)
    group["measures"] = [{"value": value, "type": 1, "unit": unit}]
    assert withings.parse_reading(group, "UTC").grams == grams


def test_parse_reading_picks_the_weight_among_other_measures():
    group = make_group(1, 2026, 11, 2, 7, 1, 79400)
    group["measures"].insert(0, {"value": 215, "type": 6, "unit": -1})
    assert withings.parse_reading(group, "UTC").grams == 79400.0


def test_parse_reading_flags_an_ambiguous_weigh_in():
    group = make_group(1, 2026, 11, 2, 7, 1, 79400, attrib=withings.AMBIGUOUS)
    assert withings.parse_reading(group, "UTC").ambiguous
    for attrib in (0, 2, 7, 8):  # by a scale, entered by hand, confirmed, same as 0
        group["attrib"] = attrib
        assert not withings.parse_reading(group, "UTC").ambiguous


def test_parse_reading_rejects_an_unknown_time_zone():
    group = make_group(1, 2026, 11, 2, 7, 1, 79400)
    with pytest.raises(DataError, match="measure group 1"):
        withings.parse_reading(group, "Mars/Olympus_Mons")


def test_parse_reading_rejects_a_group_without_a_weight():
    group = make_group(9, 2026, 11, 2, 7, 1, 79400)
    group["measures"] = []
    with pytest.raises(DataError, match="cannot read Withings measure group 9"):
        withings.parse_reading(group, "UTC")
