# Security

## Reporting a vulnerability

Please report it privately, through GitHub's "Report a vulnerability" on this repository's
Security tab, not in a public issue.

## What this server is trusted with

Read access to the calendars whose share links it holds, and the links themselves. A link is a
password: anyone who has it can read that calendar. What the server must never do: show a link to
anyone, fetch anything a caller or a feed chooses, show a calendar to anyone but the people
allowed to sign in, or hand a model text that the calendar's human reader could not have seen.

## What it does about each

### It can't change a calendar

A feed is read-only, and the server only ever sends `GET`. There is no code that creates, changes
or deletes an event, or answers an invitation.

### The address never leaves the server

- Feeds are named in the server's settings. **No tool takes an address**, so nothing a model reads
  (an invitation, say) can make the server fetch a URL.
- One module, [fetch.py](src/second_brain_ics/fetch.py), is the only one that touches the network
  or an address. Every failure there becomes one of a fixed set of sentences, chosen by the
  failure's kind, never the HTTP library's own text (which quotes the URL). That sentence is
  raised outside the handler, so it chains nothing that holds the URL.
- The settings leave the address out of their printed form, and a refused address is named by its
  variable, never quoted.
- The HTTP library's loggers, which print each request's URL at info level, are held at warning
  level in every transport.
- [tests/test_leak.py](tests/test_leak.py) plants a marker in the path and the query of every
  address, drives every kind of failure with every logger at debug level, and fails if the marker
  appears in the log, a tool's answer or error, `/healthz`, `check`'s output, the settings' repr
  or a refused setting's sentence. A control in the same file shows the check does see a leak
  when the loggers are not held back.

### What it fetches is bounded

- `https` only (`webcal://` is read as https); certificates verified against the system's trust
  store; no proxy, certificate file or other setting taken from the environment.
- Redirects followed by hand: at most three, and only to https on the same host and port.
- One deadline over the whole fetch (connect, redirects and body), so a body sent a byte at a time
  still stops. A size cap counted after decompression, so a compressed bomb stops too. Status 200
  only, and a body that must start as a calendar does (a sign-in page is refused).
- At most one fetch per feed per `SBICS_MIN_FETCH_SECONDS`, counting failures, so neither a
  caller nor the open `/healthz` can make it hammer a calendar service.

### Only listed people get in (HTTP mode)

GitHub sign-in limited to numeric GitHub user ids, checked when the token is verified and again
before each tool. No sign-in means no HTTP mode, except `--no-auth` on a loopback address with no
public URL. See [docs/remote-access.md](docs/remote-access.md).

### Event text is treated as written by someone else

An invitation's title, place, description and attendee names are written by its sender.

- Every piece goes through second-brain-core's cleaner: invisible and direction-changing
  characters are removed, and an HTML description is reduced to the text a reader would see
  (hidden elements, scripts, styles and comments dropped; nothing is fetched). Lengths are capped.
- Every answer that carries such text starts with a notice that it is information, never
  instructions, and so do the server's instructions to the client.
- **A feed can't make it slow.** Repeat rules that step every second or minute, have no frequency
  the server knows, would take more than 100,000 steps to walk, or ask for a month day no listed
  month has, are refused when the feed is read, and so is an event with two repeat rules or an
  EXRULE. Each event is expanded with one bounded walk over the call's range; an event that takes more
  than a second to expand is set aside for as long as the process runs; and each call has a ten
  second budget, after which it answers with what it has and says so. Expansion runs off the
  event loop.
- An event can still ask for something in plain, visible words. What a model does with that
  depends on the client and on the other tools the same chat has
  ([docs/remote-access.md](docs/remote-access.md#before-you-connect-it)).

### No calendar data on disk

Each feed's copy is held in memory only. A restart starts from nothing. (In HTTP mode with
sign-in, the sign-in layer keeps its client registrations in FastMCP's encrypted store on disk;
that holds no calendar data.)

### Logs

- In HTTP mode, one line per tool call (tool, caller id, client, outcome, duration) and a
  start-up line naming the feeds (names only); in either mode, a warning naming the feed and the
  kind of failure when a fetch fails. Never arguments, results, event text or an
  address.
- A failure nobody planned for gives the client one fixed sentence, and the log the exception's
  type and the place it surfaced, never its text.
- An installed copy reads no `.env` from the working directory, and FastMCP's own `.env` reading
  is switched off. With `--env-file`, that file is the whole configuration.
