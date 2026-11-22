"""The four read-only tools: list the calendars, list events, search, read one event."""

import asyncio
import base64
import binascii
import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastmcp import FastMCP
from icalendar import Component
from pydantic import Field
from second_brain_core import failure

from .. import models, views
from ..config import zone_or_none
from ..errors import CalendarError, NotFoundError, RequestError
from ..feed import (
    HORIZON,
    Budget,
    Occurrence,
    ParsedFeed,
    Series,
    find_occurrence,
    next_occurrence,
    occurrences,
    read_zone,
)
from ..identity import SERVER
from ..store import Store, View
from .annotations import READ_ONLY_FEED
from .failure import guarded

# A date, and the start of a date-time: other forms ISO 8601 allows ("20261122")
# would read as a date-time at midnight, and leave the day out of a range's end.
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
DEFAULT_DAYS = 7
MAX_RANGE = timedelta(days=366)
EVENTS_LIMIT = 50
EVENTS_LIMIT_MAX = 200
SEARCH_LIMIT = 20
SEARCH_LIMIT_MAX = 100
QUERY_CHARS = 200

NOT_FOUND = (
    "No event with that id in that calendar: it may have been deleted, or the feed "
    "changed. List or search again for a current id."
)

Calendars = Annotated[
    list[str] | None,
    Field(description="Calendar names from calendar_list; all of them when left out"),
]
Zone = Annotated[
    str | None,
    Field(
        description=(
            "An IANA time zone such as America/Chicago for the answer's times; the "
            "server's zone (SBICS_TIMEZONE) when left out"
        )
    ),
]
Start = Annotated[
    str | None,
    Field(
        description=(
            "YYYY-MM-DD or an ISO 8601 date-time; a time without an offset is in the "
            "answer's zone. Default: the start of today"
        )
    ),
]
End = Annotated[
    str | None,
    Field(
        description=(
            "YYYY-MM-DD (that whole day is included) or an ISO 8601 date-time "
            "(excluded). Default: 7 days after start. At most 366 days after start"
        )
    ),
]


def _zone(name: str | None, store: Store) -> ZoneInfo:
    wanted = name.strip() if name and name.strip() else store.settings.timezone
    zone = zone_or_none(wanted)
    if zone is None:
        shown = wanted[:60]
        raise RequestError(f"{shown!r} is not an IANA time zone, such as America/Chicago.")
    return zone


def _moment(text: str, zone: ZoneInfo, *, end: bool) -> datetime:
    """``text`` as an instant. A date is the start of that day, or for ``end`` of the next."""
    raw = text.strip()
    try:
        if DATE.fullmatch(raw):
            day = date.fromisoformat(raw)
            if end:
                day += timedelta(days=1)
            return datetime.combine(day, datetime.min.time(), zone)
        if DATE_TIME.match(raw):
            moment = datetime.fromisoformat(raw)
            return moment if moment.tzinfo is not None else moment.replace(tzinfo=zone)
    except (ValueError, OverflowError):
        pass
    shown = raw[:40]
    raise RequestError(f"{shown!r} is not a date (YYYY-MM-DD) or an ISO 8601 date-time.")


def _range(
    start: str | None, end: str | None, zone: ZoneInfo, now: datetime
) -> tuple[datetime, datetime]:
    if start:
        begins = _moment(start, zone, end=False)
    else:
        begins = datetime.combine(now.astimezone(zone).date(), datetime.min.time(), zone)
    ends = _moment(end, zone, end=True) if end else begins + timedelta(days=DEFAULT_DAYS)
    if ends <= begins:
        raise RequestError("end must be after start.")
    if ends - begins > MAX_RANGE:
        raise RequestError("The range is longer than 366 days: ask for a shorter one.")
    if begins < now - HORIZON or ends > now + HORIZON:
        raise RequestError("The range must lie within ten years of today.")
    return begins, ends


def _chosen(calendars: list[str] | None, store: Store) -> list[str]:
    if not calendars:
        return list(store.names)
    chosen: list[str] = []
    for name in calendars:
        if name not in store.names:
            shown = name[:40]
            raise RequestError(f"No calendar named {shown!r}. calendar_list gives the names.")
        if name not in chosen:
            chosen.append(name)
    return chosen


