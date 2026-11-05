from datetime import datetime

import pytest
from conftest import FakeResponse, FakeSession

from google_weight_to_sheet import sheets
from google_weight_to_sheet.errors import SheetError


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2016, 12, 10, 15, 20), "December 10, 2016 at 03:20PM"),
        (datetime(2026, 1, 5, 7, 1), "January 5, 2026 at 07:01AM"),
        (datetime(2026, 11, 2, 0, 0), "November 2, 2026 at 12:00AM"),
        (datetime(2026, 11, 2, 12, 0), "November 2, 2026 at 12:00PM"),
        (datetime(2026, 11, 2, 23, 59, 59), "November 2, 2026 at 11:59PM"),
    ],
)
def test_format_matches_the_existing_rows(moment, expected):
    assert sheets.format_datetime(moment) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("December 10, 2016 at 03:20PM", datetime(2016, 12, 10, 15, 20)),
        ("december 10, 2016 at 03:20pm", datetime(2016, 12, 10, 15, 20)),
        ("November 2, 2026 at 12:00AM", datetime(2026, 11, 2, 0, 0)),
        ("November 2, 2026 at 12:30PM", datetime(2026, 11, 2, 12, 30)),
        ("2026-11-02 07:01:00", datetime(2026, 11, 2, 7, 1)),
        ("2026-11-02T07:01:00", datetime(2026, 11, 2, 7, 1)),
        ("11/02/2026 07:01:00", datetime(2026, 11, 2, 7, 1)),
    ],
)
def test_parse_accepts_the_known_formats(text, expected):
    assert sheets.parse_datetime(text) == expected


@pytest.mark.parametrize(
    "value", ["", "yesterday", "Smarch 3, 2026 at 01:00AM", "November 31, 2026 at 01:00AM", None, 46328.5]
)
def test_parse_rejects_everything_else(value):
    assert sheets.parse_datetime(value) is None


def test_format_and_parse_round_trip():
    moment = datetime(2026, 11, 2, 7, 1)
    assert sheets.parse_datetime(sheets.format_datetime(moment)) == moment


def test_read_rows_asks_for_unformatted_values_and_quotes_the_tab():
    session = FakeSession(FakeResponse(200, {"values": [["a", 1]]}))
    assert sheets.read_rows(session, "sheet-id", "My 'Weight'") == [["a", 1]]
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"].endswith("/sheet-id/values/%27My%20%27%27Weight%27%27%27%21A2%3AE")
    assert call["params"]["valueRenderOption"] == "UNFORMATTED_VALUE"


def test_read_rows_of_an_empty_tab():
    assert sheets.read_rows(FakeSession(FakeResponse(200, {})), "id", "Weight") == []


def test_read_rows_reports_a_missing_tab_clearly():
    body = '{"error": {"message": "Unable to parse range: Nope!A2:E"}}'
    session = FakeSession(FakeResponse(400, text=body))
    with pytest.raises(SheetError, match="no tab named 'Nope'"):
        sheets.read_rows(session, "id", "Nope")


def test_append_rows_sends_raw_values():
    session = FakeSession(FakeResponse(200, {}))
    sheets.append_rows(session, "id", "Weight", [["x", 1.5]])
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/id/values/%27Weight%27%21A%3AE:append")
    assert call["params"] == {"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}
    assert call["json"] == {"majorDimension": "ROWS", "values": [["x", 1.5]]}
