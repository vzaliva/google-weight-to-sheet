from datetime import date, datetime

import pytest

from google_weight_to_sheet import cli, sync, withings
from google_weight_to_sheet.config import Config


def test_missing_spreadsheet_is_a_clean_error(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GWTS_SPREADSHEET_ID", raising=False)
    monkeypatch.setattr(cli.withings_auth, "open_session", lambda cfg: object())
    monkeypatch.setattr(cli, "_open_sheets_session", lambda cfg: object())
    assert cli.main(["--config-dir", str(tmp_path), "sync", "--dry-run"]) == 1
    assert "error: no spreadsheet configured" in capsys.readouterr().err


def test_missing_token_is_a_clean_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GWTS_SPREADSHEET_ID", "x")
    assert cli.main(["--config-dir", str(tmp_path), "sync"]) == 1
    assert "no withings token" in capsys.readouterr().err


def test_days_and_since_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["sync", "--days", "3", "--since", "2026-11-01"])


def test_since_must_be_an_iso_date():
    args = cli.build_parser().parse_args(["sync", "--since", "2026-11-01"])
    assert args.since == date(2026, 11, 1)


def make_result():
    decisions = [
        sync.Decision(withings.Reading("a", datetime(2026, 11, 2, 7, 1), 79400), "skip-match"),
        sync.Decision(
            withings.Reading("b", datetime(2026, 11, 4, 7, 30), 79000),
            "new",
            ["November 4, 2026 at 07:30AM", 174.2, "pounds", 24.38, "b"],
        ),
        sync.Decision(
            withings.Reading("c", datetime(2026, 11, 4, 19, 0), 61000, attrib=10009),
            "skip-unattributed",
        ),
    ]
    return sync.SyncResult(date(2026, 11, 1), date(2026, 11, 7), 3, decisions, 1, 5, 0, 1.8)


def test_report_lists_new_rows_but_hides_skips_by_default():
    text = cli.format_report(make_result(), Config(config_dir="."), dry_run=False, verbose=False)
    assert "NEW" in text and "BMI 24.38  id=b" in text
    assert "no height" not in text
    assert "id=a" not in text
    assert "fetched=3 new=1 skipped=2 appended=1" in text


def test_report_always_shows_an_unattributed_skip_with_its_attrib():
    text = cli.format_report(make_result(), Config(config_dir="."), dry_run=False, verbose=False)
    assert "id=c  (Withings does not say this weigh-in is yours, attrib=10009;" in text


def test_report_notes_a_missing_height():
    result = make_result()
    result.height_m = None
    text = cli.format_report(result, Config(config_dir="."), dry_run=False, verbose=False)
    assert "has no height, so the BMI column is left empty" in text


def test_dry_run_report_shows_skips_with_the_reason():
    text = cli.format_report(make_result(), Config(config_dir="."), dry_run=True, verbose=False)
    assert "id=a  (already in the sheet: same date and weight)" in text
    assert "(dry run, nothing written)" in text


def test_auth_defaults_to_both_services():
    assert cli.build_parser().parse_args(["auth"]).service == "all"
    assert cli.build_parser().parse_args(["auth", "withings"]).service == "withings"
    assert cli.build_parser().parse_args(["auth", "sheets"]).service == "sheets"


def test_auth_rejects_an_unknown_service():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["auth", "health"])
