# google-weight-to-sheet

Copies body-weight measurements from a **Withings** smart scale (Body Smart and
the other Withings scales) into a **Google Sheet**, one row per weigh-in. Run it
daily from cron and the sheet stays current.

It reads the [Withings Public API](https://developer.withings.com/), which is
free for personal use. The first version of this tool read the Google Health
API (Fitbit Aria), which is where the name comes from; only the sheet is Google
now. Rows written by that version, or by an older Fitbit sync, stay in the
sheet, and Withings rows are added after them.

> **Status:** the Withings source is new. It follows the public API reference
> and its unit tests pass, but it has not been run against a live Withings
> account yet. Run `check` and `sync --dry-run` before the first real sync.

## What it does

Each run:

1. reads the rows already in the sheet,
2. asks Withings for weight measurements since the newest row (minus a day of
   overlap),
3. appends the ones that are not in the sheet yet.

Rows have five columns, in the layout the old Fitbit scripts used:

| Col | Content | Example |
|-----|---------|---------|
| A | Date and time, as text, in the Withings account's time zone | `November 3, 2026 at 07:05AM` |
| B | Weight in pounds, one decimal | `174.2` |
| C | Unit label (configurable) | `pounds` |
| D | BMI | empty: Withings does not send it |
| E | Withings measure group id (`grpid`) | `5765432101` |

A measurement is skipped when column E already holds its id **or** when a row
has the same minute and the same weight. The second rule is what keeps rows
written by an older tool, which have different ids or none, from being added
twice. Running the tool again and again is safe.

A weigh-in that Withings marks as **ambiguous** is not written. Withings uses
that mark when a scale shared by several people could not tell who stood on it.
The log names such weigh-ins. Confirm it in the Withings app and a later sync
adds it, as long as it still falls in the sync window (see `--since` below).

It only ever appends. Rows that were already written are never changed or
removed.

## Requirements

- [uv](https://docs.astral.sh/uv/). It installs a suitable Python by itself.
- A Withings account with the scale set up in the Withings app.
- A Withings developer application, to get OAuth credentials (free, see step 1).
- Access to the spreadsheet from a Google account, and a Google Cloud project
  for its OAuth credentials (free for this use).
- A spreadsheet with a header row and a tab for the data.

## Setup

### 1. Withings developer application

1. Sign in to the [Withings Developer Dashboard](https://developer.withings.com/dashboard/)
   with your Withings account and create an application of type
   **Public API integration**.
2. Give it any name and description. As the callback URL, register exactly
   `http://localhost:8085/callback`. Withings accepts a plain `http://localhost`
   callback but then limits the application to 10 users ("restricted mode"),
   which does not matter for personal use.
3. Save it, then copy the **client ID** and the **client secret** it shows into
   `~/.config/google-weight-to-sheet/withings_client.json`:

   ```json
   {
     "client_id": "<client ID>",
     "client_secret": "<client secret>",
     "redirect_uri": "http://localhost:8085/callback"
   }
   ```

`redirect_uri` must match the registered callback URL character for character
(`localhost` is not `127.0.0.1`). If Withings will not take a localhost callback
for your application, register an HTTPS page you control instead (Withings
checks that it answers) and put that address in `redirect_uri`. `auth` then asks
you to paste the address the browser lands on after the consent.

### 2. Google Cloud, for the sheet

In the [Google Cloud console](https://console.cloud.google.com/):

1. Create a project, or pick one.
2. Enable the **Google Sheets API**.
3. Configure the OAuth consent screen (the *Audience* page). Choose
   **External** and add your Google account as a test user, then **publish** the
   app once everything works: while an External app is in *Testing*, refresh
   tokens expire after 7 days and the daily job would stop working every week.
   If the account belongs to a Google Workspace organisation, **Internal** works
   too and has no such limit.
4. On the *Data Access* page, add the scope
   `https://www.googleapis.com/auth/spreadsheets`.
5. Create an **OAuth client ID** of type **Desktop app**, download its JSON, and
   save it as `~/.config/google-weight-to-sheet/client_secret.json`.

If you set this up for the Google Health version, it can stay as it is. The
Health API is no longer used and can be disabled.

### 3. Configuration

Create `~/.config/google-weight-to-sheet/config.toml`:

```toml
spreadsheet_id = "<the long id from the spreadsheet's URL>"
sheet_name = "Weight"     # the tab; default "Weight"
unit_label = "pounds"     # text for column C; default "pounds"
```

Each key can also come from the environment (`GWTS_SPREADSHEET_ID`,
`GWTS_SHEET_NAME`, `GWTS_UNIT_LABEL`), and the directory from `--config-dir` or
`GWTS_CONFIG_DIR`. The environment wins over the file.

### 4. Authorise

On a machine with a browser:

```sh
uv run google-weight-to-sheet auth
```

This runs two consents one after the other: Withings first, then Google for the
sheet. For Withings the tool listens on `localhost:8085` until the browser comes
back from the consent page. It has to exchange the code within 30 seconds, and
it does that straight away. You end up with `withings_token.json` and
`sheets_token.json` (mode 0600) in the config directory. Each contains a refresh
token and a client secret, so treat them like passwords. `auth withings` or
`auth sheets` redoes just one of them.

Coming from the Google Health version, run `auth withings` only.
`health_token.json` is no longer used and can be deleted.

To run the sync on another machine, such as a server, **move** the Withings
token there and copy the rest (bash):

```sh
ssh server 'mkdir -p -m 700 ~/.config/google-weight-to-sheet'
scp ~/.config/google-weight-to-sheet/{withings_token,sheets_token}.json server:.config/google-weight-to-sheet/
scp ~/.config/google-weight-to-sheet/config.toml server:.config/google-weight-to-sheet/
rm ~/.config/google-weight-to-sheet/withings_token.json
```

Keep exactly one copy of `withings_token.json`. Withings hands out a new refresh
token on every refresh and retires the old one, so of two copies, the first to
refresh breaks the other. A Withings access token lasts three hours, so every
daily run refreshes it and rewrites the file. The cron user needs write access
to the config directory.

The server does not need `client_secret.json`, `withings_client.json` or a
browser. To authorise on the server itself instead, forward the port and use
`--no-browser`:

```sh
ssh -L 8085:localhost:8085 server
# then, in that session:
uv run google-weight-to-sheet auth --no-browser --port 8085
```

Port 8085 then serves the Withings consent (from its `redirect_uri`) and,
afterwards, the Google one (from `--port`).

### 5. Check and try it

```sh
uv run google-weight-to-sheet check --dump /tmp/raw.json   # what the API returns
uv run google-weight-to-sheet sync --dry-run               # what would be added
uv run google-weight-to-sheet sync                         # add it
```

`check` prints the newest raw measure group, the account's time zone and how
the group was parsed. A dry run lists every reading and says why each one is new
or skipped. Look at it before the first real run, especially on a sheet that
already has rows: every reading older than your newest row should say `skip`.

The window starts at the newest row in the sheet. If you used the Withings scale
before that row was written, those earlier weigh-ins are not fetched. Add them
with `sync --since YYYY-MM-DD`: the duplicate rules keep this safe.

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
- Withings asks clients not to poll more often than every 10 minutes per user.
  Once a day is far below that.

## Commands

| Command | What it does |
|---------|--------------|
| `auth [withings\|sheets\|all] [--no-browser] [--port N]` | Browser consent for each API; stores the tokens |
| `check [--days N] [--dump FILE]` | Verifies Withings access, shows the newest raw measure group |
| `sync [--dry-run] [--days N \| --since YYYY-MM-DD] [--dump FILE] [-v]` | Appends new weigh-ins |

`sync` normally starts from the newest row in the sheet. `--days` and `--since`
override that, for example to backfill after an outage. `--dump` saves the raw
API response and may contain personal data, so keep it out of the repository.

## How it talks to Withings and Google

- Weight: `POST https://wbsapi.withings.net/measure` with `action=getmeas`,
  `meastype=1` (weight), `category=1` (real measurements, not goals) and a
  `startdate`/`enddate` window, following `more`/`offset` paging. Withings
  filters by instant while the window is in local dates, so the request has a
  day of margin on each side and readings outside the window are dropped.
- Withings answers HTTP 200 even for most errors. The `status` in the body is
  the real outcome, and anything but 0 is reported as an error.
- Each measure group carries a Unix `date`, an `attrib` (how sure Withings is
  that it is yours) and the weight as `value × 10^unit` kg. The answer also
  names the account's time zone. Column A is the `date` in that zone, or in the
  machine's own zone if the answer names none.
- Tokens: OAuth 2.0 with `https://account.withings.com/oauth2_user/authorize2`
  and `https://wbsapi.withings.net/v2/oauth2` (`action=requesttoken`), scope
  `user.metrics` only.
- Sheet: the Sheets API `values.get` and `values.append`, with raw input so the
  date text stays text.

## Troubleshooting

| Message | Meaning |
|---------|---------|
| `no withings token at ...` / `no sheets token at ...` | Run `auth` and put the token files on this machine |
| `missing .../withings_client.json` | Create the Withings application and its client file (setup step 1) |
| `could not refresh the withings token` | The refresh token was revoked, went unused for a year, or a copy of the token file was refreshed somewhere else first. Run `auth withings` again and keep the file in one place |
| `could not refresh the sheets token` | The refresh token was revoked or expired (an External app left in *Testing* does this after 7 days). Run `auth sheets` again |
| "The redirect URI provided is missing or does not match partner callback url" (in the browser) | `redirect_uri` in `withings_client.json` differs from the callback URL registered for the application |
| `Withings refused the authorization code` | More than 30 seconds passed before the code was exchanged (when pasting the address), or the redirect URI does not match. Run `auth withings` again |
| `cannot listen on localhost:8085` | Another program uses the port. Stop it, or register another callback URL and change `redirect_uri` to match |
| `Withings status 601` | Too many requests. Try again later |
| `Withings is not sure this weigh-in is yours` | The scale could not tell which of its users stood on it. Confirm the weigh-in in the Withings app |
| `HTTP 403 ... has not been used in project` | Enable the Google Sheets API in the Cloud project |
| `no tab named 'X'` | `sheet_name` does not match the tab. Names are case-sensitive |
| `row(s) ... have a date this tool cannot read` | Column A holds a date in a format the tool does not know. Those rows are ignored for duplicate checks; new rows are still appended correctly |

## Development

```sh
uv run pytest
```

Tests never touch the network. See `AGENTS.md` for the conventions.

## License

MIT, see `LICENSE`.
