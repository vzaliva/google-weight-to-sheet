"""Command line: `auth` once, `check` to look at the raw data, `sync` from cron."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__, auth, sheets, sync, withings, withings_auth
from .config import Config, load_config
from .errors import SyncError

SERVICES = ("withings", "sheets")
_ACTION_TEXT = {
    "new": "NEW ",
    "skip-id": "skip",
    "skip-unattributed": "skip",
    "skip-match": "skip",
}
_ACTION_REASON = {
    "new": "",
    "skip-id": "  (already in the sheet: same id)",
    "skip-unattributed": (
        "  (Withings does not say this weigh-in is yours, attrib={attrib}; if it is, "
        "assign it to yourself in the Withings app)"
    ),
    "skip-match": "  (already in the sheet: same date and weight)",
}
# Shown in the log even without --verbose, because the user has to act on them.
_ALWAYS_SHOWN = frozenset({"new", "skip-unattributed"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="google-weight-to-sheet",
        description="Sync body weight from a Withings scale into a Google Sheet.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="directory with config.toml, the client files and the token files "
        "(default: ~/.config/google-weight-to-sheet)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    p_auth = commands.add_parser(
        "auth", help="authorise in a browser and store the tokens (run where a browser is)"
    )
    p_auth.add_argument(
        "service",
        nargs="?",
        choices=[*SERVICES, "all"],
        default="all",
        help="which API to authorise; each one is a separate consent (default: all)",
    )
    p_auth.add_argument(
        "--no-browser", action="store_true", help="print the URL instead of opening it"
    )
    p_auth.add_argument(
        "--port",
        type=int,
        default=0,
        help="port for the Google redirect (default: any free port); the Withings "
        "port is part of its redirect_uri",
    )

    p_check = commands.add_parser(
        "check", help="verify Withings access and show the newest raw weight measurement"
    )
    p_check.add_argument(
        "--days", type=int, default=30, help="how far back to look (default: 30)"
    )
    p_check.add_argument("--dump", type=Path, help="save the raw API response to this file")

    p_sync = commands.add_parser("sync", help="append new weigh-ins to the sheet")
    p_sync.add_argument(
        "--dry-run", action="store_true", help="show what would be added, write nothing"
    )
    window = p_sync.add_mutually_exclusive_group()
    window.add_argument(
        "--days",
        type=int,
        help="look back this many days instead of starting from the newest row",
    )
    window.add_argument(
        "--since",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="look back to this date instead of starting from the newest row",
    )
    p_sync.add_argument("--dump", type=Path, help="save the raw API response to this file")
    p_sync.add_argument(
        "-v", "--verbose", action="store_true", help="list every reading, not just new ones"
    )
    return parser


def _stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _open_sheets_session(cfg: Config) -> Any:
    return auth.make_session(auth.load_credentials(cfg, auth.SHEETS))


def _fetch_recent(
    session: Any, days: int
) -> tuple[date, date, list[dict[str, Any]], str | None]:
    today = date.today()
    start, end = today - timedelta(days=days), today + timedelta(days=2)
    groups, timezone = withings.list_weight_groups(
        session, *withings.window_timestamps(start, end)
    )
    return start, end, groups, timezone


def _verify_withings(session: Any) -> None:
    _, _, groups, _ = _fetch_recent(session, 30)
    print(f"the Withings token works: {len(groups)} weight measurement(s) in the last 30 days")


def _verify_sheets(cfg: Config, session: Any) -> None:
    if not cfg.spreadsheet_id:
        print("no spreadsheet configured yet, so the Sheets token was not tried")
        return
    print(f"can open the spreadsheet {sheets.get_title(session, cfg.spreadsheet_id)!r}")


def cmd_auth(cfg: Config, args: argparse.Namespace) -> int:
    names = SERVICES if args.service == "all" else (args.service,)
    for name in names:
        if name == "withings":
            token = withings_auth.authorize(cfg, open_browser=not args.no_browser)
            print(
                f"withings token for user {token['userid'] or '<unknown>'} saved to "
                f"{withings_auth.token_path(cfg)}"
            )
            _verify_withings(withings_auth.open_session(cfg))
        else:
            creds = auth.authorize(
                cfg, auth.SHEETS, port=args.port, open_browser=not args.no_browser
            )
            print(f"sheets token saved to {auth.token_path(cfg, auth.SHEETS)}")
            _verify_sheets(cfg, auth.make_session(creds))
    return 0


def cmd_check(cfg: Config, args: argparse.Namespace) -> int:
    session = withings_auth.open_session(cfg)
    print(f"Withings user: {withings_auth.load_token(cfg).get('userid') or '<unknown>'}")
    start, end, groups, timezone = _fetch_recent(session, args.days)
    print(f"{len(groups)} weight measurement(s) since {start}")
    print("account time zone:", timezone or "none given, so this machine's is used")
    height = withings.latest_height(session)
    print("height in the profile:", f"{height} m" if height else "none, so BMI stays empty")
    if args.dump:
        auth.write_private(args.dump, sync.dump_text(start, end, timezone, groups))
        print(f"raw response saved to {args.dump}")
    if groups:
        newest = max(groups, key=lambda group: group.get("date") or 0)
        print("newest measure group:")
        print(json.dumps(newest, indent=2))
        reading = withings.parse_reading(newest, timezone)
        print(
            f"parsed as: {sheets.format_datetime(reading.local)}, "
            f"{sync.grams_to_pounds(reading.grams)} pounds, id {reading.point_id}"
            + ("" if reading.attributed else f" (attrib={reading.attrib}: sync skips it)")
        )
    return 0


def format_report(result: sync.SyncResult, cfg: Config, *, dry_run: bool, verbose: bool) -> str:
    lines = []
    for decision in result.decisions:
        # New rows always go in the log; plain skips only when asked for.
        if decision.action not in _ALWAYS_SHOWN and not (verbose or dry_run):
            continue
        reading = decision.reading
        reason = _ACTION_REASON[decision.action].format(attrib=reading.attrib)
        bmi = f"BMI {decision.row[3]}  " if decision.row and decision.row[3] != "" else ""
        lines.append(
            f"  {_ACTION_TEXT[decision.action]}  "
            f"{sheets.format_datetime(reading.local)}  "
            f"{sync.grams_to_pounds(reading.grams)} {cfg.unit_label}  "
            f"{bmi}id={reading.point_id}{reason}"
        )
    if result.count("new") and result.height_m is None:
        lines.append(
            "note: the Withings profile has no height, so the BMI column is left empty"
        )
    if result.unparsed_rows:
        lines.append(
            f"note: {result.unparsed_rows} row(s) in the sheet have a date this tool cannot "
            "read; they are ignored when looking for duplicates"
        )
    summary = (
        f"{_stamp()} window {result.start}..{result.end} "
        f"fetched={result.fetched} new={result.count('new')} "
        f"skipped={len(result.decisions) - result.count('new')} "
        f"appended={result.appended}"
    )
    if dry_run:
        summary += " (dry run, nothing written)"
    lines.append(summary)
    return "\n".join(lines)


def cmd_sync(cfg: Config, args: argparse.Namespace) -> int:
    withings_session = withings_auth.open_session(cfg)
    sheets_session = _open_sheets_session(cfg)
    result = sync.run_sync(
        withings_session,
        sheets_session,
        cfg,
        dry_run=args.dry_run,
        days=args.days,
        since=args.since,
        dump_path=args.dump,
    )
    print(format_report(result, cfg, dry_run=args.dry_run, verbose=args.verbose))
    return 0


COMMANDS = {"auth": cmd_auth, "check": cmd_check, "sync": cmd_sync}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config_dir)
        return COMMANDS[args.command](cfg, args)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
