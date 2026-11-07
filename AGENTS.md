# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this is

A small Python tool that copies body-weight measurements from the Withings API
(Withings smart scales) into a Google Sheet. It runs once a day from cron on a
server. `README.md` explains setup and operation. The name is from the first
version, which read the Google Health API.

## Rules

- Never mention the AI assistant in commit messages, code comments,
  documentation or any other file. That includes `Co-Authored-By` and similar
  attribution trailers.
- Run and test everything with uv: `uv run pytest`,
  `uv run google-weight-to-sheet ...`. Add dependencies with `uv add` (or
  `uv add --dev`). Do not use pip, and do not edit `uv.lock` by hand.
- The repository is public. Never commit tokens, OAuth client secrets
  (`client_secret.json`, `withings_client.json`), the config file, spreadsheet
  IDs or raw API dumps (they contain personal health data). `.gitignore` covers the usual file names; read `git status` before
  every commit.
- Commit messages: a short lowercase subject, then a body in flowing prose that
  explains what was surprising or which trap the change avoids. No bullet lists
  of changed files.
- Tests must not use the network. Use the fakes in `tests/conftest.py`.

## Layout

- `src/google_weight_to_sheet/`
  - `withings.py` Withings measure requests and parsing
  - `withings_auth.py` Withings OAuth: the consent, the token file, refreshes
  - `auth.py` Google OAuth for the Sheets API, and the private-file helper
  - `sheets.py` Sheets API requests and the cell text formats
  - `sync.py` which readings are new, and the date window
  - `cli.py` the `auth`, `check` and `sync` commands
  - `config.py`, `http.py`, `errors.py`
- `tests/` one test file per module
- `README.md` setup, cron and troubleshooting

## Things to know

- The sheet has five columns (A to E) and may already hold rows written by an
  earlier Fitbit-based sync and by the Google Health version of this tool. The
  layout and the duplicate rules live in `sync.py` and `sheets.py`; changing
  them affects rows users already have.
- The Withings response shape in the tests follows the public reference
  (https://developer.withings.com/openapi.yaml) and was checked against a live
  Body Smart response. The reference is incomplete: live `attrib` values include
  10009, which it does not list, so the sync accepts only the documented values
  that mean "this user's own" and skips the rest. Confirm any other field
  against real data with `uv run google-weight-to-sheet check --dump FILE`
  before relying on it, and never commit such a dump.
- Withings answers HTTP 200 for most errors; the `status` in the body is the
  real outcome. `withings.call` turns a non-zero status into `WithingsError`.
- Every Withings refresh rotates the refresh token, and the old one dies once
  the new access token is used. Save the new token before using it, and never
  keep a token in two places.
