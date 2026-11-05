"""OAuth for the Health API and the Sheets API, with one token for both.

The one-time browser consent happens with `authorize` on a machine that has a
browser. The resulting token file is then copied to wherever the sync runs;
`load_credentials` refreshes it from there without any interaction.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import Config
from .errors import AuthError

HEALTH_SCOPE = (
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
)
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SCOPES = (HEALTH_SCOPE, SHEETS_SCOPE)


def write_private(path: Path, text: str) -> None:
    """Write a file readable only by its owner, replacing any existing one atomically."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def save_credentials(cfg: Config, creds: Any) -> None:
    write_private(cfg.token_path, creds.to_json())


def authorize(cfg: Config, *, port: int = 0, open_browser: bool = True) -> Any:
    """Run the browser consent flow and store the resulting token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not cfg.client_secret_path.exists():
        raise AuthError(
            f"missing {cfg.client_secret_path}: create a Desktop-app OAuth client in "
            "the Google Cloud console and save its JSON there"
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(cfg.client_secret_path), list(SCOPES)
    )
    # prompt=consent makes Google return a refresh token even on a repeat grant.
    creds = flow.run_local_server(
        port=port,
        open_browser=open_browser,
        prompt="consent",
        authorization_prompt_message="Open this URL in a browser to authorise:\n{url}\n",
    )
    save_credentials(cfg, creds)
    return creds


def load_credentials(cfg: Config) -> Any:
    """Load the stored token, refreshing it when it has expired."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not cfg.token_path.exists():
        raise AuthError(
            f"no token at {cfg.token_path}: run `google-weight-to-sheet auth` on a "
            "machine with a browser and copy token.json here"
        )
    creds = Credentials.from_authorized_user_file(str(cfg.token_path), list(SCOPES))
    if not creds.valid:
        if not creds.refresh_token:
            raise AuthError("the stored token has no refresh token: run `auth` again")
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AuthError(
                f"could not refresh the token ({exc}): run `auth` again"
            ) from exc
        save_credentials(cfg, creds)
    return creds


def make_session(creds: Any) -> Any:
    """An HTTP session that adds the bearer token and refreshes it on a 401."""
    from google.auth.transport.requests import AuthorizedSession

    return AuthorizedSession(creds)
