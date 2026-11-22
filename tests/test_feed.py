"""Reading feeds into series, and expanding them: the shapes real feeds have."""

import time
from datetime import UTC as UTC_TZ
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from build import calendar, event, simple
from conftest import feed_text

from second_brain_ics import feed as feed_module
from second_brain_ics.errors import FeedError
from second_brain_ics.feed import (
    BROKEN,
    COSTLY,
    NEVER,
    Budget,
    find_occurrence,
    next_occurrence,
    occurrences,
    parse_feed,
    parse_rid_key,
    rid_key,
    series_id,
    usable_zone,
)

CHICAGO = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")
NOW = datetime(2026, 11, 22, tzinfo=UTC_TZ)


def at(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, zone: ZoneInfo = CHICAGO
) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=zone)


def titles(feed: feed_module.ParsedFeed, start: datetime, end: datetime) -> list[str]:
    return [
        str(item.component.get("SUMMARY"))
        for item in occurrences([feed], start, end, CHICAGO, Budget())
    ]


# -- the spike's feed: every awkward shape in one -------------------------------


def test_the_spike_feed_reads_into_its_series() -> None:
    parsed = parse_feed("spike", feed_text("spike.ics"), now=NOW)
    assert parsed.title == "Spike"
    assert parsed.zone == "America/Chicago"
    assert len(parsed.series) == 5
    assert parsed.duplicates == 0
    assert parsed.unreadable == {}
    assert parsed.floating == 1


def test_a_weekly_series_with_a_moved_and_a_cancelled_date() -> None:
    parsed = parse_feed("spike", feed_text("spike.ics"), now=NOW)
    got = [
        (item.start, str(item.component.get("SUMMARY")))
        for item in occurrences([parsed], at(2026, 12, 1), at(2027, 1, 1), CHICAGO, Budget())
        if "Weekly" in str(item.component.get("SUMMARY"))
    ]
    assert got == [
        (at(2026, 12, 2, 9), "Weekly series"),
        (at(2026, 12, 9, 9), "Weekly series"),
        (at(2026, 12, 17, 14), "Weekly series (moved)"),  # moved from the 16th
        (at(2026, 12, 23, 9), "Weekly series"),
        # the 30th is cancelled
    ]


def test_the_series_keeps_its_local_time_across_the_daylight_saving_change() -> None:
    parsed = parse_feed("spike", feed_text("spike.ics"), now=NOW)
    weekly = [
        item.start
        for item in occurrences([parsed], at(2027, 3, 1), at(2027, 3, 25), CHICAGO, Budget())
        if item.series.uid == "weekly-series@spike"
    ]
    assert [moment.hour for moment in weekly] == [9, 9, 9, 9]
    offsets = {moment.utcoffset() for moment in weekly}
    assert offsets == {timedelta(hours=-6), timedelta(hours=-5)}


def test_the_moved_occurrence_keeps_the_id_of_the_date_it_replaces() -> None:
    parsed = parse_feed("spike", feed_text("spike.ics"), now=NOW)
    moved = [
        item
        for item in occurrences([parsed], at(2026, 12, 1), at(2027, 1, 1), CHICAGO, Budget())
        if "moved" in str(item.component.get("SUMMARY"))
    ]
    assert [item.rid for item in moved] == ["20261216T150000Z"]  # 09:00 Chicago, the 16th
    series = parsed.series[series_id("weekly-series@spike")]
    found = find_occurrence(parsed, series, "20261216T150000Z", CHICAGO, Budget())
    assert found is not None and found.start == at(2026, 12, 17, 14)


def test_a_floating_time_is_read_in_the_feeds_zone() -> None:
    parsed = parse_feed("spike", feed_text("spike.ics"), now=NOW)
    got = [
        item.start
        for item in occurrences([parsed], at(2027, 3, 1), at(2027, 3, 2), UTC, Budget())
        if item.series.uid == "floating@spike"
    ]
    assert got == [at(2027, 3, 1, 7)]  # 07:00 in Chicago, though the answer is in UTC


