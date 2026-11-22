"""What the tools answer. Every answer that carries event text puts the notice first."""

from pydantic import BaseModel, Field
from second_brain_core.textclean import outside_text_notice

UNTRUSTED_NOTICE = outside_text_notice(
    "Calendar names, event titles, places, descriptions and attendee names",
    "whoever wrote each event, and for an invitation that is someone else",
)


class FeedStatus(BaseModel):
    """How current one feed's data is in this answer."""

    calendar: str
    # When the data was fetched (UTC, ISO 8601). Missing when there is no copy.
    fetched_at: str | None = None
    # True when the latest fetch failed and an older copy is being served.
    stale: bool = False
    # Why the latest fetch failed.
    error: str | None = None
    # The zone the feed's times without a zone were read in (its X-WR-TIMEZONE).
    floating_zone: str | None = None


class Calendar(BaseModel):
    calendar: str
    title: str
    events: int = Field(description="Series in the feed: a repeating event counts once")
    repeat_forever: int
    earliest_start: str | None
    latest_start: str | None = Field(
        description="The latest first start of any series; a repeating series runs on after it"
    )
    calendar_zone: str | None = Field(description="The feed's X-WR-TIMEZONE, when usable")
    calendar_zone_dropped: bool = Field(
        description="True when the feed named a zone that isn't one; it was ignored"
    )
    floating: int = Field(description="Series written without a time zone")
    unknown_zones: int = Field(
        description="Series in a time zone this server doesn't know; read as having none"
    )
    duplicates_dropped: int
    set_aside: dict[str, int] = Field(description="Events not shown, by reason")
    status: FeedStatus


class CalendarList(BaseModel):
    notice: str = UNTRUSTED_NOTICE
    calendars: list[Calendar]


class Occurrence(BaseModel):
    """One occurrence of an event."""

    id: str = Field(description="calendar_read takes it; it survives a refetch")
    calendar: str
    title: str
    start: str = Field(description="ISO 8601; a date alone for an all-day event")
    end: str = Field(
        description="ISO 8601; for an all-day event the LAST day it covers (inclusive)"
    )
    all_day: bool
    location: str | None = None
    status: str | None = None
    recurring: bool
    floating: bool = Field(description="Written without a time zone, read in the zone named")
    busy: bool = Field(description="False when the event is marked as free time")
    has_description: bool


class Events(BaseModel):
    notice: str = UNTRUSTED_NOTICE
    timezone: str
    start: str
    end: str
    feeds: list[FeedStatus]
    occurrences: list[Occurrence]
    more: bool
    cursor: str | None = Field(default=None, description="Pass it back for the next page")
    incomplete: bool = Field(
        default=False,
        description="True when expanding took too long and some occurrences may be missing",
    )


class SeriesMatch(BaseModel):
    """A matching event, once, with its next occurrence (or its first, if none is ahead)."""

    id: str
    calendar: str
    title: str
    recurring: bool
    next: Occurrence | None = Field(description="The next occurrence from now, if any")
    first_start: str


class SearchResults(BaseModel):
    notice: str = UNTRUSTED_NOTICE
    timezone: str
    feeds: list[FeedStatus]
    # With a range: matching occurrences in it. Without: matching events, once each.
    occurrences: list[Occurrence] = []
    events: list[SeriesMatch] = []
    more: bool
    incomplete: bool = False


class Person(BaseModel):
    name: str | None = None
    email: str | None = None
    role: str | None = None
    status: str | None = None


class Reminder(BaseModel):
    action: str | None
    trigger: str | None


class EventDetail(BaseModel):
    """One event (or one occurrence of it) in full."""

    notice: str = UNTRUSTED_NOTICE
    timezone: str
    status_of_feed: FeedStatus
    id: str
    calendar: str
    title: str
    start: str | None
    end: str | None
    all_day: bool
    location: str | None = None
    status: str | None = None
    recurring: bool
    floating: bool
    busy: bool
    description: str | None = None
    url: str | None = None
    organizer: Person | None = None
    attendees: list[Person] = []
    attendee_count: int = 0
    repeat_rule: str | None = None
    exceptions: int = Field(0, description="Dates cancelled or changed in the series")
    reminders: list[Reminder] = []
    categories: list[str] = []
    classification: str | None = None
    sequence: int = 0
    created: str | None = None
    last_modified: str | None = None
    next: Occurrence | None = Field(
        default=None, description="For an event read by its series id: the next occurrence"
    )
