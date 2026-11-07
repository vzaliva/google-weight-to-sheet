"""Exceptions raised by the sync.

Everything derives from SyncError so the command line can report expected
failures as a one-line message and still show a traceback for real bugs.
"""

from __future__ import annotations


class SyncError(Exception):
    """Base class for expected failures."""


class ConfigError(SyncError):
    """Missing or invalid configuration."""


class AuthError(SyncError):
    """No usable credentials; the user has to authorise again."""


class DataError(SyncError):
    """The API returned data in a shape this tool does not understand."""


class SheetError(SyncError):
    """The target spreadsheet or tab could not be used."""


class ApiError(SyncError):
    """An HTTP error response."""

    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status} from {url}: {body[:500]}")


class WithingsError(SyncError):
    """A Withings call that answered with a non-zero status.

    Withings reports most errors with HTTP 200 and the real outcome in the body,
    so these never show up as an ApiError.
    """

    def __init__(self, status: object, url: str, error: str) -> None:
        self.status = status
        self.url = url
        self.error = error
        super().__init__(f"Withings status {status} from {url}: {error or 'no details'}")