def test_a_floating_time_without_a_feed_zone_is_read_in_the_answers_zone() -> None:
    parsed = parse_feed(
        "f",
        calendar(event("f@t", "DTSTART:20270301T070000", end="DTEND:20270301T080000")),
        now=NOW,
    )
    assert parsed.zone is None and parsed.floating == 1
    berlin = ZoneInfo("Europe/Berlin")
    got = [
        item.start
        for item in occurrences([parsed], at(2027, 2, 28), at(2027, 3, 3), berlin, Budget())
    ]
    assert got == [datetime(2027, 3, 1, 7, tzinfo=berlin)]


# -- duplicates (a brand-new Proton link served them) ---------------------------


def test_duplicate_masters_and_overrides_keep_the_newest() -> None:
    parsed = parse_feed("p", feed_text("proton.ics"), now=NOW)
    assert parsed.duplicates == 3  # two extra masters, one extra override
    standup = parsed.series[series_id("standup@proton.test")]
    assert str(standup.master.get("SUMMARY")) == "Stand-up"  # type: ignore[union-attr]
    week = titles(parsed, at(2026, 12, 1), at(2026, 12, 4))
    assert week == ["Stand-up", "Stand-up", "Stand-up (late)"]


def test_on_a_tie_the_last_copy_in_the_file_wins() -> None:
    first = event("u@t", "DTSTART:20270105T150000Z", title="first")
    last = event("u@t", "DTSTART:20270105T150000Z", title="last")
    parsed = parse_feed("t", calendar(first, last), now=NOW)
    assert titles(parsed, at(2027, 1, 1), at(2027, 1, 10)) == ["last"]


# -- zones ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "usable"),
    [
        ("America/Chicago", "America/Chicago"),
        ("Central Standard Time", "America/Chicago"),
        ("W. Europe Standard Time", "Europe/Berlin"),
        ("Not/AZone", None),
        ("", None),
    ],
)
def test_a_calendar_zone_is_checked_and_windows_names_mapped(raw: str, usable: str | None) -> None:
    assert usable_zone(raw) == usable


def test_an_unusable_calendar_zone_is_dropped_and_said_so() -> None:
    body = calendar(
        event("f@t", "DTSTART:20270301T070000", end="DTEND:20270301T080000"),
        event("z@t", "DTSTART:20270302T070000Z"),
        zone="Garbage/Zone",
    )
    parsed = parse_feed("t", body, now=NOW)
    assert parsed.zone is None and parsed.zone_dropped is True
    # Without it, every series still expands.
    assert len(list(occurrences([parsed], at(2027, 2, 28), at(2027, 3, 5), CHICAGO, Budget()))) == 2


def test_a_windows_calendar_zone_is_used_mapped() -> None:
    body = calendar(event("f@t", "DTSTART:20270301T070000"), zone="Central Standard Time")
    parsed = parse_feed("t", body, now=NOW)
    assert parsed.zone == "America/Chicago"
    got = [
        item.start for item in occurrences([parsed], at(2027, 2, 28), at(2027, 3, 3), UTC, Budget())
    ]
    assert got == [at(2027, 3, 1, 7)]


def test_an_unknown_tzid_is_counted_apart_from_floating() -> None:
    body = calendar(event("u@t", "DTSTART;TZID=Nowhere/Zone:20270301T070000"))
    parsed = parse_feed("t", body, now=NOW)
    assert parsed.unknown_zones == 1 and parsed.floating == 0


