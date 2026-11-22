"""Turning events into answers: times in the answer's zone, every piece of text cleaned.

Event text is written by whoever made the event, and for an invitation that is
someone else, so each piece goes through second-brain-core's cleaner before it
leaves the server (invisible and bidirectional characters removed, HTML reduced
to the text a reader sees), and the answers carry the notice (models.py).
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from icalendar import Component
from second_brain_core.textclean import clean_line, html_to_text, tidy

from . import models
from .feed import Occurrence, ParsedFeed, Series, instant, read_zone, rule_text, when
from .store import View

TITLE_CHARS = 300
LOCATION_CHARS = 300
NAME_CHARS = 200
URL_CHARS = 2000
RULE_CHARS = 500
DESCRIPTION_CHARS = 20_000
ATTENDEES_AT_MOST = 50
UNTITLED = "(no title)"


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) else value


def text(component: Component, name: str) -> str | None:
    """A text property as it was written, or None when it is missing or empty."""
    value = _first(component.get(name))
    if value is None:
        return None
    raw = str(value)
    return raw if raw.strip() else None


def line(component: Component, name: str, limit: int) -> str | None:
    raw = text(component, name)
    if raw is None:
        return None
    cleaned = clean_line(raw, limit)
    return cleaned or None


def title(component: Component) -> str:
    return line(component, "SUMMARY", TITLE_CHARS) or UNTITLED


def description(component: Component) -> str | None:
    """The description as plain text, or the HTML one (X-ALT-DESC) reduced to text."""
    raw = text(component, "DESCRIPTION")
    if raw is not None:
        body = tidy(raw)
    else:
        alt = _first(component.get("X-ALT-DESC"))
        if alt is None or "html" not in str(alt.params.get("FMTTYPE", "")).lower():
            return None
        body = html_to_text(str(alt))
    if not body:
        return None
    if len(body) > DESCRIPTION_CHARS:
        body = body[: DESCRIPTION_CHARS - 1].rstrip() + "…"
    return body


def has_description(component: Component) -> bool:
    return text(component, "DESCRIPTION") is not None or component.get("X-ALT-DESC") is not None


def stamp(value: Any) -> str | None:
    """A DTSTAMP-like value as UTC ISO 8601."""
    value = getattr(_first(value), "dt", None)
    if isinstance(value, datetime):
        return instant(value, ZoneInfo("UTC")).astimezone(UTC).isoformat()
    return None


def _end_of(component: Component, start: date | datetime) -> date | datetime:
    end = when(component, "DTEND")
    if end is not None:
        return end
    try:
        computed: date | datetime = component.end  # type: ignore[attr-defined]
        return computed
    except Exception:  # noqa: BLE001 - no end written and none computable: RFC 5545's defaults
        return start + timedelta(days=1) if not isinstance(start, datetime) else start


def span(
    component: Component, zone: ZoneInfo, floating: ZoneInfo | None = None
) -> tuple[str, str, bool]:
    """Start and end in ``zone``, and whether the event is all-day.

    An all-day event's ``end`` is the last day it covers: RFC 5545 writes the day
    after, which a reader takes for one day too many.
    """
    start = when(component)
    assert start is not None  # every event kept has a start
    end = _end_of(component, start)
    if not isinstance(start, datetime):
        last = end if not isinstance(end, datetime) else end.date()
        last = max(last - timedelta(days=1), start)
        return start.isoformat(), last.isoformat(), True
    begins = instant(start, zone, floating).astimezone(zone)
    ends = instant(end, zone, floating).astimezone(zone)
    return begins.isoformat(timespec="seconds"), ends.isoformat(timespec="seconds"), False


def is_busy(component: Component) -> bool:
    transparent = str(component.get("TRANSP", "")).upper() == "TRANSPARENT"
    cancelled = str(component.get("STATUS", "")).upper() == "CANCELLED"
    return not (transparent or cancelled)


def status_of(component: Component) -> str | None:
    value = line(component, "STATUS", 40)
    return value.lower() if value else None


def is_floating(series: Series) -> bool:
    return series.kind in ("floating", "unknown")


def recurring(series: Series) -> bool:
    return series.repeats or bool(series.overrides)


def occurrence_id(feed: ParsedFeed, series: Series, rid: str | None = None) -> str:
    base = f"{feed.name}:{series.id}"
    return f"{base}:{rid}" if rid else base


def occurrence(item: Occurrence, zone: ZoneInfo) -> models.Occurrence:
    start, end, all_day = span(item.component, zone, read_zone(item.feed, zone))
    return models.Occurrence(
        id=occurrence_id(item.feed, item.series, item.rid),
        calendar=item.feed.name,
        title=title(item.component),
        start=start,
        end=end,
        all_day=all_day,
        location=line(item.component, "LOCATION", LOCATION_CHARS),
        status=status_of(item.component),
        recurring=recurring(item.series),
        floating=is_floating(item.series),
        busy=is_busy(item.component),
        has_description=has_description(item.component),
    )


def feed_status(view: View) -> models.FeedStatus:
    copy = view.copy
    return models.FeedStatus(
        calendar=view.name,
        fetched_at=copy.fetched_at.isoformat(timespec="seconds") if copy else None,
        stale=view.stale,
        error=view.error,
        floating_zone=copy.feed.zone if copy else None,
    )


def calendar(view: View) -> models.Calendar:
    status = feed_status(view)
    if view.copy is None:
        return models.Calendar(
            calendar=view.name,
            title="",
            events=0,
            repeat_forever=0,
            earliest_start=None,
            latest_start=None,
            calendar_zone=None,
            calendar_zone_dropped=False,
            floating=0,
            unknown_zones=0,
            duplicates_dropped=0,
            set_aside={},
            status=status,
        )
    feed = view.copy.feed
    utc = ZoneInfo("UTC")
    floating = read_zone(feed, utc)
    starts = sorted(
        instant(item.start, utc, floating).astimezone(utc) for item in feed.series.values()
    )
    return models.Calendar(
        calendar=feed.name,
        title=clean_line(feed.title, TITLE_CHARS),
        events=len(feed.series),
        repeat_forever=sum(1 for item in feed.series.values() if item.forever),
        earliest_start=starts[0].date().isoformat() if starts else None,
        latest_start=starts[-1].date().isoformat() if starts else None,
        calendar_zone=feed.zone,
        calendar_zone_dropped=feed.zone_dropped,
        floating=feed.floating,
        unknown_zones=feed.unknown_zones,
        duplicates_dropped=feed.duplicates,
        set_aside=feed.set_aside(),
        status=status,
    )


def _person(prop: Any) -> models.Person:
    params = getattr(prop, "params", {}) or {}
    address = str(prop)
    if address.lower().startswith("mailto:"):
        address = address[len("mailto:") :]

    def param(name: str, limit: int) -> str | None:
        value = params.get(name)
        if value is None:
            return None
        cleaned = clean_line(str(value), limit)
        return cleaned or None

    role = param("ROLE", 40)
    status = param("PARTSTAT", 40)
    return models.Person(
        name=param("CN", NAME_CHARS),
        email=clean_line(address, NAME_CHARS) or None,
        role=role.lower() if role else None,
        status=status.lower() if status else None,
    )


def _all(component: Component, name: str) -> list[Any]:
    value = component.get(name)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _exception_count(series: Series) -> int:
    count = len(series.overrides)
    if series.master is not None:
        for exdate in _all(series.master, "EXDATE"):
            count += len(getattr(exdate, "dts", []))
    return count


def _reminders(component: Component) -> list[models.Reminder]:
    reminders = []
    for alarm in component.walk("VALARM"):
        trigger = alarm.get("TRIGGER")
        value = getattr(trigger, "dt", None)
        if isinstance(value, timedelta):
            when = clean_line(str(value), 60)
        elif isinstance(value, datetime):
            when = instant(value, ZoneInfo("UTC")).isoformat()
        else:
            when = None
        action = alarm.get("ACTION")
        reminders.append(
            models.Reminder(
                action=clean_line(str(action), 40).lower() if action is not None else None,
                trigger=when,
            )
        )
    return reminders


def _categories(component: Component) -> list[str]:
    names: list[str] = []
    for prop in _all(component, "CATEGORIES"):
        for item in getattr(prop, "cats", []):
            cleaned = clean_line(str(item), NAME_CHARS)
            if cleaned:
                names.append(cleaned)
    return names


def detail(
    view: View,
    series: Series,
    component: Component,
    zone: ZoneInfo,
    *,
    rid: str | None,
    following: models.Occurrence | None,
) -> models.EventDetail:
    assert view.copy is not None
    feed = view.copy.feed
    start, end, all_day = span(component, zone, read_zone(feed, zone))
    attendees = _all(component, "ATTENDEE")
    organizer = component.get("ORGANIZER")
    rule = rule_text(series.master)
    try:
        sequence = int(component.get("SEQUENCE", 0))
    except (TypeError, ValueError):
        sequence = 0
    return models.EventDetail(
        timezone=zone.key,
        status_of_feed=feed_status(view),
        id=occurrence_id(feed, series, rid),
        calendar=feed.name,
        title=title(component),
        start=start,
        end=end,
        all_day=all_day,
        location=line(component, "LOCATION", LOCATION_CHARS),
        status=status_of(component),
        recurring=recurring(series),
        floating=is_floating(series),
        busy=is_busy(component),
        description=description(component),
        url=line(component, "URL", URL_CHARS),
        organizer=_person(organizer) if organizer is not None else None,
        attendees=[_person(item) for item in attendees[:ATTENDEES_AT_MOST]],
        attendee_count=len(attendees),
        repeat_rule=clean_line(rule, RULE_CHARS) if rule else None,
        exceptions=_exception_count(series),
        reminders=_reminders(component),
        categories=_categories(component),
        classification=(line(component, "CLASS", 40) or "").lower() or None,
        sequence=sequence,
        created=stamp(component.get("CREATED")),
        last_modified=stamp(component.get("LAST-MODIFIED")),
        next=following,
    )
