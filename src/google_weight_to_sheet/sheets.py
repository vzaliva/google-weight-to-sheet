"""Reading and appending rows in the weight sheet.

The sheet has five columns, matching the layout the earlier sync used:
  A  date and time, text such as "December 10, 2016 at 03:20PM"
  B  weight
  C  unit label
  D  BMI (left empty: Withings does not send it)
  E  data point id
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from .errors import ApiError, SheetError
from .http import request_json

API_URL = "https://sheets.googleapis.com/v4/spreadsheets"

# Spelled out so the text never depends on the locale (cron runs with a bare one).
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_TEXT_DATETIME = re.compile(
    r"^\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\s+at\s+(\d{1,2}):(\d{2})\s*([AaPp][Mm])\s*$"
)
_NUMERIC_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
)


def format_datetime(moment: datetime) -> str:
    """Format as "December 10, 2016 at 03:20PM"."""
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return (
        f"{MONTHS[moment.month - 1]} {moment.day}, {moment.year} "
        f"at {hour:02d}:{moment.minute:02d}{suffix}"
    )


def parse_datetime(value: Any) -> datetime | None:
    """Parse a cell from column A; None if it is not a date this tool recognises."""
    if not isinstance(value, str):
        return None
    match = _TEXT_DATETIME.match(value)
    if match:
        month_name, day, year, hour, minute, suffix = match.groups()
        months = {name.lower(): number for number, name in enumerate(MONTHS, 1)}
        month = months.get(month_name.lower())
        hour_12 = int(hour)
        if month is None or not 1 <= hour_12 <= 12:
            return None
        hour_24 = hour_12 % 12 + (12 if suffix.lower() == "pm" else 0)
        try:
            return datetime(int(year), month, int(day), hour_24, int(minute))
        except ValueError:
            return None
    for fmt in _NUMERIC_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None


def _a1_range(sheet_name: str, cells: str) -> str:
    quoted = "'" + sheet_name.replace("'", "''") + "'"
    return f"{quoted}!{cells}"


def _values_url(spreadsheet_id: str, sheet_name: str, cells: str) -> str:
    rng = quote(_a1_range(sheet_name, cells), safe="")
    return f"{API_URL}/{quote(spreadsheet_id, safe='')}/values/{rng}"


def get_title(session: Any, spreadsheet_id: str) -> str:
    """The spreadsheet's title; a cheap way to prove the token can open it."""
    body = request_json(
        session,
        "GET",
        f"{API_URL}/{quote(spreadsheet_id, safe='')}",
        params={"fields": "properties.title"},
    )
    return body["properties"]["title"]


def read_rows(session: Any, spreadsheet_id: str, sheet_name: str) -> list[list[Any]]:
    """All data rows (everything below the header), columns A to E.

    Numbers come back as numbers and dates as the text the sheet displays, so a
    weight is never confused with a formatted string.
    """
    url = _values_url(spreadsheet_id, sheet_name, "A2:E")
    try:
        body = request_json(
            session,
            "GET",
            url,
            params={
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
            },
        )
    except ApiError as exc:
        if exc.status == 400 and "Unable to parse range" in exc.body:
            raise SheetError(
                f"the spreadsheet has no tab named {sheet_name!r}; "
                "check sheet_name in the configuration"
            ) from exc
        raise
    return body.get("values", [])


def append_rows(
    session: Any, spreadsheet_id: str, sheet_name: str, rows: list[list[Any]]
) -> None:
    """Append rows after the last one. RAW input keeps the text in column A as text."""
    url = _values_url(spreadsheet_id, sheet_name, "A:E") + ":append"
    request_json(
        session,
        "POST",
        url,
        params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
        json={"majorDimension": "ROWS", "values": rows},
    )
