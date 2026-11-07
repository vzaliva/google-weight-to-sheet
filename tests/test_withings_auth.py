import json
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import FakeResponse, FakeSession

from google_weight_to_sheet import withings_auth
from google_weight_to_sheet.config import Config
from google_weight_to_sheet.errors import AuthError

NOW = 1_762_500_000


def write_client(tmp_path, **extra):
    data = {"client_id": "cid", "client_secret": "csecret", **extra}
    (tmp_path / "withings_client.json").write_text(json.dumps(data))
    return Config(config_dir=tmp_path)


def token_answer(access="new-access", refresh="new-refresh", userid=363):
    return {
        "status": 0,
        "body": {
            "userid": userid,
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": 10800,
            "scope": "user.metrics",
            "token_type": "Bearer",
        },
    }


def stored_token(**overrides):
    token = {
        "client_id": "cid",
        "client_secret": "csecret",
        "userid": "363",
        "scope": "user.metrics",
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "expires_at": NOW + 3600,
    }
    return {**token, **overrides}


# --- the client file ----------------------------------------------------------


def test_missing_client_file_is_explained(tmp_path):
    with pytest.raises(AuthError, match="withings_client.json.*developer dashboard"):
        withings_auth.load_client(Config(config_dir=tmp_path))


def test_client_file_defaults_the_redirect_uri(tmp_path):
    client = withings_auth.load_client(write_client(tmp_path))
    assert client == withings_auth.Client("cid", "csecret", "http://localhost:8085/callback")


def test_client_file_without_a_secret_is_rejected(tmp_path):
    (tmp_path / "withings_client.json").write_text('{"client_id": "cid"}')
    with pytest.raises(AuthError, match="client_secret"):
        withings_auth.load_client(Config(config_dir=tmp_path))


# --- consent -------------------------------------------------------------------


def test_authorization_url_asks_only_for_metrics():
    client = withings_auth.Client("cid", "csecret", "https://example.org/cb")
    url = withings_auth.authorization_url(client, "st")
    assert url.startswith("https://account.withings.com/oauth2_user/authorize2?")
    assert parse_qs(urlsplit(url).query) == {
        "response_type": ["code"],
        "client_id": ["cid"],
        "scope": ["user.metrics"],
        "redirect_uri": ["https://example.org/cb"],
        "state": ["st"],
    }


def test_code_from_redirect_accepts_a_full_address_or_a_path():
    assert withings_auth.code_from_redirect("https://example.org/cb?code=c1&state=st", "st") == "c1"
    assert withings_auth.code_from_redirect("/callback?state=st&code=c2", "st") == "c2"


def test_code_from_redirect_checks_the_state():
    with pytest.raises(AuthError, match="state mismatch"):
        withings_auth.code_from_redirect("https://example.org/cb?code=c&state=other", "st")


def test_code_from_redirect_reports_a_refusal():
    with pytest.raises(AuthError, match="did not grant access: access_denied"):
        withings_auth.code_from_redirect("/cb?error=access_denied&state=st", "st")


def test_exchange_code_posts_the_form_and_records_the_expiry():
    session = FakeSession(FakeResponse(200, token_answer()))
    client = withings_auth.Client("cid", "csecret", "http://localhost:8085/callback")
    token = withings_auth.exchange_code(session, client, "the-code", now=NOW)
    assert session.calls[0]["url"] == "https://wbsapi.withings.net/v2/oauth2"
    assert session.calls[0]["data"] == {
        "action": "requesttoken",
        "grant_type": "authorization_code",
        "client_id": "cid",
        "client_secret": "csecret",
        "code": "the-code",
        "redirect_uri": "http://localhost:8085/callback",
    }
    assert token == stored_token(
        access_token="new-access", refresh_token="new-refresh", expires_at=NOW + 10800
    )


def test_exchange_code_explains_a_refused_code():
    session = FakeSession(FakeResponse(200, {"status": 503, "error": "Invalid Params"}))
    client = withings_auth.Client("cid", "csecret")
    with pytest.raises(AuthError, match="30 seconds"):
        withings_auth.exchange_code(session, client, "late", now=NOW)


def test_authorize_with_a_pasted_address_stores_the_token(tmp_path, monkeypatch):
    cfg = write_client(tmp_path, redirect_uri="https://example.org/cb")
    monkeypatch.setattr(withings_auth.secrets, "token_urlsafe", lambda n: "st")
    session = FakeSession(FakeResponse(200, token_answer()))
    token = withings_auth.authorize(
        cfg,
        open_browser=False,
        http=session,
        prompt=lambda text: "https://example.org/cb?code=c1&state=st",
    )
    assert session.calls[0]["data"]["code"] == "c1"
    assert json.loads((tmp_path / "withings_token.json").read_text()) == token
    assert (tmp_path / "withings_token.json").stat().st_mode & 0o777 == 0o600


# --- using the stored token -----------------------------------------------------


def test_missing_token_names_the_command(tmp_path):
    with pytest.raises(AuthError, match=r"no withings token .* auth withings"):
        withings_auth.open_session(Config(config_dir=tmp_path), now=NOW)


def test_a_broken_token_file_says_to_authorise_again(tmp_path):
    (tmp_path / "withings_token.json").write_text('{"access_token": "a"}')
    with pytest.raises(AuthError, match="auth withings"):
        withings_auth.open_session(Config(config_dir=tmp_path), now=NOW)


def test_a_fresh_token_is_used_without_refreshing(tmp_path):
    cfg = Config(config_dir=tmp_path)
    withings_auth.save_token(cfg, stored_token())
    session = withings_auth.open_session(cfg, http=FakeSession(), now=NOW)
    assert session.headers["Authorization"] == "Bearer old-access"


def test_an_expiring_token_is_refreshed_and_the_new_one_saved(tmp_path):
    cfg = Config(config_dir=tmp_path)
    withings_auth.save_token(cfg, stored_token(expires_at=NOW + 60))
    http = FakeSession(FakeResponse(200, token_answer(userid=None)))
    session = withings_auth.open_session(cfg, http=http, now=NOW)

    assert http.calls[0]["data"] == {
        "action": "requesttoken",
        "grant_type": "refresh_token",
        "client_id": "cid",
        "client_secret": "csecret",
        "refresh_token": "old-refresh",
    }
    assert session.headers["Authorization"] == "Bearer new-access"
    # The old refresh token stops working once the new access token is used.
    saved = json.loads((tmp_path / "withings_token.json").read_text())
    assert saved["refresh_token"] == "new-refresh"
    assert saved["expires_at"] == NOW + 10800
    assert saved["userid"] == "363"  # kept when the refresh answer leaves it out
    assert (tmp_path / "withings_token.json").stat().st_mode & 0o777 == 0o600


def test_a_failed_refresh_keeps_the_file_and_says_to_authorise_again(tmp_path):
    cfg = Config(config_dir=tmp_path)
    withings_auth.save_token(cfg, stored_token(expires_at=NOW - 1))
    before = (tmp_path / "withings_token.json").read_text()
    http = FakeSession(FakeResponse(200, {"status": 503, "error": "invalid refresh_token"}))
    with pytest.raises(AuthError, match="could not refresh the withings token.*auth withings"):
        withings_auth.open_session(cfg, http=http, now=NOW)
    assert (tmp_path / "withings_token.json").read_text() == before
