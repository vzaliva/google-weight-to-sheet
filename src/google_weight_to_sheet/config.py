"""Configuration: a TOML file in the config directory, overridden by environment variables.

Nothing here is secret. The OAuth client file and the token live next to the
config file but are handled by the auth module.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError

APP_NAME = "google-weight-to-sheet"
DEFAULT_SHEET_NAME = "Weight"
DEFAULT_UNIT_LABEL = "pounds"


@dataclass(frozen=True)
class Config:
    config_dir: Path
    spreadsheet_id: str | None = None
    sheet_name: str = DEFAULT_SHEET_NAME
    unit_label: str = DEFAULT_UNIT_LABEL

    @property
    def client_secret_path(self) -> Path:
        return self.config_dir / "client_secret.json"

    def require_spreadsheet_id(self) -> str:
        if not self.spreadsheet_id:
            raise ConfigError(
                "no spreadsheet configured: set spreadsheet_id in "
                f"{self.config_dir / 'config.toml'} or GWTS_SPREADSHEET_ID in the environment"
            )
        return self.spreadsheet_id


def default_config_dir(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def load_config(
    config_dir: Path | None = None, env: Mapping[str, str] | None = None
) -> Config:
    env = os.environ if env is None else env
    if config_dir is None:
        override = env.get("GWTS_CONFIG_DIR")
        config_dir = Path(override) if override else default_config_dir(env)

    values: dict[str, object] = {}
    path = config_dir / "config.toml"
    if path.exists():
        try:
            values = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc

    def pick(key: str, env_name: str, default: str | None) -> str | None:
        value = env.get(env_name) or values.get(key) or default
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"{path}: {key} must be a string")
        return value

    return Config(
        config_dir=config_dir,
        spreadsheet_id=pick("spreadsheet_id", "GWTS_SPREADSHEET_ID", None),
        sheet_name=pick("sheet_name", "GWTS_SHEET_NAME", DEFAULT_SHEET_NAME)
        or DEFAULT_SHEET_NAME,
        unit_label=pick("unit_label", "GWTS_UNIT_LABEL", DEFAULT_UNIT_LABEL)
        or DEFAULT_UNIT_LABEL,
    )
