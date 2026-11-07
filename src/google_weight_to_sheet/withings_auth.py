"""OAuth for the Withings API.

Withings uses OAuth 2.0 loosely: the token endpoint wants an extra
`action=requesttoken` and answers in the same status envelope as every other
Withings call, so a generic OAuth library does not fit. The flow is small
enough to do by hand.

Three Withings rules shape this module:

- The redirect URI must match one registered for the app exactly. A plain
  `http://localhost:PORT/...` URI is allowed, with the app limited to 10 users,
  which is plenty for personal use. For such a URI the redirect is caught by a
  one-shot local web server; for any other URI the user pastes the address the
  browser ended up on.
- The authorization code is valid for 30 seconds, so it is exchanged at once.
- Every refresh returns a new refresh token, and the old one stops working once
  the new access token is used (or 8 hours later). So the new token is saved
  before it is used, and the token file has to live on one machine only: a copy
  left behind elsewhere goes stale the first time either copy is refreshed.
"""

from __future__ import annotations

import json
import secrets
import sys
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, parse_qs, urlencode, urlsplit

import requests

from . import withings
from .auth import write_private
from .config import Config
from .errors import AuthError, WithingsError

AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = f"{withings.API_URL}/v2/oauth2"
SCOPE = "user.metrics"
CLIENT_FILE = "withings_client.json"
TOKEN_FILE = "withings_token.json"
DEFAULT_REDIRECT_URI = "http://localhost:8085/callback"
# Refresh a little early so the token cannot expire in the middle of a run.
EXPIRY_MARGIN_SECONDS = 300
_LOCAL_HOSTS = ("localhost", "127.0.0.1")


@dataclass(frozen=True)
class Client:
    client_id: str
    client_secret: str
    redirect_uri: str = DEFAULT_REDIRECT_URI


def client_path(cfg: Config) -> Path:
    return cfg.config_dir / CLIENT_FILE


def token_path(cfg: Config) -> Path:
    return cfg.config_dir / TOKEN_FILE


