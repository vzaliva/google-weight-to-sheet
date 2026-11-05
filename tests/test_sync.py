import json
from datetime import date, datetime

import pytest
from conftest import FakeResponse, FakeSession, make_point

from google_weight_to_sheet import health, sync
from google_weight_to_sheet.config import Config
from google_weight_to_sheet.errors import ConfigError

TODAY = date(2026, 11, 5)


def reading(point_id, when, grams):
    return health.Reading(point_id, when, grams)


# --- units -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("grams", "pounds"),
    [(0, 0.0), (79400, 175.0), (100000, 220.5), (79500, 175.3), (45359.237, 100.0)],
)
def test_grams_to_pounds_rounds_to_one_decimal(grams, pounds):
    assert sync.grams_to_pounds(grams) == pounds


# --- reading the existing sheet --------------------------------------------


def test_index_handles_text_numbers_and_short_rows():
    rows = [
        ["December 10, 2016 at 03:20PM", 175.0, "pounds"],  # legacy: no BMI, no id
        ["November 2, 2026 at 07:01AM", "175.0", "pounds", 24.1, 1762000000000.0],
        ["", "", "", "", ""],  # blank row is ignored
        ["not a date", 170.0, "pounds"],
    ]
    index = sync.index_rows(rows)
    assert index.rows == 3
    assert index.unparsed == 1
    assert index.newest == datetime(2026, 11, 2, 7, 1)
    assert index.ids == {"1762000000000"}  # a numeric id comes back as a float
    assert (datetime(2016, 12, 10, 15, 20), 1750) in index.keys
    assert (datetime(2026, 11, 2, 7, 1), 1750) in index.keys  # "175.0" as text still keys


def test_index_of_an_empty_sheet():
    index = sync.index_rows([])
    assert index.newest is None
    assert not index.ids and not index.keys


# --- deciding what to add ---------------------------------------------------


def test_new_rows_have_the_five_column_layout():
    index = sync.ExistingIndex()
    [decision] = sync.plan_rows([reading("abc", datetime(2026, 11, 3, 7, 5), 79400)], index, "pounds")
    assert decision.action == "new"
    assert decision.row == ["November 3, 2026 at 07:05AM", 175.0, "pounds", "", "abc"]


def test_skips_a_reading_whose_id_is_in_the_sheet():
    index = sync.index_rows([["x", 1, "pounds", "", "abc"]])
    [decision] = sync.plan_rows([reading("abc", datetime(2026, 11, 3, 7, 5), 79400)], index, "pounds")
    assert decision.action == "skip-id"


def test_skips_a_reading_matching_a_legacy_row_by_minute_and_weight():
    legacy = [["November 2, 2026 at 07:01AM", 175.0, "pounds"]]  # no id, written by the old sync
    index = sync.index_rows(legacy)
    # the new API reports seconds too; the minute and the rounded weight must still match
    [decision] = sync.plan_rows(
        [reading("new-id", datetime(2026, 11, 2, 7, 1, 42), 79400)], index, "pounds"
    )
    assert decision.action == "skip-match"


def test_a_different_weight_in_the_same_minute_is_new():
    index = sync.index_rows([["November 2, 2026 at 07:01AM", 175.0, "pounds"]])
    [decision] = sync.plan_rows([reading("n", datetime(2026, 11, 2, 7, 1), 80000)], index, "pounds")
    assert decision.action == "new"


def test_decisions_are_oldest_first_and_do_not_repeat_within_a_batch():
    readings = [
        reading("b", datetime(2026, 11, 4, 7, 0), 79400),
        reading("a", datetime(2026, 11, 3, 7, 0), 79400),
        reading("a", datetime(2026, 11, 3, 7, 0), 79400),  # the API repeated a point
    ]
    decisions = sync.plan_rows(readings, sync.ExistingIndex(), "pounds")
    assert [(d.reading.point_id, d.action) for d in decisions] == [
        ("a", "new"),
        ("a", "skip-id"),
        ("b", "new"),
    ]


def test_planning_does_not_modify_the_index():
    index = sync.ExistingIndex()
    sync.plan_rows([reading("a", datetime(2026, 11, 3, 7, 0), 79400)], index, "pounds")
    assert not index.ids and not index.keys


# --- the date window ----------------------------------------------------------


def test_window_starts_just_before_the_newest_row():
    index = sync.index_rows([["November 2, 2026 at 07:01AM", 175.0]])
    assert sync.choose_window(index, TODAY, days=None, since=None) == (date(2026, 11, 1), date(2026, 11, 7))


def test_window_for_an_empty_sheet_looks_back_a_fixed_time():
    start, _ = sync.choose_window(sync.ExistingIndex(), TODAY, days=None, since=None)
    assert start == date(2026, 10, 6)