def test_a_zone_defined_only_in_the_feed_is_used() -> None:
    vtimezone = "\r\n".join(
        [
            "BEGIN:VTIMEZONE",
            "TZID:Custom Office Time",
            "BEGIN:STANDARD",
            "DTSTART:19700101T000000",
            "TZOFFSETFROM:+0300",
            "TZOFFSETTO:+0300",
            "END:STANDARD",
            "END:VTIMEZONE",
        ]
    )
    body = calendar(event("c@t", "DTSTART;TZID=Custom Office Time:20270301T120000"), head=vtimezone)
    parsed = parse_feed("t", body, now=NOW)
    got = [
        item.start for item in occurrences([parsed], at(2027, 2, 28), at(2027, 3, 3), UTC, Budget())
    ]
    assert [moment.astimezone(UTC_TZ).hour for moment in got] == [9]


# -- ends -------------------------------------------------------------------------


def test_duration_and_missing_ends_follow_the_rfc() -> None:
    body = calendar(
        event("d@t", "DTSTART:20270301T070000Z", "DURATION:PT90M"),
        event("a@t", "DTSTART;VALUE=DATE:20270302"),
    )
    parsed = parse_feed("t", body, now=NOW)
    got = {
        item.series.uid: (item.component.end)  # type: ignore[attr-defined]
        for item in occurrences([parsed], at(2027, 2, 28), at(2027, 3, 5), UTC, Budget())
    }
    assert got["d@t"] == datetime(2027, 3, 1, 8, 30, tzinfo=UTC)
    assert got["a@t"] == date(2027, 3, 3)


# -- what can't be read, or would cost too much ----------------------------------


def test_a_broken_event_is_set_aside_and_the_rest_read() -> None:
    body = calendar(
        event("bad@t", "DTSTART:2027NOTADATE"),
        event("rule@t", "DTSTART:20270301T070000Z", "RRULE:FREQ=FORTNIGHTLY"),
        event("two-starts@t", "DTSTART:20270301T070000Z", "DTSTART:20270302T070000Z"),
        event(
            "two-rules@t",
            "DTSTART:19000101T090000Z",
            "RRULE:FREQ=SECONDLY",
            "RRULE:FREQ=SECONDLY",
        ),
        event("exrule@t", "DTSTART:20270301T070000Z", "RRULE:FREQ=DAILY", "EXRULE:FREQ=WEEKLY"),
        event(
            "two-rids@t",
            "DTSTART:20270301T070000Z",
            "RECURRENCE-ID:20270301T070000Z",
            "RECURRENCE-ID:20270302T070000Z",
        ),
        event(
            "year-one@t",
            "DTSTART;TZID=Asia/Tokyo:00010101T010000",
            "RECURRENCE-ID;TZID=Asia/Tokyo:00010101T010000",
        ),
        event("good@t", "DTSTART:20270301T070000Z"),
    )
    parsed = parse_feed("t", body, now=NOW)
    assert parsed.unreadable == {BROKEN: 7}
    assert [item.uid for item in parsed.series.values()] == ["good@t"]


@pytest.mark.parametrize(
    "rule",
    [
        "RRULE:FREQ=SECONDLY",
        "RRULE:FREQ=MINUTELY",
        "RRULE:FREQ=MINUTELY;INTERVAL=1000",
        "RRULE:FREQ=DAILY;BYHOUR=0,1,2,3,4,5,6,7,8,9,10,11;BYMINUTE=0,15,30,45",
    ],
)
def test_a_costly_repeat_rule_is_refused_when_read(rule: str) -> None:
    body = calendar(event("hostile@t", "DTSTART:20260101T000000Z", rule))
    parsed = parse_feed("t", body, now=NOW)
    assert parsed.set_aside() == {COSTLY: 1}
    assert list(occurrences([parsed], at(2027, 1, 1), at(2027, 1, 2), UTC, Budget())) == []


def test_hourly_since_1900_is_refused_but_hourly_this_year_is_not() -> None:
    old = event("old@t", "DTSTART:19000101T000000Z", "RRULE:FREQ=HOURLY")
    new = event("new@t", "DTSTART:20261101T000000Z", "RRULE:FREQ=HOURLY;COUNT=48")
    parsed = parse_feed("t", calendar(old, new), now=NOW)
    assert parsed.series[series_id("old@t")].unusable == COSTLY
    assert parsed.series[series_id("new@t")].unusable is None


