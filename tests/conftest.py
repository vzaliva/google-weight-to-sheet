"""Shared fakes. Tests never touch the network."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: str | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self) -> Any:
        return self._payload


class FakeSession:
    """Answers requests from a queue of responses, or from a handler function."""

    def __init__(
        self,
        *responses: FakeResponse,
        handler: Callable[[str, str, dict | None, Any], FakeResponse] | None = None,
    ):
        self.queue = list(responses)
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def request(self, method, url, params=None, json=None, timeout=None):
        self.calls.append(
            {"method": method, "url": url, "params": dict(params or {}), "json": json}
        )
        if self.handler is not None:
            return self.handler(method, url, params, json)
        return self.queue.pop(0)


def make_point(
    point_id: str,
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    grams: float,
    *,
    utc_offset: str = "-28800s",
) -> dict[str, Any]:
    """A weight data point shaped like the Health API reference describes it."""
    clock: dict[str, int] = {}
    if hour:
        clock["hours"] = hour
    if minute:
        clock["minutes"] = minute
    civil: dict[str, Any] = {"date": {"year": year, "month": month, "day": day}}
    if clock:
        civil["time"] = clock
    return {
        "name": f"users/123/dataTypes/weight/dataPoints/{point_id}",
        "weight": {
            "sampleTime": {
                "physicalTime": f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:00Z",
                "utcOffset": utc_offset,
                "civilTime": civil,
            },
            "weightGrams": grams,
        },
    }


@pytest.fixture
def make_session():
    return FakeSession
