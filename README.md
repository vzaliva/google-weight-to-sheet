# google-weight-to-sheet

Copies body-weight measurements from the **Google Health API** (Fitbit Aria and
other scales) into a **Google Sheet**, one row per weigh-in. Run it daily from
cron and the sheet stays current.

It replaces the many "Fitbit to Google Sheets" scripts that used the legacy
Fitbit Web API, which Google switched off in early November 2026.

> **Status:** early version. The unit tests pass, and `auth` and `check` have
> been run against a live Google Health account, which confirmed the response
> shape described below. A full `sync` into a sheet that already holds rows has
> not been run yet. Run `check` and `sync --dry-run` first.

## What it does

Each run:

1. reads the rows already in the sheet,
2. asks the Health API for weight measurements since the newest row (minus a
   day of overlap),
3. appends the ones that are not in the sheet yet.

Rows have five columns, in the layout the old Fitbit scripts used:

| Col | Content | Example |
|-----|---------|---------|
| A | Date and time, as text, in the user's local time | `November 3, 2026 at 07:05AM` |
| B | Weight in pounds, one decimal | `174.2` |
| C | Unit label (configurable) | `pounds` |
| D | BMI | empty: the Health API does not provide it |
| E | Health API data point id | `1762153500000` |

A measurement is skipped when column E already holds its id **or** when a row
has the same minute and the same weight. The second rule is what keeps rows
written by an older tool, which have different ids or none, from being added
twice. Running the tool again and again is safe.

It only ever appends. Rows that were already written are never changed or
removed.

## Requirements

