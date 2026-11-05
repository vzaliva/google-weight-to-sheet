"""JSON over HTTP with a short retry loop for transient failures."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .errors import ApiError

TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 4
# Google asks clients to back off on these and try again.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def request_json(
    session: Any,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Send a request and return the decoded JSON body.

    Retries rate limits and server errors with exponential backoff and raises
    ApiError for any other failure status.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = session.request(
            method, url, params=params, json=json, timeout=TIMEOUT_SECONDS
        )
        if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
            sleep(2**attempt)
            continue
        if response.status_code >= 400:
            raise ApiError(response.status_code, url, response.text)
        return response.json()
    raise AssertionError("unreachable")  # pragma: no cover
