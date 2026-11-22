# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0, a minor version
may change the interfaces.

## [Unreleased]

## [0.1.0] — First cut

A read-only MCP server over named calendar feeds, the secret iCalendar links calendar services
hand out for sharing (a Proton Calendar share link, for one), built on second-brain-core.

### Added

- **Four read-only tools** (second-brain-joplin #106): `calendar_list`, `calendar_events` (a date
  range, repeats expanded, paged), `calendar_search` (every word, in titles, places and
  descriptions) and `calendar_read` (one event or occurrence in full).
  - Times in a named zone (`SBICS_TIMEZONE`, or the call's); all-day events as dates, with the
    last day they cover; times without a zone marked floating and read in the feed's
    `X-WR-TIMEZONE` when it has one.
  - Moved and cancelled occurrences; duplicate copies of an event dropped (a new Proton link served
    them), the newest kept and counted.
  - Ids that survive a refetch, and keep an occurrence's identity when it is moved.
- **The feed's address stays on the server.** No tool takes an address; none is ever in a log
  line, an error, an answer or a refused setting's sentence (a test plants a marker in every
  address and fails if it appears).
- **Bounded fetching:** https only (`webcal://` read as https), redirects only to https on the
  same host, one deadline for the whole fetch, a size cap after decompression, status 200 only,
  and a body that must be a calendar. At most one fetch per feed a minute; on failure the last good
  copy is served as stale, with the reason. Copies are held in memory only.
- **Hostile feeds can't stall it:** repeat rules too costly to expand are refused, a slow event is
  set aside, and each call has a time budget, after which it says it is incomplete. A broken
  event is set aside and counted; the rest of the feed is read.
- **Outside text is cleaned and marked:** titles, places, descriptions and attendee names go
  through second-brain-core's cleaner, and every answer carrying them says they are information,
  not instructions.
- `second-brain-ics check`: fetches each feed once and prints counts and timings, never an address
  or event text. `/healthz` in HTTP mode reports `calendar`.

[Unreleased]: https://github.com/second-brain-tools/second-brain-ics/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/second-brain-tools/second-brain-ics/releases/tag/v0.1.0
