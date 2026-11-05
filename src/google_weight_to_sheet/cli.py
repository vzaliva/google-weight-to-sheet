"""Command line: `auth` once, `check` to look at the raw data, `sync` from cron."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import __version__, auth, health, sheets, sync
from .config import Config, load_config
from .errors import ApiError, SyncError

_ACTION_TEXT = {
    "new": "NEW ",
    "skip-id": "skip",
    "skip-match": "skip",
}
_ACTION_REASON = {
    "new": "",
    "skip-id": "  (already in the sheet: same id)",
    "skip-match": "  (already in the sheet: same date and weight)",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="google-weight-to-sheet",
        description="Sync body weight from the Google Health API into a Google Sheet.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="directory with config.toml, client_secret.json and token.json "
        "(default: ~/.config/google-weight-to-sheet)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    p_auth = commands.add_parser(
        "auth", help="authorise in a browser and store the token (run where a browser is)"
    )
    p_auth.add_argument(
        "--no-browser", action="store_true", help="print the URL instead of opening it"
    )
    p_auth.add_argument(
        "--port",
        type=int,
        default=0,
        help="port for the local redirect (default: any free port)",
    )

    p_check = commands.add_parser(
        "check", help="verify access and show the newest raw weight data point"
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


def _open_session(cfg: Config) -> Any:
    return auth.make_session(auth.load_credentials(cfg))


def cmd_auth(cfg: Config, args: argparse.Namespace) -> int:
    creds = auth.authorize(cfg, port=args.port, open_browser=not args.no_browser)
    print(f"token saved to {cfg.token_path}")
    session = auth.make_session(creds)
    try:
        identity = health.get_identity(session)
    except ApiError as exc:
        if "ACCOUNT_NOT_LINKED" in exc.body:
            print(
                "warning: this Google Account is not linked to Google Health yet. Sign in "
                "to the Google Health app with it, then run `check`.",
                file=sys.stderr,
            )
            return 0
        raise
    print(f"signed in to the Health API as {identity.get('healthUserId', '<unknown id>')}")
    return 0


def cmd_check(cfg: Config, args: argparse.Namespace) -> int:
    session = _open_session(cfg)
    identity = health.get_identity(session)
    print(f"Health API user: {identity.get('healthUserId', '<unknown id>')}")
    today = date.today()
    start, end = today - timedelta(days=args.days), today + timedelta(days=2)
    points = list(health.list_weight_points(session, start, end))
    print(f"{len(points)} weight data point(s) from {start} to {today}")
    if args.dump:
        payload = {"start": start.isoformat(), "end": end.isoformat(), "dataPoints": points}
        auth.write_private(args.dump, json.dumps(payload, indent=2) + "\n")
        print(f"raw response saved to {args.dump}")
    if points:
        print("first data point in the response (newest first):")
        print(json.dumps(points[0], indent=2))
        reading = health.parse_reading(points[0])
        print(
            f"parsed as: {sheets.format_datetime(reading.local)}, "
            f"{sync.grams_to_pounds(reading.grams)} pounds, id {reading.point_id}"
        )
    return 0


def format_report(result: sync.SyncResult, cfg: Config, *, dry_run: bool, verbose: bool) -> str:
    lines = []
    for decision in result.decisions:
        # New rows always go in the log; skipped ones only when asked for.
        if decision.action != "new" and not (verbose or dry_run):
            continue
        reading = decision.reading
        lines.append(
            f"  {_ACTION_TEXT[decision.action]}  "
            f"{sheets.format_datetime(reading.local)}  "
            f"{sync.grams_to_pounds(reading.grams)} {cfg.unit_label}  "
            f"id={reading.point_id}{_ACTION_REASON[decision.action]}"
        )
    if result.unparsed_rows:
        lines.append(
            f"note: {result.unparsed_rows} row(s) in the sheet have a date this tool cannot "
            "read; they are ignored when looking for duplicates"
        )
    summary = (
        f"{_stamp()} window {result.start}..{result.end} "
        f"fetched={result.fetched} new={result.count('new')} "
        f"skipped={result.count('skip-id') + result.count('skip-match')} "
        f"appended={result.appended}"
    )
    if dry_run:
        summary += " (dry run, nothing written)"
    lines.append(summary)
    return "\n".join(lines)


def cmd_sync(cfg: Config, args: argparse.Namespace) -> int:
    session = _open_session(cfg)
    result = sync.run_sync(
        session,
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
