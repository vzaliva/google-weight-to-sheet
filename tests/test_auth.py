import pytest

from google_weight_to_sheet import auth
from google_weight_to_sheet.config import Config
from google_weight_to_sheet.errors import AuthError


def test_the_sheets_token_has_its_own_file_and_only_the_sheets_scope(tmp_path):
    assert auth.SHEETS.scopes == (auth.SHEETS_SCOPE,)
    assert auth.token_path(Config(config_dir=tmp_path), auth.SHEETS) == tmp_path / "sheets_token.json"


def test_missing_token_names_the_service_and_the_command(tmp_path):
    with pytest.raises(AuthError, match=r"no sheets token .* auth sheets"):
        auth.load_credentials(Config(config_dir=tmp_path), auth.SHEETS)


def test_missing_client_file_is_explained(tmp_path):
    with pytest.raises(AuthError, match="client_secret.json"):
        auth.authorize(Config(config_dir=tmp_path), auth.SHEETS)


def test_write_private_creates_an_owner_only_file(tmp_path):
    target = tmp_path / "sub" / "t.json"
    auth.write_private(target, "{}")
    assert target.read_text() == "{}"
    assert target.stat().st_mode & 0o777 == 0o600
