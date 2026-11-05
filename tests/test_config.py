from pathlib import Path

import pytest

from google_weight_to_sheet.config import default_config_dir, load_config
from google_weight_to_sheet.errors import ConfigError


def test_defaults_when_there_is_no_file(tmp_path):
    cfg = load_config(tmp_path, env={})
    assert cfg.spreadsheet_id is None
    assert cfg.sheet_name == "Weight"
    assert cfg.unit_label == "pounds"
    assert cfg.token_path == tmp_path / "token.json"
    assert cfg.client_secret_path == tmp_path / "client_secret.json"


def test_reads_the_toml_file(tmp_path):
    (tmp_path / "config.toml").write_text(
        'spreadsheet_id = "abc"\nsheet_name = "Body"\nunit_label = "kg"\n'
    )
    cfg = load_config(tmp_path, env={})
    assert (cfg.spreadsheet_id, cfg.sheet_name, cfg.unit_label) == ("abc", "Body", "kg")


def test_environment_overrides_the_file(tmp_path):
    (tmp_path / "config.toml").write_text('spreadsheet_id = "from-file"\n')
    cfg = load_config(tmp_path, env={"GWTS_SPREADSHEET_ID": "from-env"})
    assert cfg.spreadsheet_id == "from-env"


def test_config_dir_can_come_from_the_environment(tmp_path):
    cfg = load_config(env={"GWTS_CONFIG_DIR": str(tmp_path)})
    assert cfg.config_dir == tmp_path


def test_non_string_value_is_rejected(tmp_path):
    (tmp_path / "config.toml").write_text("spreadsheet_id = 42\n")
    with pytest.raises(ConfigError, match="spreadsheet_id must be a string"):
        load_config(tmp_path, env={})


def test_broken_toml_is_reported_with_the_path(tmp_path):
    (tmp_path / "config.toml").write_text("this is = = not toml")
    with pytest.raises(ConfigError, match="config.toml"):
        load_config(tmp_path, env={})


def test_spreadsheet_id_is_required_when_asked_for(tmp_path):
    cfg = load_config(tmp_path, env={})
    with pytest.raises(ConfigError, match="no spreadsheet configured"):
        cfg.require_spreadsheet_id()


def test_default_dir_follows_xdg_config_home():
    assert default_config_dir({"XDG_CONFIG_HOME": "/x"}) == Path("/x/google-weight-to-sheet")
