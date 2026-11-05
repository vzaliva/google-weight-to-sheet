"""OAuth for the Health API and the Sheets API, with one token for each.

The Health API rejects a token that also carries a scope outside its own: it
answers 403 DISALLOWED_OAUTH_SCOPES. So the two APIs cannot share a token. Each
gets its own consent and its own token file, both made with the same OAuth
client.

The browser consent happens with `authorize` on a machine that has a browser.
The token files are then copied to wherever the sync runs; `load_credentials`
refreshes them from there without any interaction.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .errors import AuthError

HEALTH_SCOPE = (
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
)
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


@dataclass(frozen=True)
class Service:
    name: str
    scopes: tuple[str, ...]
    token_file: str


HEALTH = Service("health", (HEALTH_SCOPE,), "health_token.json")
SHEETS = Service("sheets", (SHEETS_SCOPE,), "sheets_token.json")
SERVICES = {service.name: service for service in (HEALTH, SHEETS)}


def token_path(cfg: Config, service: Service) -> Path:
    return cfg.config_dir / service.token_file


def write_private(path: Path, text: str) -> None:
    """Write a file readable only by its owner, replacing any existing one atomically."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def save_credentials(cfg: Config, service: Service, creds: Any) -> None:
    write_private(token_path(cfg, service), creds.to_json())


def authorize(
    cfg: Config, service: Service, *, port: int = 0, open_browser: bool = True
) -> Any:
    """Run the browser consent flow for one service and store the resulting token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not cfg.client_secret_path.exists():
        raise AuthError(
            f"missing {cfg.client_secret_path}: create a Desktop-app OAuth client in "
            "the Google Cloud console and save its JSON there"
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(cfg.client_secret_path), list(service.scopes)
    )
    # prompt=consent makes Google return a refresh token even on a repeat grant.
    creds = flow.run_local_server(
        port=port,
        open_browser=open_browser,
        prompt="consent",
        authorization_prompt_message=(
            f"Open this URL in a browser to authorise {service.name} access:\n{{url}}\n"
        ),
    )
    save_credentials(cfg, service, creds)
    return creds


def load_credentials(cfg: Config, service: Service) -> Any:
    """Load a stored token, refreshing it when it has expired."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(cfg, service)
    if not path.exists():
        raise AuthError(
            f"no {service.name} token at {path}: run `google-weight-to-sheet auth "
            f"{service.name}` on a machine with a browser and copy the file here"
        )
    creds = Credentials.from_authorized_user_file(str(path), list(service.scopes))
    if not creds.valid:
        if not creds.refresh_token:
            raise AuthError(
                f"the {service.name} token has no refresh token: "
                f"run `auth {service.name}` again"
            )
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AuthError(
                f"could not refresh the {service.name} token ({exc}): "
                f"run `auth {service.name}` again"
            ) from exc
        save_credentials(cfg, service, creds)
    return creds


def make_session(creds: Any) -> Any:
    """An HTTP session that adds the bearer token and refreshes it on a 401."""
    from google.auth.transport.requests import AuthorizedSession

    return AuthorizedSession(creds)