def load_client(cfg: Config) -> Client:
    """The app's credentials from the Withings developer dashboard."""
    path = client_path(cfg)
    if not path.exists():
        raise AuthError(
            f"missing {path}: create an application in the Withings developer "
            "dashboard and save its client id and secret there (see the README)"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Client(
            client_id=str(data["client_id"]),
            client_secret=str(data["client_secret"]),
            redirect_uri=str(data.get("redirect_uri") or DEFAULT_REDIRECT_URI),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AuthError(
            f"{path}: expected a JSON object with client_id and client_secret ({exc})"
        ) from exc


def authorization_url(client: Client, state: str) -> str:
    query = {
        "response_type": "code",
        "client_id": client.client_id,
        "scope": SCOPE,
        "redirect_uri": client.redirect_uri,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def code_from_redirect(address: str, state: str) -> str:
    """The authorization code from the address Withings redirected the browser to."""
    query = parse_qs(urlsplit(address.strip()).query)
    if "error" in query:
        raise AuthError(f"Withings did not grant access: {query['error'][0]}")
    if query.get("state", [""])[0] != state:
        raise AuthError(
            "the redirect does not belong to this authorisation (state mismatch); "
            "run `auth withings` again"
        )
    code = query.get("code", [""])[0]
    if not code:
        raise AuthError("the redirect address has no authorization code in it")
    return code


def _token_record(
    client_id: str, client_secret: str, body: dict[str, Any], now: float
) -> dict[str, Any]:
    try:
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "userid": str(body.get("userid") or ""),
            "scope": body.get("scope") or SCOPE,
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "expires_at": int(now) + int(body.get("expires_in") or 10800),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise AuthError(f"Withings answered the token request without a token ({exc})") from exc


def exchange_code(
    session: Any, client: Client, code: str, *, now: float | None = None
) -> dict[str, Any]:
    """Trade an authorization code for an access and a refresh token."""
    try:
        body = withings.call(
            session,
            TOKEN_URL,
            {
                "action": "requesttoken",
                "grant_type": "authorization_code",
                "client_id": client.client_id,
                "client_secret": client.client_secret,
                "code": code,
                "redirect_uri": client.redirect_uri,
            },
        )
    except WithingsError as exc:
        raise AuthError(
            f"Withings refused the authorization code ({exc}). The code is valid for "
            "30 seconds and the redirect URI must match the registered one exactly"
        ) from exc
    now = time.time() if now is None else now
    return _token_record(client.client_id, client.client_secret, body, now)


def refresh(session: Any, token: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """A new token record; the refresh token in it replaces the old one."""
    try:
        body = withings.call(
            session,
            TOKEN_URL,
            {
                "action": "requesttoken",
                "grant_type": "refresh_token",
                "client_id": token["client_id"],
                "client_secret": token["client_secret"],
                "refresh_token": token["refresh_token"],
            },
        )
    except WithingsError as exc:
        raise AuthError(
            f"could not refresh the withings token ({exc}). If it was revoked, has "
            "expired, or a copy of the token file was refreshed on another machine, "
            "run `auth withings` again"
        ) from exc
    now = time.time() if now is None else now
    record = _token_record(token["client_id"], token["client_secret"], body, now)
    record["userid"] = record["userid"] or token.get("userid", "")
    return record


def save_token(cfg: Config, token: dict[str, Any]) -> None:
    write_private(token_path(cfg), json.dumps(token, indent=2) + "\n")


def load_token(cfg: Config) -> dict[str, Any]:
    path = token_path(cfg)
    if not path.exists():
        raise AuthError(
            f"no withings token at {path}: run `google-weight-to-sheet auth withings` "
            "on a machine with a browser and move the file here"
        )
    try:
        token = json.loads(path.read_text(encoding="utf-8"))
        for key in ("client_id", "client_secret", "access_token", "refresh_token"):
            if not token.get(key):
                raise KeyError(key)
        token["expires_at"] = int(token.get("expires_at") or 0)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise AuthError(f"{path} is not a usable token ({exc}): run `auth withings` again") from exc
    return token


def open_session(cfg: Config, *, http: Any = None, now: float | None = None) -> Any:
    """An HTTP session for the Withings API, refreshing the token when it is due."""
    now = time.time() if now is None else now
    token = load_token(cfg)
    if token["expires_at"] - EXPIRY_MARGIN_SECONDS <= now:
        token = refresh(http or requests.Session(), token, now=now)
        # Save before the new access token is used: from that moment on only
        # the new refresh token works.
        save_token(cfg, token)
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token['access_token']}"
    return session


def _wait_for_redirect(target: SplitResult) -> str:
    """Serve the redirect URI on this machine until the browser arrives at it.

    Returns the path and query the browser asked for.
    """
    received: list[str] = []
    expected_path = target.path or "/"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if urlsplit(self.path).path != expected_path:
                self.send_error(404)
                return
            received.append(self.path)
            text = b"Withings answered. You can close this tab and go back to the terminal.\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(text)))
            self.end_headers()
            self.wfile.write(text)

        def log_message(self, *args: Any) -> None:
            pass  # keep the terminal quiet

    try:
        server = HTTPServer((target.hostname or "localhost", target.port or 80), Handler)
    except OSError as exc:
        raise AuthError(
            f"cannot listen on {target.netloc} for the Withings redirect ({exc})"
        ) from exc
    with server:
        while not received:
            server.handle_request()
    return received[0]


def authorize(
    cfg: Config,
    *,
    open_browser: bool = True,
    http: Any = None,
    prompt: Callable[[str], str] = input,
) -> dict[str, Any]:
    """Run the browser consent, store the token and return it."""
    client = load_client(cfg)
    state = secrets.token_urlsafe(16)
    url = authorization_url(client, state)
    target = urlsplit(client.redirect_uri)
    catch_locally = target.scheme == "http" and target.hostname in _LOCAL_HOSTS

    print(f"Open this URL in a browser to authorise Withings access:\n{url}\n")
    if open_browser:
        webbrowser.open(url)
    if catch_locally:
        print(f"Waiting for the browser to come back to {client.redirect_uri} ...")
        sys.stdout.flush()
        address = _wait_for_redirect(target)
    else:
        print(
            f"After you allow access the browser goes to {client.redirect_uri}?code=...\n"
            "Copy that whole address from the address bar and paste it here. Be quick: "
            "Withings accepts the code for only 30 seconds."
        )
        address = prompt("Address: ")
    code = code_from_redirect(address, state)
    token = exchange_code(http or requests.Session(), client, code)
    save_token(cfg, token)
    return token
