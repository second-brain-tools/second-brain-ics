"""Reading a feed's body into series of events, and expanding them into occurrences.

The rules, each from something a real feed or a review showed:

- **Broken events are set aside before anything reads a time.** icalendar parses a
  bad value without complaint and records it in ``component.errors``; the first
  read of it then raises, quoting the raw value. Set aside, they are counted.
- **Duplicates are dropped here, not left to the expander**, which keeps the first
  in the file on a tie. A brand-new Proton share link served one feed with several
  copies of the same events. A master is one per UID, an override one per
  (UID, RECURRENCE-ID); the highest SEQUENCE wins, then the latest DTSTAMP, then
  the last in the file.
- **The calendar-wide zone (X-WR-TIMEZONE) is checked before it is used.** A
  Windows zone name is mapped to its IANA one; a name that still isn't a zone is
  dropped (and said so), since the expander would fail every series on it.
- **Floating times are counted before anything changes them.** With a usable
  X-WR-TIMEZONE they are read in that zone, as the expander does; without one, in
  the answer's zone, and marked as floating.
- **Each series is expanded on its own**, so one that fails or costs too much never
  takes the calendar with it, and UIDs from different feeds never mix.
- **Repeat rules that would cost too much are refused.** The expander walks a rule
  from its DTSTART every time, so a rule that steps every second, every minute, or
  every hour since 1900 would stall a call for many seconds. Such rules are refused
  when the feed is read; a series that still takes too long is set aside for as
  long as the process runs; and a call has a budget, after which it answers with
  what it has and says it is incomplete.
"""

import hashlib
import heapq
import math
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as day_start
from typing import Any
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar, Component
from icalendar.timezone.windows_to_olson import WINDOWS_TO_OLSON

from .config import zone_or_none
from .errors import UNREADABLE, FeedError

# How far from now a query may reach, either way. Repeat rules are judged against it.
HORIZON = timedelta(days=3660)
# A rule whose walk from DTSTART to the horizon would take more steps than this is
# refused.
MAX_STEPS = 100_000
# A series whose expansion takes longer than this in one call is set aside.
SLOW_SERIES_SECONDS = 1.0
# What one call may spend expanding, in all.
CALL_BUDGET_SECONDS = 10.0

COSTLY_FREQUENCIES = frozenset({"SECONDLY", "MINUTELY"})
_STEP_SECONDS = {
    "HOURLY": 3600.0,
    "DAILY": 86400.0,
    "WEEKLY": 7 * 86400.0,
    "MONTHLY": 28 * 86400.0,
    "YEARLY": 365 * 86400.0,
}
# BY rules that add occurrences within each step, so multiply the walk.
_EXPANDING = ("BYHOUR", "BYMINUTE", "BYSECOND")

COSTLY = "repeat rule too costly"
NEVER = "repeat rule never matches"
# The most days each month can have (February in a leap year).
_MONTH_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
BROKEN = "unreadable value"
FAILED = "could not be expanded"

# Series found too slow, by signature (UID, rule and start), so that a refetch does
# not pay for them again. Bounded: a feed that changes its rules all the time can't
# grow it.
_SLOW: dict[str, None] = {}
_SLOW_AT_MOST = 1024
# Expansion runs in worker threads, two calls at once at times.
_SLOW_LOCK = threading.Lock()


def _remember_slow(signature: str) -> None:
    with _SLOW_LOCK:
        _SLOW[signature] = None
        while len(_SLOW) > _SLOW_AT_MOST:
            del _SLOW[next(iter(_SLOW))]


def _is_slow(signature: str) -> bool:
    with _SLOW_LOCK:
        return signature in _SLOW


