"""The sync itself: decide which readings are new and append them.

Rows written by earlier syncs (Fitbit, then the Google Health API) carry other
ids or none, so a reading counts as already present when either its id is in
column E or a row has the same minute and the same weight.

A reading Withings marks as ambiguous is left out: the scale could not tell
who stood on it, and the sheet is append-only, so another person's weigh-in
would stay in it for good.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import sheets, withings
from .auth import write_private
from .config import Config

KG_TO_LB = 2.2046226218
DEFAULT_LOOKBACK_DAYS = 30
# Re-read a little before the newest row so a late-syncing scale is still caught.
OVERLAP_DAYS = 1


def _round_half_up_tenths(value: float) -> int:
    return math.floor(value * 10 + 0.5)


def grams_to_pounds(grams: float) -> float:
    """Pounds to one decimal, rounded half up, the way the sheet's existing rows were."""
    return _round_half_up_tenths(grams / 1000 * KG_TO_LB) / 10


def normalise_id(value: Any) -> str:
    """Cell value to id text; Sheets stores numeric-looking ids as numbers."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


@dataclass
class ExistingIndex:
    ids: set[str] = field(default_factory=set)
    keys: set[tuple[datetime, int]] = field(default_factory=set)
    newest: datetime | None = None
    rows: int = 0
    unparsed: int = 0  # rows whose date could not be read


def _cell(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else ""


def _tenths_of_cell(value: Any) -> int | None:
    try:
        return _round_half_up_tenths(float(value))
    except (TypeError, ValueError):
        return None


def index_rows(rows: list[list[Any]]) -> ExistingIndex:
    """Summarise existing sheet rows for duplicate detection."""
    index = ExistingIndex()
    for row in rows:
        if not any(str(cell).strip() for cell in row):
            continue
        index.rows += 1
        point_id = normalise_id(_cell(row, 4))
        if point_id:
            index.ids.add(point_id)
        moment = sheets.parse_datetime(_cell(row, 0))
        if moment is None:
            index.unparsed += 1
            continue
        if index.newest is None or moment > index.newest:
            index.newest = moment
        tenths = _tenths_of_cell(_cell(row, 1))
        if tenths is not None:
            index.keys.add((moment.replace(second=0, microsecond=0), tenths))
    return index


@dataclass(frozen=True)
class Decision:
    reading: withings.Reading
    action: str  # "new", "skip-id", "skip-ambiguous" or "skip-match"
    row: list[Any] | None = None


def plan_rows(
    readings: list[withings.Reading], index: ExistingIndex, unit_label: str
) -> list[Decision]:
    """Decide for each reading, oldest first, whether it needs a new row."""
    ids = set(index.ids)
    keys = set(index.keys)
    decisions: list[Decision] = []
    for reading in sorted(readings, key=lambda r: (r.local, r.point_id)):
        pounds = grams_to_pounds(reading.grams)
        key = (reading.local.replace(second=0, microsecond=0), _round_half_up_tenths(pounds))
        if reading.point_id in ids:
            decisions.append(Decision(reading, "skip-id"))
        elif reading.ambiguous:
            decisions.append(Decision(reading, "skip-ambiguous"))
        elif key in keys:
            decisions.append(Decision(reading, "skip-match"))
        else:
            row = [
                sheets.format_datetime(reading.local),
                pounds,
                unit_label,
                "",
                reading.point_id,
            ]
            decisions.append(Decision(reading, "new", row))
            ids.add(reading.point_id)
            keys.add(key)
    return decisions


def choose_window(
    index: ExistingIndex, today: date, *, days: int | None, since: date | None
) -> tuple[date, date]:
    """The civil date range [start, end) to ask the API for.

    An explicit --since or --days wins; otherwise start just before the newest
    row in the sheet, and fall back to a fixed look-back for an empty sheet.
    The end is two days ahead so a server in another time zone cannot cut off
    today's measurements.
    """
    end = today + timedelta(days=2)
    if since is not None:
        start = since
    elif days is not None:
        start = today - timedelta(days=days)
    elif index.newest is not None:
        start = index.newest.date() - timedelta(days=OVERLAP_DAYS)
    else:
        start = today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    return min(start, today), end


def dump_text(start: date, end: date, timezone: str | None, groups: list[dict[str, Any]]) -> str:
    """The raw answer as saved by --dump; it holds personal health data."""
    payload = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": timezone,
        "measuregrps": groups,
    }
    return json.dumps(payload, indent=2) + "\n"


@dataclass
class SyncResult:
    start: date
    end: date
    fetched: int
    decisions: list[Decision]
    appended: int
    existing_rows: int
    unparsed_rows: int

    def count(self, action: str) -> int:
        return sum(1 for d in self.decisions if d.action == action)


def run_sync(
    withings_session: Any,
    sheets_session: Any,
    cfg: Config,
    *,
    dry_run: bool = False,
    days: int | None = None,
    since: date | None = None,
    today: date | None = None,
    dump_path: Path | None = None,
) -> SyncResult:
    spreadsheet_id = cfg.require_spreadsheet_id()
    rows = sheets.read_rows(sheets_session, spreadsheet_id, cfg.sheet_name)
    index = index_rows(rows)
    start, end = choose_window(index, today or date.today(), days=days, since=since)

    groups, timezone = withings.list_weight_groups(
        withings_session, *withings.window_timestamps(start, end)
    )
    if dump_path is not None:
        write_private(dump_path, dump_text(start, end, timezone, groups))

    readings = [withings.parse_reading(group, timezone) for group in groups]
    # The request had a day of margin for time zones; keep the window exact.
    readings = [r for r in readings if start <= r.local.date() < end]
    decisions = plan_rows(readings, index, cfg.unit_label)
    new_rows = [d.row for d in decisions if d.row is not None]
    if new_rows and not dry_run:
        sheets.append_rows(sheets_session, spreadsheet_id, cfg.sheet_name, new_rows)
    return SyncResult(
        start=start,
        end=end,
        fetched=len(readings),
        decisions=decisions,
        appended=0 if dry_run else len(new_rows),
        existing_rows=index.rows,
        unparsed_rows=index.unparsed,
    )