- [uv](https://docs.astral.sh/uv/). It installs a suitable Python by itself.
- A **personal** Google Account whose Google Health data holds your weigh-ins.
  Google Health refuses Google Workspace (work or school) accounts with "Can't use
  this Google Account with Google Health". Sign in to the Google Health app with
  the account at least once, or the API answers `ACCOUNT_NOT_LINKED`.
- Access to the spreadsheet from a Google account. It can be a different account
  from the Health one, since each API gets its own token, so a Workspace-owned
  sheet works.
- A Google Cloud project, to get OAuth credentials (free for this use).
- A spreadsheet with a header row and a tab for the data.

## Setup

### 1. Google Cloud

In the [Google Cloud console](https://console.cloud.google.com/):

1. Create a project, or pick one.
2. Enable two APIs: **Google Health API** (`health.googleapis.com`) and
   **Google Sheets API**.
3. Configure the OAuth consent screen (the *Audience* page). Choose
   **External** and add the Google account(s) you will authorise with as test
   users. Once everything works, **publish** the app: while an External app is
   in *Testing*, refresh tokens expire after 7 days and the daily job would stop
   working every week. (An Internal app is not an option: it admits only accounts
   from your own Workspace organisation, and Google Health does not accept those.)
4. On the *Data Access* page, add these scopes:
   - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
   - `https://www.googleapis.com/auth/spreadsheets`

   Both are declared on the same consent screen, but the tool requests them in
   two separate consents (see step 3).
5. Create an **OAuth client ID** of type **Desktop app**, download its JSON, and
   save it as `~/.config/google-weight-to-sheet/client_secret.json`.

### 2. Configuration

Create `~/.config/google-weight-to-sheet/config.toml`:

```toml
spreadsheet_id = "<the long id from the spreadsheet's URL>"
sheet_name = "Weight"     # the tab; default "Weight"
unit_label = "pounds"     # text for column C; default "pounds"
```

Each key can also come from the environment (`GWTS_SPREADSHEET_ID`,
`GWTS_SHEET_NAME`, `GWTS_UNIT_LABEL`), and the directory from `--config-dir` or
`GWTS_CONFIG_DIR`. The environment wins over the file.

### 3. Authorise

On a machine with a browser:

```sh
uv run google-weight-to-sheet auth
```

This runs **two** consents one after the other, one per API, because the Health
API refuses (`403 DISALLOWED_OAUTH_SCOPES`) any token that also carries another
scope such as Sheets. Approve the Health consent first, then the Sheets one. You
end up with `health_token.json` and `sheets_token.json` (mode 0600) in the config
directory. Each contains a refresh token and the client secret, so treat them
like passwords. `auth health` or `auth sheets` redoes just one of them.

To run the sync on another machine, such as a server, copy the tokens there
(bash):

```sh
ssh server 'mkdir -p -m 700 ~/.config/google-weight-to-sheet'
scp ~/.config/google-weight-to-sheet/{health_token,sheets_token}.json server:.config/google-weight-to-sheet/
scp ~/.config/google-weight-to-sheet/config.toml server:.config/google-weight-to-sheet/
```

The server does not need `client_secret.json` or a browser. It refreshes the
tokens on its own. To authorise on the server itself instead, forward the
redirect port and use `auth --no-browser --port 8085` with
`ssh -L 8085:localhost:8085 server`.

### 4. Check and try it

```sh
uv run google-weight-to-sheet check --dump /tmp/raw.json   # what the API returns
uv run google-weight-to-sheet sync --dry-run               # what would be added
uv run google-weight-to-sheet sync                         # add it
```

`check` prints the newest raw weight data point and how it was parsed. A dry run
lists every reading and says why each one is new or skipped. Look at it before
the first real run, especially on a sheet that already has rows: every reading
older than your newest row should say `skip`.

## Daily run with cron

```cron
17 7 * * * /home/USER/.local/bin/uv run --project /home/USER/src/google-weight-to-sheet --frozen --no-dev google-weight-to-sheet sync >> /home/USER/log/google-weight-to-sheet.log
```

- Use the absolute path of `uv` (`command -v uv`); cron has almost no `PATH`.
- Each run prints one summary line (and a line per new row) to the log. Errors go
  to stderr, which cron mails to `MAILTO` if the machine can send mail.
- A failure should not go unnoticed for days. Either rely on that mail, or ping a
  dead-man's-switch service after a successful run by appending
  `&& curl -fsS https://hc-ping.com/<your-check-id>` to the line.
- The window starts at the newest row in the sheet, so days missed while the
  machine was off are picked up by the next run.

## Commands

| Command | What it does |
|---------|--------------|
| `auth [health\|sheets\|all] [--no-browser] [--port N]` | Browser consent for each API; stores the tokens |
| `check [--days N] [--dump FILE]` | Verifies access, shows the newest raw data point |
| `sync [--dry-run] [--days N \| --since YYYY-MM-DD] [--dump FILE] [-v]` | Appends new weigh-ins |

`sync` normally starts from the newest row in the sheet. `--days` and `--since`
override that, for example to backfill after an outage. `--dump` saves the raw
API response and may contain personal data, so keep it out of the repository.

## How it talks to Google

- Weight: `GET https://health.googleapis.com/v4/users/me/dataTypes/weight/dataPoints`
  with a `civil_time` date filter, following `nextPageToken`.
- Each data point carries `weight.weightGrams` and a `weight.sampleTime` with the
  local `civilTime`. Column A is built from the civil time, so it matches the
  clock on your scale's display, whatever the server's time zone.
- Sheet: the Sheets API `values.get` and `values.append`, with raw input so the
  date text stays text.

## Troubleshooting

| Message | Meaning |
|---------|---------|
| `no health token at ...` / `no sheets token at ...` | Run `auth` and copy the token files to this machine |
| `could not refresh the health token` (or `sheets`) | The refresh token was revoked or expired (an External app left in *Testing* does this after 7 days). Run `auth health` or `auth sheets` again |
| `403 DISALLOWED_OAUTH_SCOPES` | The token used for the Health API carries a scope the Health API does not accept, such as Sheets. Run `auth health`: its token only ever asks for the Health scope |
| `ACCOUNT_NOT_LINKED` | The Google Account has not been set up in the Google Health app |
| "Can't use this Google Account with Google Health" (in a browser) | The account is managed by Google Workspace or a school, which Google Health does not support. Use a personal Google account for the Health token |
| `HTTP 403 ... has not been used in project` | Enable the Google Health API and the Google Sheets API in the Cloud project |
| `no tab named 'X'` | `sheet_name` does not match the tab. Names are case-sensitive |
| `row(s) ... have a date this tool cannot read` | Column A holds a date in a format the tool does not know. Those rows are ignored for duplicate checks; new rows are still appended correctly |

## Development

```sh
uv run pytest
```

Tests never touch the network. See `AGENTS.md` for the conventions.

## License

MIT, see `LICENSE`.
