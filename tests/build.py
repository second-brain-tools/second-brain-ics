"""Building small feeds for one case each, written out as iCalendar text.

Invisible and bidirectional characters are written here as escapes, never as the
characters themselves (second-brain-core's check_no_invisible_characters).
"""

ZWSP = "\u200b"  # zero-width space
RLO = "\u202e"  # right-to-left override


def event(uid: str, start: str, *lines: str, end: str | None = None, title: str = "") -> str:
    """One VEVENT. ``start``/``end`` are whole property lines, e.g. ``DTSTART:20270101T090000Z``."""
    parts = ["BEGIN:VEVENT", f"UID:{uid}", "DTSTAMP:20261121T000000Z", start]
    if end:
        parts.append(end)
    parts.append(f"SUMMARY:{title or uid}")
    parts.extend(lines)
    parts.append("END:VEVENT")
    return "\r\n".join(parts)


def calendar(*events: str, zone: str | None = None, name: str = "Built", head: str = "") -> bytes:
    parts = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//second-brain-ics tests//built//EN"]
    parts.append(f"X-WR-CALNAME:{name}")
    if zone:
        parts.append(f"X-WR-TIMEZONE:{zone}")
    if head:
        parts.append(head)
    parts.extend(events)
    parts.append("END:VCALENDAR")
    return ("\r\n".join(parts) + "\r\n").encode()


def simple(uid: str = "one@test", when: str = "20270105T150000Z", title: str = "") -> bytes:
    """A calendar with one event at ``when`` (UTC), an hour long."""
    hour = int(when[9:11]) + 1
    end = f"{when[:9]}{hour:02d}{when[11:]}"
    return calendar(event(uid, f"DTSTART:{when}", end=f"DTEND:{end}", title=title))
