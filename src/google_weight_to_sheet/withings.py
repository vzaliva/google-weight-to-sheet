"""Reading weight measurements from the Withings API.

Request and response shapes follow the public reference for `getmeas`:
  https://developer.withings.com/api-reference/#tag/measure/operation/measure-getmeas

Every Withings call is a form-encoded POST that answers HTTP 200 with an
envelope, {"status": 0, "body": {...}}. A non-zero status is the error, so the
HTTP status alone says nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from .errors import DataError, WithingsError
from .http import request_json

API_URL = "https://wbsapi.withings.net"
MEASURE_URL = f"{API_URL}/measure"

WEIGHT = 1  # measure type: weight, in kg
REAL_MEASURES = 1  # category: real measurements (2 would be the user's goals)
# attrib: taken by a scale, but it may be another person's weigh-in. The scale
# could not tell who stood on it and Withings has not had it confirmed.
AMBIGUOUS = 1


@dataclass(frozen=True)
class Reading:
    point_id: str
    local: datetime  # wall-clock time in the account's time zone, without a zone
    grams: float
    ambiguous: bool = False


def call(session: Any, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """POST one Withings action and return the body of a successful answer."""
    payload = request_json(session, "POST", url, data=params)
    if not isinstance(payload, dict):
        raise WithingsError(None, url, f"unexpected answer {payload!r:.200}")
    status = payload.get("status")
    if status != 0:
        raise WithingsError(status, url, str(payload.get("error") or ""))
    return payload.get("body") or {}


def window_timestamps(start: date, end: date) -> tuple[int, int]:
    """Unix times that cover the civil dates [start, end) in any time zone.

    Withings filters by instant, while the window is in local dates, and the
    account's zone is only known from the answer. A day of margin on each side
    covers UTC-12 to UTC+14. The caller drops readings outside the window.
    """

    def midnight_utc(day: date) -> int:
        return int(datetime.combine(day, time.min, tzinfo=UTC).timestamp())

    return midnight_utc(start - timedelta(days=1)), midnight_utc(end + timedelta(days=1))


def _has_weight(group: dict[str, Any]) -> bool:
    return any(m.get("type") == WEIGHT for m in group.get("measures") or [])


def list_weight_groups(
    session: Any, startdate: int, enddate: int
) -> tuple[list[dict[str, Any]], str | None]:
    """Raw measure groups holding a weight, taken between two Unix times.

    Also returns the account's time zone from the answer, which the groups'
    timestamps need to become local times. Follows `more`/`offset` paging.
    """
    params: dict[str, Any] = {
        "action": "getmeas",
        "meastype": WEIGHT,
        "category": REAL_MEASURES,
        "startdate": startdate,
        "enddate": enddate,
    }
    groups: list[dict[str, Any]] = []
    timezone: str | None = None
    while True:
        body = call(session, MEASURE_URL, params)
        timezone = timezone or body.get("timezone")
        groups.extend(g for g in body.get("measuregrps") or [] if _has_weight(g))
        if not body.get("more"):
            return groups, timezone
        if body.get("offset") == params.get("offset"):
            raise DataError("Withings kept answering more=1 with the same offset")
        params = {**params, "offset": body.get("offset")}


def _local_time(timestamp: int, zone_name: str | None) -> datetime:
    if zone_name:
        return datetime.fromtimestamp(timestamp, ZoneInfo(zone_name)).replace(tzinfo=None)
    # No zone in the answer: the best guess is this machine's own.
    return datetime.fromtimestamp(timestamp)


def _grams(measure: dict[str, Any]) -> float:
    # The weight is value * 10^unit kg, e.g. 79400 and -3. Decimal keeps the
    # shift exact, so 79400 * 10^-3 kg is exactly 79400 g, not 79400.00000000001.
    return float(Decimal(int(measure["value"])).scaleb(int(measure["unit"]) + 3))


def parse_reading(group: dict[str, Any], timezone: str | None) -> Reading:
    """Turn a raw measure group into a Reading.

    `timezone` is the account's zone from the same answer. A group's own
    `timezone` wins if it has one; the reference does not promise either.
    """
    grpid = group.get("grpid", "<no grpid>")
    try:
        weight = next(m for m in group["measures"] if m.get("type") == WEIGHT)
        return Reading(
            point_id=str(int(group["grpid"])),
            local=_local_time(int(group["date"]), group.get("timezone") or timezone),
            grams=_grams(weight),
            ambiguous=group.get("attrib") == AMBIGUOUS,
        )
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        # ZoneInfoNotFoundError is a KeyError, so an unknown zone lands here too.
        raise DataError(
            f"cannot read Withings measure group {grpid}: {type(exc).__name__}: {exc}"
        ) from exc
