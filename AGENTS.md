# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this is

A small Python tool that copies body-weight measurements from the Google Health
API into a Google Sheet. It runs once a day from cron on a server. `README.md`
explains setup and operation.

## Rules

- Never mention the AI assistant in commit messages, code comments,
  documentation or any other file. That includes `Co-Authored-By` and similar
  attribution trailers.
- Run and test everything with uv: `uv run pytest`,
  `uv run google-weight-to-sheet ...`. Add dependencies with `uv add` (or
  `uv add --dev`). Do not use pip, and do not edit `uv.lock` by hand.
- The repository is public. Never commit tokens, OAuth client secrets, the
  config file, spreadsheet IDs or raw API dumps (they contain personal health
  data). `.gitignore` covers the usual file names; read `git status` before
  every commit.
- Commit messages: a short lowercase subject, then a body in flowing prose that
  explains what was surprising or which trap the change avoids. No bullet lists
  of changed files.
- Tests must not use the network. Use the fakes in `tests/conftest.py`.

## Layout

- `src/google_weight_to_sheet/`
  - `auth.py` OAuth, with a separate token per API (the Health API rejects a
    token that also carries the Sheets scope)
  - `health.py` Health API requests and parsing
  - `sheets.py` Sheets API requests and the cell text formats
  - `sync.py` which readings are new, and the date window
  - `cli.py` the `auth`, `check` and `sync` commands
  - `config.py`, `http.py`, `errors.py`
- `tests/` one test file per module
- `README.md` setup, cron and troubleshooting

## Things to know

- The sheet has five columns (A to E) and may already hold rows written by an
  earlier Fitbit-based sync. The layout and the duplicate rules live in
  `sync.py` and `sheets.py`; changing them affects rows users already have.
- The Health API response shape in the tests comes from the public reference,
  not from a captured response. Confirm a field against real data with
  `uv run google-weight-to-sheet check --dump FILE` before relying on it.
