"""Reading weight measurements from the Google Health API.

Request and response shapes follow the public reference:
  https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from .errors import DataError
from .http import request_json

BASE_URL = "https://health.googleapis.com/v4"


@dataclass(frozen=True)
class Reading:
    point_id: str
    local: datetime  # wall-clock time where the user was, without a time zone
    grams: float


def civil_filter(start: date, end: date) -> str:
    """Filter for measurements on civil dates in [start, end)."""
    return (
        f'weight.sample_time.civil_time >= "{start.isoformat()}" '
        f'AND weight.sample_time.civil_time < "{end.isoformat()}"'
    )


def list_weight_points(
    session: Any, start: date, end: date, *, page_size: int = 1000
) -> Iterator[dict[str, Any]]:
    """Yield raw weight data points with civil dates in [start, end), following pages."""
    url = f"{BASE_URL}/users/me/dataTypes/weight/dataPoints"
    params: dict[str, Any] = {
        "filter": civil_filter(start, end),
        "pageSize": page_size,
    }
    while True:
        body = request_json(session, "GET", url, params=params)
        yield from body.get("dataPoints", [])
        token = body.get("nextPageToken")
        if not token:
            return
        params = {**params, "pageToken": token}


def get_identity(session: Any) -> dict[str, Any]:
    """The Health API's view of the signed-in user; fails if the account is not linked."""
    return request_json(session, "GET", f"{BASE_URL}/users/me/identity")


def _duration_seconds(value: Any) -> float:
    """Parse a protobuf JSON duration such as "-28800s"."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.endswith("s"):
        return float(value[:-1])
    raise ValueError(f"unrecognised duration {value!r}")


def _local_time(sample_time: dict[str, Any]) -> datetime:
    civil = sample_time.get("civilTime")
    if civil and "date" in civil:
        day = civil["date"]
        # Protobuf JSON leaves out zero-valued fields, so midnight may have no time at all.
        clock = civil.get("time") or {}
        return datetime(
            int(day["year"]),
            int(day["month"]),
            int(day["day"]),
            int(clock.get("hours", 0)),
            int(clock.get("minutes", 0)),
            int(clock.get("seconds", 0)),
        )
    physical = sample_time.get("physicalTime")
    offset = sample_time.get("utcOffset")
    if physical and offset is not None:
        utc = datetime.fromisoformat(physical)  # 3.11+ accepts the trailing "Z"
        shifted = utc + timedelta(seconds=_duration_seconds(offset))
        return shifted.replace(tzinfo=None)
    raise ValueError("no civilTime, and no physicalTime with utcOffset")


def parse_reading(point: dict[str, Any]) -> Reading:
    """Turn a raw weight data point into a Reading."""
    name = point.get("name", "<unnamed>")
    try:
        weight = point["weight"]
        return Reading(
            point_id=name.rsplit("/", 1)[-1],
            local=_local_time(weight["sampleTime"]),
            grams=float(weight["weightGrams"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError(
            f"cannot read weight data point {name}: {type(exc).__name__}: {exc}"
        ) from exc