def test_a_slow_series_is_set_aside_and_remembered_across_refetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feed_module, "SLOW_SERIES_SECONDS", -1.0)  # every walk is "slow"
    monkeypatch.setattr(feed_module, "_SLOW", {})
    body = calendar(event("w@t", "DTSTART:20261202T150000Z", "RRULE:FREQ=WEEKLY"))
    parsed = parse_feed("t", body, now=NOW)
    got = list(occurrences([parsed], at(2026, 12, 1), at(2027, 1, 1), UTC, Budget()))
    assert len(got) == 5  # what the slow walk found is still given
    assert parsed.series[series_id("w@t")].unusable == COSTLY
    again = parse_feed("t", body, now=NOW)
    assert again.series[series_id("w@t")].unusable == COSTLY


def test_a_long_running_weekday_series_stays_shown_for_a_whole_year() -> None:
    """A stand-up since 2006: one bounded walk, not a walk per window."""
    rule = "RRULE:FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"
    body = calendar(event("s@t", "DTSTART;TZID=America/Chicago:20060104T090000", rule))
    parsed = parse_feed("t", body, now=NOW)
    started = time.monotonic()
    got = list(occurrences([parsed], at(2026, 11, 22), at(2027, 11, 22), CHICAGO, Budget()))
    assert len(got) == 260 and time.monotonic() - started < 1.0  # 52 weeks of weekdays
    assert parsed.series[series_id("s@t")].unusable is None


@pytest.mark.parametrize(
    "rule",
    [
        "RRULE:FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30",
        "RRULE:FREQ=HOURLY;BYMONTH=4,6;BYMONTHDAY=31",
        "RRULE:FREQ=MONTHLY;BYMONTHDAY=32",
    ],
)
def test_a_rule_that_never_matches_is_refused_when_read(rule: str) -> None:
    parsed = parse_feed("t", calendar(event("n@t", "DTSTART:19000101T090000Z", rule)), now=NOW)
    assert parsed.set_aside() == {NEVER: 1}
    started = time.monotonic()
    assert list(occurrences([parsed], at(2026, 11, 22), at(2027, 11, 22), UTC, Budget())) == []
    assert time.monotonic() - started < 0.5


def test_a_rule_that_can_match_is_kept() -> None:
    rule = "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29"  # leap days
    parsed = parse_feed("t", calendar(event("l@t", "DTSTART:20240229T090000Z", rule)), now=NOW)
    got = list(occurrences([parsed], at(2027, 11, 22), at(2028, 11, 22), UTC, Budget()))
    assert [item.start.date() for item in got] == [date(2028, 2, 29)]


def test_a_call_that_runs_out_of_budget_says_it_is_incomplete() -> None:
    body = calendar(*(event(f"e{n}@t", f"DTSTART:2027010{n}T150000Z") for n in range(1, 6)))
    parsed = parse_feed("t", body, now=NOW)
    budget = Budget(seconds=0.0)
    assert list(occurrences([parsed], at(2027, 1, 1), at(2027, 1, 10), UTC, budget)) == []
    assert budget.incomplete is True


def test_a_body_that_is_no_calendar_is_a_feed_error() -> None:
    with pytest.raises(FeedError, match="could not be read as a calendar"):
        parse_feed("t", b"BEGIN:VCALENDAR\r\nthis is not\r\n", now=NOW)


# -- merging and ids --------------------------------------------------------------


def test_all_day_floating_and_zoned_starts_merge_in_one_order() -> None:
    body = calendar(
        event("a@t", "DTSTART;VALUE=DATE:20270301"),
        event("f@t", "DTSTART:20270301T070000"),
        event("z@t", "DTSTART:20270301T050000Z"),
    )
    parsed = parse_feed("t", body, now=NOW)
    order = [
        item.series.uid
        for item in occurrences([parsed], at(2027, 2, 28), at(2027, 3, 3), CHICAGO, Budget())
    ]
    # 05:00Z is 23:00 the day before in Chicago; then the day itself; then 07:00 floating.
    assert order == ["z@t", "a@t", "f@t"]


