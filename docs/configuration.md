# Configuration

Every setting is an environment variable starting `SBICS_`. They come from the process
environment first, then from the file named with `--env-file` or, in a source checkout only, the
`.env` beside `pyproject.toml`: never both files. A variable already set to something wins over a
file. An installed copy reads no `.env` from the working directory.

A value that can't be used is never guessed at: `serve` and `check` refuse to start and say which
variable is wrong (exit status 2), and `/healthz` reports down.

## Feeds

| Variable | Required | |
|---|---|---|
| `SBICS_FEEDS` | yes | The feeds' names, separated by commas: `personal,family` |
| `SBICS_FEED_<NAME>` | yes, one per name | That feed's address: `SBICS_FEED_PERSONAL=https://…` |

- **A name** is lower-case letters, digits and underscores, starting with a letter, at most 32
  characters. It is what a client passes as `calendars`, and what answers call the calendar. No
  hyphen, so the address variable can be exported from a shell.
- **An address** is `https://`, or `webcal://` (the same address, fetched over https). Plain
  `http`, a user name or password in the address, and a fragment (`#…`) are refused.
- **The address is a secret.** Anyone who has it can read the calendar. A refused address is named
  by its variable, never quoted. Keep the env file readable by you only (`chmod 600`).

## Time zone

| Variable | Default | |
|---|---|---|
| `SBICS_TIMEZONE` | `UTC` | The zone answers are given in, unless a call names one: an IANA name such as `America/Chicago` |

Set it. The server never uses its machine's clock zone, and never a feed's own calendar-wide zone
(`X-WR-TIMEZONE`) as the answer's zone: with several feeds that would be ambiguous. Each answer
with times names the zone it used (`calendar_list`, which has none, gives its spans as UTC
dates).

A feed's `X-WR-TIMEZONE` still matters for times written **without** a zone ("floating" times):
they are read in that zone, as most calendar apps do, and the answer says so. A Windows zone name
there (`Central Standard Time`) is mapped to its IANA name; one that still isn't a zone is ignored,
and `calendar_list` and `check` say so.

## Fetching

| Variable | Default | |
|---|---|---|
| `SBICS_MIN_FETCH_SECONDS` | `60` | 10 to 86400. A feed is fetched when a call needs it and its last attempt is older than this. Attempts count, so a feed whose server is down is still asked at most this often |
| `SBICS_FETCH_TIMEOUT` | `30` | Seconds for the whole fetch: connecting, redirects and the body |
| `SBICS_MAX_FEED_MB` | `10` | 1 to 50. The largest feed accepted, counted after decompression |

A feed is held in memory as a parsed copy, which costs far more than the feed's size: in one
measurement an 8.4 MB feed peaked near 127 MB while it was read, about fifteen times its size. A
refresh holds two copies at once. That is why the cap is 50.

## HTTP mode and sign-in

These are second-brain-core's, under this server's prefix:

| Variable | Default | |
|---|---|---|
| `SBICS_TRANSPORT` | `stdio` | `http` for a remote client |
| `SBICS_HOST` | `127.0.0.1` | |
| `SBICS_PORT` | `8777` | |
| `SBICS_HTTP_PATH` | `/mcp` | |
| `SBICS_PUBLIC_URL` | none | The public origin, `https://ics.example.com`; sign-in needs it |
| `SBICS_GITHUB_CLIENT_ID`, `SBICS_GITHUB_CLIENT_SECRET` | none | The GitHub OAuth App |
| `SBICS_JWT_SIGNING_KEY` | none | At least 32 characters: `openssl rand -base64 32` |
| `SBICS_ALLOWED_GITHUB_IDS` | none | Numeric GitHub user ids, separated by commas |
| `SBICS_OAUTH_REDIRECT_URIS` | Claude's callback | Who may register as a client: https URLs, separated by commas |
| `SBICS_RATE_LIMIT_PER_MINUTE` | `60` | Tool calls per caller per minute; `0` turns it off |

See [remote-access.md](remote-access.md).

## Example

[.env.example](../.env.example) lists every setting.
