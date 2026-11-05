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
    """An HTTP error response from a Google API."""

    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status} from {url}: {body[:500]}")