def test_two_feeds_with_the_same_uid_never_mix() -> None:
    one = parse_feed("one", simple("same@t", "20270105T150000Z", title="one"), now=NOW)
    two = parse_feed("two", simple("same@t", "20270105T150000Z", title="two"), now=NOW)
    got = [
        (item.feed.name, str(item.component.get("SUMMARY")))
        for item in occurrences([one, two], at(2027, 1, 1), at(2027, 1, 10), UTC, Budget())
    ]
    assert got == [("one", "one"), ("two", "two")]


def test_ids_survive_a_reordered_feed() -> None:
    a = event("a@t", "DTSTART:20270105T150000Z")
    b = event("b@t", "DTSTART:20270106T150000Z", "RRULE:FREQ=DAILY;COUNT=3")
    first = parse_feed("t", calendar(a, b), now=NOW)
    second = parse_feed("t", calendar(b, a), now=NOW)

    def ids(parsed: feed_module.ParsedFeed) -> list[tuple[str, str]]:
        return [
            (item.series.id, item.rid)
            for item in occurrences([parsed], at(2027, 1, 1), at(2027, 1, 10), UTC, Budget())
        ]

    assert ids(first) == ids(second) and len(ids(first)) == 4


@pytest.mark.parametrize(
    "value",
    [
        date(2027, 3, 1),
        datetime(2027, 3, 1, 7, 30),
        datetime(2027, 3, 1, 7, 30, tzinfo=UTC),
        date(999, 1, 2),
        datetime(999, 1, 2, 3, 4, 5),
    ],
)
def test_a_rid_key_reads_back(value: date) -> None:
    assert parse_rid_key(rid_key(value)) == value


@pytest.mark.parametrize(
    "text", ["", "2027", "20271301", "20270301T0730", "x" * 16, "20270301T073000Q"]
)
def test_a_bad_rid_key_reads_as_none(text: str) -> None:
    assert parse_rid_key(text) is None


def test_the_next_occurrence_from_now() -> None:
    body = calendar(event("w@t", "DTSTART:20261202T150000Z", "RRULE:FREQ=WEEKLY;COUNT=10"))
    parsed = parse_feed("t", body, now=NOW)
    series = parsed.series[series_id("w@t")]
    following = next_occurrence(parsed, series, at(2026, 12, 10, zone=UTC), UTC, Budget())
    assert following is not None and following.start == at(2026, 12, 16, 15, zone=UTC)
    assert next_occurrence(parsed, series, at(2027, 6, 1, zone=UTC), UTC, Budget()) is None


def test_paging_keys_stay_in_order_when_an_override_moves_ahead() -> None:
    """A moved occurrence can come out of the expander before ones that start earlier."""
    body = calendar(
        event("f@t", "DTSTART:20270105T090000", "RRULE:FREQ=DAILY;COUNT=4"),
        event("f@t", "DTSTART:20270105T193000Z", "RECURRENCE-ID:20270107T090000"),
    )
    parsed = parse_feed("t", body, now=NOW)
    zone = ZoneInfo("Pacific/Kiritimati")
    every = list(occurrences([parsed], at(2027, 1, 1), at(2027, 1, 12), zone, Budget()))
    assert [item.key for item in every] == sorted(item.key for item in every)
    assert len(every) == 4
    seen = []
    after = None
    while True:
        page = list(
            occurrences([parsed], at(2027, 1, 1), at(2027, 1, 12), zone, Budget(), after=after)
        )[:1]
        if not page:
            break
        seen.append(page[0].rid)
        after = page[0].key
    assert sorted(seen) == sorted(item.rid for item in every)
