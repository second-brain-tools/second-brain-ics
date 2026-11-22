# second-brain-ics

An [MCP](https://modelcontextprotocol.io) server that lets Claude **read calendar feeds**: the
secret iCalendar (`.ics`) links that calendar services hand out for sharing, such as a
[Proton Calendar](https://proton.me/calendar) share link. It expands repeating events, handles
moved and cancelled occurrences, time zones and all-day events, and answers in the time zone you
name.

**It cannot write.** A feed is read-only by nature, and there is no code here that creates,
changes or deletes an event or answers an invitation.

A sibling of [second-brain-joplin](https://github.com/second-brain-tools/second-brain-joplin), on
the same stack ([second-brain-core](https://github.com/second-brain-tools/second-brain-core)):
FastMCP, stdio for a client on the same machine, streamable HTTP with GitHub sign-in for a Claude
custom connector.

**Status:** 0.1.0. Issues are tracked on the organisation's public tracker,
[second-brain-joplin](https://github.com/second-brain-tools/second-brain-joplin/issues).

## What Claude gets

| Tool | What it does |
|---|---|
| `calendar_list` | The calendars: names, how many events, the span of their starts, and how current each one is |
| `calendar_events` | Occurrences in a date range, repeats expanded, sorted by start, a page at a time |
| `calendar_search` | Events whose title, place or description holds every word of a query |
| `calendar_read` | One event, or one occurrence of it, in full: description, organizer, attendees, repeat rule |

Details: [docs/tools.md](docs/tools.md).

## What it is careful about

- **The feed's address is a secret, and it stays on the server.** Anyone who has a share link
  can read that calendar. Feeds are named in the server's settings, so no tool takes an address
  (nothing a model reads can make the server fetch a URL), and no log line, error or answer ever
  carries one. A test plants a marker in every address it uses and fails if it shows up anywhere.
- **Bounded fetching.** https only; redirects only to https on the same host; one deadline for
  the whole fetch; a size cap on the decoded body; at most one fetch per feed a minute. When a
  fetch fails, the last good copy is served, marked stale, with the reason.
- **Event text is written by other people** (an invitation's title and description are the
  sender's). Every piece is cleaned of invisible and direction-changing characters, HTML is
  reduced to the text a reader sees, and every answer says its text is information, not
  instructions.
- **Repeat rules can't stall it.** Rules that would take too long to expand are refused, and a
  call has a time budget.
- **No calendar data on disk.** The copy of each feed is held in memory only.
- **Quiet logs.** In HTTP mode, one line per tool call: tool, caller, client, outcome, duration;
  in either mode, a warning naming the feed when a fetch fails. Never a search term, an event or
  an address.

More in [SECURITY.md](SECURITY.md).

## Try it

```bash
uvx second-brain-ics --version
```

Make a share link (for Proton Calendar: [docs/proton-calendar.md](docs/proton-calendar.md)), then
put it in an env file only you can read:

```bash
umask 077
cat > ics.env <<'EOF'
SBICS_FEEDS=personal
SBICS_FEED_PERSONAL=https://calendar.example.com/your/secret/link.ics
SBICS_TIMEZONE=America/Chicago
EOF
uvx second-brain-ics check --env-file ics.env   # fetches each feed once, prints counts
```

Then add it to Claude Code as a stdio server:

```bash
claude mcp add second-brain-ics -- uvx second-brain-ics serve --env-file "$PWD/ics.env"
```

- Settings: [docs/configuration.md](docs/configuration.md)
- As a Claude custom connector, for claude.ai and the apps:
  [docs/remote-access.md](docs/remote-access.md)

## Development

```bash
uv sync
uv run pytest --cov-fail-under=90   # the suite runs its own HTTPS server; no feed needed
uv run mypy
uvx pre-commit run --all-files      # ruff lint and format, as CI runs them
uv run scripts/smoke.py --from .    # installs the checkout with uvx and talks MCP to it
```

Sign-in, the audit log, HTTP serving, the settings-file rules and the text cleaner are
second-brain-core's: fix shared code there, release it, then move this package's pin. To try an
unreleased core change, point uv at a checkout for the moment, and don't commit it
(`[tool.uv.sources] second-brain-core = { path = "../second-brain-core", editable = true }`).

More in [CONTRIBUTING.md](CONTRIBUTING.md); releasing in [docs/releasing.md](docs/releasing.md).

## License

Copyright (C) 2026 John McKenzie

second-brain-ics is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either version 3 of
the License, or (at your option) any later version. It is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE) for the full text.

In short: run it and change it as you like. If you pass copies on, or change it and let other
people use your version over a network, you must offer them the source under the same license.