def test_explicit_days_and_since_win_over_the_sheet():
    index = sync.index_rows([["November 2, 2026 at 07:01AM", 175.0]])
    assert sync.choose_window(index, TODAY, days=10, since=None)[0] == date(2026, 10, 26)
    assert sync.choose_window(index, TODAY, days=None, since=date(2026, 9, 1))[0] == date(2026, 9, 1)


def test_window_start_is_never_after_today():
    index = sync.index_rows([["November 30, 2030 at 07:01AM", 175.0]])
    start, end = sync.choose_window(index, TODAY, days=None, since=None)
    assert start == TODAY and end > start


# --- the whole run --------------------------------------------------------------


def make_cfg(tmp_path):
    return Config(config_dir=tmp_path, spreadsheet_id="sheet-id")


def routed(rows, points):
    """A session that serves the sheet, the Health API, and records appends."""

    def handler(method, url, params, body):
        if "sheets.googleapis.com" in url and method == "GET":
            return FakeResponse(200, {"values": rows})
        if "sheets.googleapis.com" in url and method == "POST":
            return FakeResponse(200, {})
        if "health.googleapis.com" in url:
            return FakeResponse(200, {"dataPoints": points})
        raise AssertionError(f"unexpected request {method} {url}")

    return FakeSession(handler=handler)


LEGACY_ROW = ["November 2, 2026 at 07:01AM", 175.0, "pounds", 24.1, 1762000000000.0]


def test_run_appends_only_what_is_missing(tmp_path):
    points = [
        make_point("old", 2026, 11, 2, 7, 1, 79400),  # same minute and weight as the legacy row
        make_point("fresh", 2026, 11, 4, 7, 30, 79000),
    ]
    session = routed([LEGACY_ROW], points)
    result = sync.run_sync(session, session, make_cfg(tmp_path), today=TODAY)

    assert [d.action for d in result.decisions] == ["skip-match", "new"]
    assert result.appended == 1
    post = [c for c in session.calls if c["method"] == "POST"]
    assert len(post) == 1
    assert post[0]["json"]["values"] == [["November 4, 2026 at 07:30AM", 174.2, "pounds", "", "fresh"]]


def test_dry_run_writes_nothing(tmp_path):
    session = routed([], [make_point("fresh", 2026, 11, 4, 7, 30, 79000)])
    result = sync.run_sync(session, session, make_cfg(tmp_path), dry_run=True, today=TODAY)
    assert result.count("new") == 1
    assert result.appended == 0
    assert not [c for c in session.calls if c["method"] == "POST"]


def test_nothing_new_means_no_append(tmp_path):
    session = routed([LEGACY_ROW], [make_point("old", 2026, 11, 2, 7, 1, 79400)])
    result = sync.run_sync(session, session, make_cfg(tmp_path), today=TODAY)
    assert result.appended == 0
    assert not [c for c in session.calls if c["method"] == "POST"]


def test_running_twice_does_not_duplicate(tmp_path):
    points = [make_point("fresh", 2026, 11, 4, 7, 30, 79000)]
    first = routed([LEGACY_ROW], points)
    sync.run_sync(first, first, make_cfg(tmp_path), today=TODAY)
    appended = next(c for c in first.calls if c["method"] == "POST")["json"]["values"]

    second = routed([LEGACY_ROW, *appended], points)
    result = sync.run_sync(second, second, make_cfg(tmp_path), today=TODAY)
    assert result.appended == 0
    assert result.decisions[0].action == "skip-id"


def test_dump_saves_the_raw_response_privately(tmp_path):
    point = make_point("fresh", 2026, 11, 4, 7, 30, 79000)
    session = routed([], [point])
    dump = tmp_path / "raw.json"
    sync.run_sync(session, session, make_cfg(tmp_path), dry_run=True, today=TODAY, dump_path=dump)
    assert json.loads(dump.read_text())["dataPoints"] == [point]
    assert dump.stat().st_mode & 0o777 == 0o600


def test_run_requires_a_spreadsheet(tmp_path):
    with pytest.raises(ConfigError):
        sync.run_sync(FakeSession(), FakeSession(), Config(config_dir=tmp_path), today=TODAY)


def test_health_and_sheets_requests_use_their_own_session(tmp_path):
    points = [make_point("fresh", 2026, 11, 4, 7, 30, 79000)]

    def health_handler(method, url, params, body):
        return FakeResponse(200, {"dataPoints": points})

    def sheets_handler(method, url, params, body):
        return FakeResponse(200, {"values": [LEGACY_ROW]} if method == "GET" else {})

    health_session = FakeSession(handler=health_handler)
    sheets_session = FakeSession(handler=sheets_handler)
    sync.run_sync(health_session, sheets_session, make_cfg(tmp_path), today=TODAY)

    assert health_session.calls
    assert all("health.googleapis.com" in c["url"] for c in health_session.calls)
    assert all("sheets.googleapis.com" in c["url"] for c in sheets_session.calls)
    assert {c["method"] for c in sheets_session.calls} == {"GET", "POST"}