async def _views(names: list[str], store: Store) -> tuple[list[View], list[ParsedFeed]]:
    """Each named feed as it is now, and the parsed copies to answer from.

    A feed with no copy is still reported (with its error); when none of them has
    one, the call fails with their reasons.
    """
    got = [await store.view(name) for name in names]
    feeds = [view.copy.feed for view in got if view.copy is not None]
    if not feeds:
        reasons = " ".join(
            view.error or f"The calendar feed {view.name!r} is not read yet." for view in got
        )
        raise CalendarError(reasons)
    return got, feeds


def _fingerprint(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def _cursor_for(key: tuple[float, str, str, str], query: str) -> str:
    raw = json.dumps({"k": list(key), "q": query}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_key(cursor: str, query: str) -> tuple[float, str, str, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        stamp, feed, series, rid = data["k"]
        if data["q"] != query:
            raise ValueError
        return (float(stamp), str(feed), str(series), str(rid))
    except (ValueError, KeyError, TypeError, binascii.Error, json.JSONDecodeError):
        raise RequestError(
            "That cursor doesn't belong to this request: repeat the request without it."
        ) from None


def _words(query: str) -> list[str]:
    words = query.casefold().split()
    if not words:
        raise RequestError("query must hold at least one word.")
    return words


def _matches(component_texts: list[str], words: list[str]) -> bool:
    haystack = "\n".join(component_texts).casefold()
    return all(word in haystack for word in words)


def _texts(component: Component) -> list[str]:
    """What a search reads of an event: its title, place and description, cleaned."""
    return [
        views.title(component),
        views.line(component, "LOCATION", views.LOCATION_CHARS) or "",
        views.description(component) or "",
    ]


def _now() -> datetime:
    return datetime.now(UTC)


def register(mcp: FastMCP, store: Store) -> None:
    """Register the four tools, reading through ``store``."""

    @mcp.tool(annotations=READ_ONLY_FEED)
    @guarded
    async def calendar_list() -> models.CalendarList:
        """The calendars this server reads: names, counts, and how current each one is.

        Fetches a feed first when its copy is older than the server's minimum
        interval. Each calendar's `status` says when its data was fetched, and
        whether the latest fetch failed (the older copy is then served as stale).
        """
        got = [await store.view(name) for name in store.names]
        return models.CalendarList(calendars=[views.calendar(view) for view in got])

    @mcp.tool(annotations=READ_ONLY_FEED)
    @guarded
    async def calendar_events(
        start: Start = None,
        end: End = None,
        calendars: Calendars = None,
        timezone: Zone = None,
        limit: Annotated[int, Field(ge=1, le=EVENTS_LIMIT_MAX)] = EVENTS_LIMIT,
        cursor: Annotated[str | None, Field(description="From the previous page")] = None,
    ) -> models.Events:
        """Occurrences in a date range, repeating events expanded, sorted by start.

        Times are in the zone the answer names. An all-day event has dates, and its
        `end` is the last day it covers. When `more` is true, pass `cursor` back
        with the same arguments for the next page.
        """
        try:
            zone = _zone(timezone, store)
            now = _now()
            begins, ends = _range(start, end, zone, now)
            names = _chosen(calendars, store)
            query = _fingerprint("events", begins, ends, names, zone.key)
            after = _cursor_key(cursor, query) if cursor else None
            got, feeds = await _views(names, store)

            def page() -> tuple[list[Occurrence], bool, bool]:
                budget = Budget()
                stream = occurrences(feeds, begins, ends, zone, budget, after=after)
                taken: list[Occurrence] = []
                for item in stream:
                    if len(taken) == limit:
                        return taken, True, budget.incomplete
                    taken.append(item)
                return taken, False, budget.incomplete

            taken, more, incomplete = await asyncio.to_thread(page)
            return models.Events(
                timezone=zone.key,
                start=begins.isoformat(timespec="seconds"),
                end=ends.isoformat(timespec="seconds"),
                feeds=[views.feed_status(view) for view in got],
                occurrences=[views.occurrence(item, zone) for item in taken],
                more=more,
                cursor=_cursor_for(taken[-1].key, query) if more and taken else None,
                incomplete=incomplete,
            )
        except CalendarError as exc:
            raise failure.tool_error(SERVER, exc) from None

    @mcp.tool(annotations=READ_ONLY_FEED)
    @guarded
    async def calendar_search(
        query: Annotated[str, Field(min_length=1, max_length=QUERY_CHARS)],
        start: Start = None,
        end: End = None,
        calendars: Calendars = None,
        timezone: Zone = None,
        limit: Annotated[int, Field(ge=1, le=SEARCH_LIMIT_MAX)] = SEARCH_LIMIT,
    ) -> models.SearchResults:
        """Events whose title, place or description holds every word of `query`.

        Case doesn't matter. With `start` or `end`, the matching occurrences in that
        range; without either, each matching event once, with its next occurrence
        from now (or `next` empty when none is ahead).
        """
        try:
            words = _words(query)
            zone = _zone(timezone, store)
            now = _now()
            names = _chosen(calendars, store)
            got, feeds = await _views(names, store)
            statuses = [views.feed_status(view) for view in got]

            if start or end:
                begins, ends = _range(start, end, zone, now)

                def ranged() -> tuple[list[Occurrence], bool, bool]:
                    budget = Budget()
                    stream = occurrences(
                        feeds,
                        begins,
                        ends,
                        zone,
                        budget,
                        keep=lambda item: _matches(_texts(item.component), words),
                    )
                    taken: list[Occurrence] = []
                    for item in stream:
                        if len(taken) == limit:
                            return taken, True, budget.incomplete
                        taken.append(item)
                    return taken, False, budget.incomplete

                taken, more, incomplete = await asyncio.to_thread(ranged)
                return models.SearchResults(
                    timezone=zone.key,
                    feeds=statuses,
                    occurrences=[views.occurrence(item, zone) for item in taken],
                    more=more,
                    incomplete=incomplete,
                )

            def by_series() -> tuple[list[models.SeriesMatch], bool, bool]:
                budget = Budget()
                found: list[tuple[ParsedFeed, Series]] = []
                for feed in feeds:
                    for item in feed.usable:
                        texts = [text for part in item.components for text in _texts(part)]
                        if _matches(texts, words):
                            found.append((feed, item))
                matches: list[tuple[float, models.SeriesMatch]] = []
                for feed, item in found:
                    following = next_occurrence(feed, item, now, zone, budget)
                    first_start = views.span(item.first, zone, read_zone(feed, zone))[0]
                    when = following.start.timestamp() if following is not None else float("inf")
                    matches.append(
                        (
                            when,
                            models.SeriesMatch(
                                id=views.occurrence_id(feed, item),
                                calendar=feed.name,
                                title=views.title(item.first),
                                recurring=views.recurring(item),
                                next=views.occurrence(following, zone) if following else None,
                                first_start=first_start,
                            ),
                        )
                    )
                matches.sort(key=lambda pair: (pair[0], pair[1].calendar, pair[1].id))
                return (
                    [match for _, match in matches[:limit]],
                    len(matches) > limit,
                    budget.incomplete,
                )

            events, more, incomplete = await asyncio.to_thread(by_series)
            return models.SearchResults(
                timezone=zone.key, feeds=statuses, events=events, more=more, incomplete=incomplete
            )
        except CalendarError as exc:
            raise failure.tool_error(SERVER, exc) from None

    @mcp.tool(annotations=READ_ONLY_FEED)
    @guarded
    async def calendar_read(
        id: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description="An id from calendar_events or calendar_search",
            ),
        ],
        timezone: Zone = None,
    ) -> models.EventDetail:
        """One event in full: description, place, organizer, attendees, repeat rule.

        With an occurrence's id, that occurrence (a moved one shows its new time);
        with an event's id (from a search without a range), the event and its next
        occurrence.
        """
        try:
            zone = _zone(timezone, store)
            parts = id.strip().split(":")
            if len(parts) not in (2, 3) or parts[0] not in store.names:
                raise NotFoundError(NOT_FOUND)
            view = await store.view(parts[0])
            if view.copy is None:
                raise CalendarError(view.error or NOT_FOUND)
            feed = view.copy.feed
            series = feed.series.get(parts[1])
            if series is None or series.unusable is not None:
                raise NotFoundError(NOT_FOUND)
            now = _now()

            def look() -> models.EventDetail:
                budget = Budget()
                if len(parts) == 3:
                    found = find_occurrence(feed, series, parts[2], zone, budget)
                    if found is None:
                        raise NotFoundError(NOT_FOUND)
                    return views.detail(
                        view, series, found.component, zone, rid=found.rid, following=None
                    )
                following = next_occurrence(feed, series, now, zone, budget)
                return views.detail(
                    view,
                    series,
                    series.first,
                    zone,
                    rid=None,
                    following=views.occurrence(following, zone) if following else None,
                )

            return await asyncio.to_thread(look)
        except CalendarError as exc:
            raise failure.tool_error(SERVER, exc) from None