def series_id(uid: str) -> str:
    """The id part for a series: stable across refetches, and it says nothing of the UID."""
    return hashlib.sha256(uid.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def rid_key(value: date | datetime) -> str:
    """An occurrence's RECURRENCE-ID as an id part: a date, a UTC instant, or a floating time."""
    # Written out by hand: strftime's %Y doesn't pad a year before 1000 everywhere.
    if not isinstance(value, datetime):
        return f"{value.year:04d}{value.month:02d}{value.day:02d}"
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    text = (
        f"{value.year:04d}{value.month:02d}{value.day:02d}"
        f"T{value.hour:02d}{value.minute:02d}{value.second:02d}"
    )
    return text + "Z" if value.tzinfo is not None else text


def parse_rid_key(text: str) -> date | datetime | None:
    """The value :func:`rid_key` made ``text`` from, or None when it isn't one."""
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
        if len(text) == 16 and text.endswith("Z"):
            return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if len(text) == 15:
            return datetime.strptime(text, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return None


def instant(value: date | datetime, zone: ZoneInfo, floating: ZoneInfo | None = None) -> datetime:
    """``value`` as an aware datetime.

    A date starts its day in ``zone``; a time without a zone is read in ``floating``
    (the feed's X-WR-TIMEZONE, see :func:`read_zone`), else in ``zone``.
    """
    if not isinstance(value, datetime):
        return datetime.combine(value, day_start(), zone)
    if value.tzinfo is None:
        return value.replace(tzinfo=floating or zone)
    return value


def when(component: Component, name: str = "DTSTART") -> date | datetime | None:
    """A date or date-time property's value, or None."""
    value = getattr(component.get(name), "dt", None)
    return value if isinstance(value, date) else None


def rule_of(component: Component | None) -> dict[str, list[Any]] | None:
    """The RRULE as its parts (``{"FREQ": ["WEEKLY"], ...}``), or None."""
    if component is None:
        return None
    rule = component.get("RRULE")
    return rule if isinstance(rule, dict) else None


def rule_text(component: Component | None) -> str:
    """The RRULE as written, or the empty string."""
    rule = component.get("RRULE") if component is not None else None
    to_ical = getattr(rule, "to_ical", None)
    return to_ical().decode() if to_ical is not None else ""


def _value(component: Component, name: str) -> Any:
    """A property's value, or None; only for components with no recorded errors."""
    prop = component.get(name)
    if prop is None:
        return None
    return getattr(prop, "dt", prop)


def _sequence(component: Component) -> int:
    try:
        return int(component.get("SEQUENCE", 0))
    except (TypeError, ValueError):
        return 0


def _stamp(component: Component) -> float:
    value = _value(component, "DTSTAMP")
    if isinstance(value, datetime):
        return instant(value, ZoneInfo("UTC")).timestamp()
    return -math.inf


@dataclass
class Series:
    """One UID's master event and its changed occurrences."""

    id: str
    uid: str
    master: Component | None
    overrides: dict[str, Component]
    # How its start is written: "zoned", "floating", "date", or "unknown" (a TZID
    # this machine doesn't know, with no definition in the feed: read as floating).
    kind: str
    # Why it is set aside, or None.
    unusable: str | None = None
    _calendar: Calendar | None = field(default=None, repr=False)

    @property
    def components(self) -> list[Component]:
        head = [self.master] if self.master is not None else []
        return head + list(self.overrides.values())

    @property
    def first(self) -> Component:
        return self.components[0]

    @property
    def start(self) -> date | datetime:
        value = when(self.first)
        assert value is not None  # parse_feed keeps only events with a start
        return value

    @property
    def repeats(self) -> bool:
        return self.master is not None and bool(
            self.master.get("RRULE") or self.master.get("RDATE")
        )

    @property
    def forever(self) -> bool:
        rule = rule_of(self.master)
        return rule is not None and "COUNT" not in rule and "UNTIL" not in rule

    @property
    def signature(self) -> str:
        return f"{self.uid}\x00{rule_text(self.master)}\x00{rid_key(self.start)}"

    def calendar(self, zone_name: str | None) -> Calendar:
        """A calendar holding just this series, for the expander."""
        if self._calendar is None:
            calendar = Calendar()
            calendar.add("PRODID", "-//second-brain-ics//one series//EN")
            calendar.add("VERSION", "2.0")
            if zone_name:
                calendar.add("X-WR-TIMEZONE", zone_name)
            for component in self.components:
                calendar.add_component(component)
            self._calendar = calendar
        return self._calendar


@dataclass
class ParsedFeed:
    """A feed's events by series, and what reading it found."""

    name: str
    title: str
    # The usable calendar-wide zone, after mapping a Windows name; None if absent
    # or unusable.
    zone: str | None
    # True when the feed named a zone that isn't one, which was dropped.
    zone_dropped: bool
    series: dict[str, Series]
    duplicates: int
    unreadable: dict[str, int]
    size: int

    @property
    def floating(self) -> int:
        return sum(1 for item in self.series.values() if item.kind == "floating")

    @property
    def unknown_zones(self) -> int:
        return sum(1 for item in self.series.values() if item.kind == "unknown")

    @property
    def usable(self) -> list[Series]:
        return [item for item in self.series.values() if item.unusable is None]

    def set_aside(self) -> dict[str, int]:
        counts = dict(self.unreadable)
        for item in self.series.values():
            if item.unusable is not None:
                counts[item.unusable] = counts.get(item.unusable, 0) + 1
        return counts


def usable_zone(raw: str) -> str | None:
    """``raw`` (an X-WR-TIMEZONE) as an IANA zone name, or None."""
    name = raw.strip()
    if zone_or_none(name) is not None:
        return name
    mapped = WINDOWS_TO_OLSON.get(name)
    if mapped and zone_or_none(mapped) is not None:
        return mapped
    return None


def _kind(component: Component) -> str:
    prop = component.get("DTSTART")
    value = getattr(prop, "dt", None)
    if not isinstance(value, datetime):
        return "date"
    if value.tzinfo is not None:
        return "zoned"
    return "unknown" if prop.params.get("TZID") else "floating"


def _broken(component: Component) -> bool:
    """True when ``component`` can't be read safely, before any of its times is touched.

    icalendar records a bad value in ``component.errors`` and raises, quoting it, on
    first use. A property that may appear once but appears twice comes back as a
    list, which nothing downstream expects; several RRULEs, or an EXRULE (removed
    from iCalendar in RFC 5545), would also escape the cost check.
    """
    if component.errors:
        return True
    for name in ("DTSTART", "DTEND", "DURATION", "RECURRENCE-ID", "RRULE", "UID", "SEQUENCE"):
        if isinstance(component.get(name), list):
            return True
    if component.get("DTSTART") is None or component.get("EXRULE") is not None:
        return True
    return False


def _numbers(rule: dict[str, list[Any]], name: str) -> list[int] | None:
    try:
        return [int(value) for value in rule.get(name) or []]
    except (TypeError, ValueError):
        return None


def _never_matches(rule: dict[str, list[Any]]) -> bool:
    """True for a rule whose month days no listed month has (``BYMONTH=2;BYMONTHDAY=30``).

    The expander looks for a first match up to the end of the calendar, which costs
    seconds for each such rule on every call. Other rules that can never match are
    caught at run time, by the time each one's expansion takes.
    """
    days = _numbers(rule, "BYMONTHDAY")
    months = _numbers(rule, "BYMONTH")
    if not days or days is None or months is None:
        return False
    if any(day == 0 or abs(day) > 31 for day in days):
        return True
    months = months or list(range(1, 13))
    if any(month < 1 or month > 12 for month in months):
        return True
    longest = max(_MONTH_DAYS[month - 1] for month in months)
    return all(abs(day) > longest for day in days)


def costly_reason(master: Component | None, start: date | datetime, now: datetime) -> str | None:
    """Why ``master``'s repeat rule would cost too much to expand, or None."""
    rule = rule_of(master)
    if rule is None:
        return None
    frequency = str((rule.get("FREQ") or [""])[0]).upper()
    if frequency in COSTLY_FREQUENCIES:
        return COSTLY
    step = _STEP_SECONDS.get(frequency)
    if step is None:
        return COSTLY
    try:
        interval = max(int((rule.get("INTERVAL") or [1])[0]), 1)
    except (TypeError, ValueError):
        interval = 1
    if _never_matches(rule):
        return NEVER
    first = instant(start, ZoneInfo("UTC"))
    span = (now + HORIZON - first).total_seconds()
    steps = max(span, 0.0) / (step * interval)
    for name in _EXPANDING:
        steps *= max(len(rule.get(name) or []), 1)
    return COSTLY if steps > MAX_STEPS else None


Ranked = dict[Any, tuple[tuple[int, float, int], Component]]


def _sort_in(
    component: Component,
    index: int,
    rid_zone: ZoneInfo | None,
    masters: Ranked,
    overrides: Ranked,
) -> bool | None:
    """File ``component`` as a master or an override: None when broken, False for a duplicate."""
    if _broken(component):
        return None
    when(component)  # reads the start once, so a value that can't be read fails here
    uid = str(component.get("UID") or "") or (
        "no-uid:" + hashlib.sha256(component.to_ical()).hexdigest()
    )
    rank = (_sequence(component), _stamp(component), index)
    rid = when(component, "RECURRENCE-ID")
    if rid is None:
        key: Any = uid
        table = masters
    else:
        if rid_zone is not None and isinstance(rid, datetime) and rid.tzinfo is None:
            rid = rid.replace(tzinfo=rid_zone)
        key = (uid, rid_key(rid))
        table = overrides
    held = table.get(key)
    if held is not None and held[0] > rank:
        return False
    table[key] = (rank, component)
    return held is None


def parse_feed(name: str, body: bytes, *, now: datetime | None = None) -> ParsedFeed:
    """``body`` as series. A body that isn't a calendar at all is a :class:`FeedError`."""
    now = now or datetime.now(UTC)
    calendar: Calendar | None = None
    try:
        parsed = Calendar.from_ical(body)
        if isinstance(parsed, Calendar):
            calendar = parsed
    except Exception:  # noqa: BLE001 - its text can quote the feed; the reason is fixed
        calendar = None
    if calendar is None:
        raise FeedError(name, UNREADABLE)

    raw_zone = str(calendar.get("X-WR-TIMEZONE") or "")
    zone = usable_zone(raw_zone) if raw_zone.strip() else None
    # The expander reads a floating RECURRENCE-ID in that zone, so the key made
    # here does too: an override and the occurrence it replaces get one id.
    rid_zone = ZoneInfo(zone) if zone else None

    unreadable: dict[str, int] = {}
    masters: Ranked = {}
    overrides: Ranked = {}
    duplicates = 0
    for index, component in enumerate(calendar.walk("VEVENT")):
        try:
            kept = _sort_in(component, index, rid_zone, masters, overrides)
        except Exception:  # noqa: BLE001 - one odd event never fails the feed; no text kept
            kept = None
        if kept is None:
            unreadable[BROKEN] = unreadable.get(BROKEN, 0) + 1
        elif not kept:
            duplicates += 1

    by_uid: dict[str, dict[str, Component]] = {}
    for (uid, override_key), (_, component) in overrides.items():
        by_uid.setdefault(uid, {})[override_key] = component
    series: dict[str, Series] = {}
    for uid in sorted(set(masters) | set(by_uid)):
        master = masters[uid][1] if uid in masters else None
        changed = dict(sorted(by_uid.get(uid, {}).items()))
        item = Series(
            id=series_id(uid),
            uid=uid,
            master=master,
            overrides=changed,
            kind=_kind(master if master is not None else next(iter(changed.values()))),
        )
        item.unusable = costly_reason(master, item.start, now)
        if item.unusable is None and _is_slow(item.signature):
            item.unusable = COSTLY
        series[item.id] = item

    return ParsedFeed(
        name=name,
        title=str(calendar.get("X-WR-CALNAME") or ""),
        zone=zone,
        zone_dropped=bool(raw_zone.strip()) and zone is None,
        series=series,
        duplicates=duplicates,
        unreadable=unreadable,
        size=len(body),
    )


@dataclass
class Budget:
    """What a call may still spend expanding; ``incomplete`` once it ran out."""

    seconds: float = CALL_BUDGET_SECONDS
    clock: Callable[[], float] = time.monotonic
    incomplete: bool = False
    _deadline: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._deadline = self.clock() + self.seconds

    def spent(self) -> bool:
        if self.clock() >= self._deadline:
            self.incomplete = True
        return self.incomplete


@dataclass(frozen=True)
class Occurrence:
    """One occurrence of a series, with the key that orders all answers."""

    feed: ParsedFeed = field(compare=False)
    series: Series = field(compare=False)
    component: Component = field(compare=False)
    start: datetime
    rid: str
    key: tuple[float, str, str, str]


def _start_of(component: Component) -> date | datetime:
    value = when(component)
    assert value is not None  # every event kept, and every occurrence, has a start
    return value


def read_zone(feed: ParsedFeed, zone: ZoneInfo) -> ZoneInfo:
    """The zone a floating time in ``feed`` is read in: its X-WR-TIMEZONE, else ``zone``."""
    return ZoneInfo(feed.zone) if feed.zone else zone


def series_occurrences(
    feed: ParsedFeed,
    series: Series,
    start: datetime,
    end: datetime,
    zone: ZoneInfo,
    budget: Budget,
) -> list[Occurrence]:
    """``series``' occurrences that end after ``start`` and start before ``end``, by key.

    One bounded walk (the expander's ``between``), timed: a walk over a second sets
    the series aside for as long as the process runs. ``after`` would walk again
    for each window it tries, up to year 9999 when nothing matches.
    """
    if series.unusable is not None:
        return []
    if budget.spent():
        return []
    query = recurring_ical_events.of(series.calendar(feed.zone), skip_bad_series=True)
    floating = read_zone(feed, zone)
    began = time.monotonic()
    found: list[Occurrence] = []
    try:
        for component in query.between(start, end):
            begins = instant(_start_of(component), zone, floating).astimezone(zone)
            if begins >= end:
                continue
            rid = when(component, "RECURRENCE-ID")
            key_part = rid_key(rid if rid is not None else _start_of(component))
            found.append(
                Occurrence(
                    feed=feed,
                    series=series,
                    component=component,
                    start=begins,
                    rid=key_part,
                    key=(begins.timestamp(), feed.name, series.id, key_part),
                )
            )
    except Exception:  # noqa: BLE001 - one series never fails the calendar; no text kept
        series.unusable = FAILED
        budget.incomplete = True
        return []
    if time.monotonic() - began > SLOW_SERIES_SECONDS:
        _remember_slow(series.signature)
        series.unusable = COSTLY
    found.sort(key=lambda item: item.key)
    return found


def occurrences(
    feeds: list[ParsedFeed],
    start: datetime,
    end: datetime,
    zone: ZoneInfo,
    budget: Budget,
    *,
    after: tuple[float, str, str, str] | None = None,
    keep: Callable[[Occurrence], bool] | None = None,
) -> Iterator[Occurrence]:
    """Every feed's occurrences in ``[start, end)``, in one order, from after the key ``after``."""
    streams = [
        series_occurrences(feed, item, start, end, zone, budget)
        for feed in feeds
        for item in sorted(feed.usable, key=lambda series: series.id)
    ]
    for occurrence in heapq.merge(*streams, key=lambda item: item.key):
        if after is not None and occurrence.key <= after:
            continue
        if keep is not None and not keep(occurrence):
            continue
        yield occurrence


def find_occurrence(
    feed: ParsedFeed, series: Series, rid: str, zone: ZoneInfo, budget: Budget
) -> Occurrence | None:
    """The occurrence of ``series`` whose RECURRENCE-ID key is ``rid``, or None."""
    value = parse_rid_key(rid)
    if value is None:
        return None
    override = series.overrides.get(rid)
    target = instant(
        _start_of(override) if override is not None else value, zone, read_zone(feed, zone)
    )
    begin = target - timedelta(days=2)
    for occurrence in series_occurrences(
        feed, series, begin, target + timedelta(days=2), zone, budget
    ):
        if occurrence.rid == rid:
            return occurrence
    return None


NEXT_WITHIN = timedelta(days=400)


def next_occurrence(
    feed: ParsedFeed, series: Series, now: datetime, zone: ZoneInfo, budget: Budget
) -> Occurrence | None:
    """``series``' first occurrence that hasn't ended by ``now``.

    A repeating series is looked at for the next 400 days; a series whose first
    start is further ahead, at that start.
    """
    found = series_occurrences(feed, series, now, now + NEXT_WITHIN, zone, budget)
    if found:
        return found[0]
    first = instant(series.start, zone, read_zone(feed, zone))
    if first > now + NEXT_WITHIN and first < now + HORIZON:
        later = series_occurrences(feed, series, first, first + timedelta(days=2), zone, budget)
        return later[0] if later else None
    return None
