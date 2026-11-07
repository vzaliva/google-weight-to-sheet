"""Shared fakes. Tests never touch the network."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

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

    def request(self, method, url, params=None, data=None, json=None, timeout=None):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": dict(params or {}),
                "data": None if data is None else dict(data),
                "json": json,
            }
        )
        if self.handler is not None:
            return self.handler(method, url, params, json)
        return self.queue.pop(0)


ZONE = "America/Los_Angeles"


def make_group(
    grpid: int,
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    grams: int,
    *,
    attrib: int = 0,
    zone: str = ZONE,
) -> dict[str, Any]:
    """A weight measure group shaped like the Withings getmeas reference describes it.

    The time is wall-clock time in `zone`; the group itself only has a Unix time.
    """
    moment = int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(zone)).timestamp())
    return {
        "grpid": grpid,
        "attrib": attrib,
        "date": moment,
        "created": moment + 40,
        "modified": moment + 40,
        "category": 1,
        "deviceid": "892359876fd8805ac45bab078c4828692f0276b1",
        "hash_deviceid": "892359876fd8805ac45bab078c4828692f0276b1",
        "measures": [{"value": grams, "type": 1, "unit": -3}],
        "comment": None,
    }


def measure_answer(
    groups: list[dict[str, Any]], *, zone: str | None = ZONE, more: int = 0, offset: int = 0
) -> dict[str, Any]:
    """A successful getmeas answer around some measure groups."""
    body: dict[str, Any] = {
        "updatetime": 1762500000,
        "measuregrps": groups,
        "more": more,
        "offset": offset,
    }
    if zone is not None:
        body["timezone"] = zone
    return {"status": 0, "body": body}


@pytest.fixture
def make_session():
    return FakeSession
