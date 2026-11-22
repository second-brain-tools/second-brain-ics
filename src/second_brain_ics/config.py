"""Runtime configuration: which feeds to read, in which zone, and the fetch limits.

Every setting is a ``SBICS_*`` variable. Reading never raises: a value that can't
be used keeps its safe default and is recorded as a sentence in
``unreadable_values``; the commands refuse to run until it is fixed. /healthz
reads the settings too, so reading must not fail.

A feed's address is a secret: anyone who has it can read that calendar. It is
never part of a sentence here, never in a repr, and never in a log line. A
problem with one is named by the feed and the variable, not by its value.
"""

import os
import re
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field
from second_brain_core.env import env_int, env_list, env_seconds

FEEDS_VAR = "SBICS_FEEDS"
TIMEZONE_VAR = "SBICS_TIMEZONE"
MIN_FETCH_VAR = "SBICS_MIN_FETCH_SECONDS"
TIMEOUT_VAR = "SBICS_FETCH_TIMEOUT"
MAX_FEED_VAR = "SBICS_MAX_FEED_MB"

DEFAULT_TIMEZONE = "UTC"
DEFAULT_MIN_FETCH_SECONDS = 60
MIN_FETCH_RANGE = (10, 86400)
DEFAULT_FETCH_TIMEOUT = 30.0
DEFAULT_MAX_FEED_MB = 10
MAX_FEED_RANGE = (1, 50)

# A feed's name: what a client passes as `calendars`, and the end of its address
# variable. No hyphen, so every variable can be exported from a shell.
FEED_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
# What a mistaken name may be quoted as: short, and nothing an address is made of.
SHOWABLE = re.compile(r"[A-Za-z0-9_-]{1,40}")


def feed_var(name: str) -> str:
    """The variable that holds feed ``name``'s address."""
    return f"SBICS_FEED_{name.upper()}"


class Feed(BaseModel):
    """One named feed. The address never appears in a repr or a sentence."""

    name: str
    url: str = Field(repr=False)


def zone_or_none(name: str) -> ZoneInfo | None:
    """``name`` as an IANA zone, or None when it isn't one."""
    if not name or name != name.strip():
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def normalise_url(raw: str) -> str | None:
    """``raw`` as the https address to fetch, or None when it may not be used.

    ``https://``, or ``webcal://`` (the same address, read over https). Never
    plain http, never a user name or password in the address (that would send a
    password to whatever the address points at), never a fragment.
    """
    value = raw.strip()
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises for a port that isn't a number
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("https", "webcal"):
        return None
    if not parts.hostname or parts.username is not None or parts.password is not None:
        return None
    if parts.fragment or "#" in value:
        return None
    if any(char.isspace() for char in value):
        return None
    return urlunsplit(("https", parts.netloc, parts.path or "/", parts.query, ""))


class Settings(BaseModel):
    """The server's settings, read from ``SBICS_*`` variables."""

    feeds: tuple[Feed, ...] = ()
    timezone: str = DEFAULT_TIMEZONE
    min_fetch_seconds: float = float(DEFAULT_MIN_FETCH_SECONDS)
    fetch_timeout: float = DEFAULT_FETCH_TIMEOUT
    max_feed_bytes: int = DEFAULT_MAX_FEED_MB * 1024 * 1024
    # Values that can't be used, each as the sentence that says what it should be.
    # The setting keeps its default (a feed that can't be used is left out), and
    # the commands refuse to run until it is fixed.
    unreadable_values: tuple[str, ...] = ()

    def feed(self, name: str) -> Feed | None:
        return next((feed for feed in self.feeds if feed.name == name), None)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(feed.name for feed in self.feeds)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from the ``SBICS_*`` environment variables. Never raises."""
        problems: list[str] = []
        feeds: list[Feed] = []
        names = env_list(FEEDS_VAR)
        if not names:
            problems.append(f"{FEEDS_VAR} must name at least one feed, separated by commas")
        seen: set[str] = set()
        for name in names:
            if not FEED_NAME.match(name):
                # Quoted only when it can't be a secret: an address put here by mistake
                # would otherwise be printed back, into a terminal or a service's log.
                shown = repr(name) if SHOWABLE.fullmatch(name) else "an entry (not shown)"
                problems.append(
                    f"{FEEDS_VAR}: {shown} is not a feed name: lower-case letters, digits and "
                    "underscores, starting with a letter, at most 32 characters"
                )
                continue
            if name in seen:
                problems.append(f"{FEEDS_VAR} names {name!r} twice")
                continue
            seen.add(name)
            raw = os.environ.get(feed_var(name), "")
            if not raw.strip():
                problems.append(f"{feed_var(name)} must hold the address of feed {name!r}")
                continue
            url = normalise_url(raw)
            if url is None:
                problems.append(
                    f"{feed_var(name)} must be an https:// or webcal:// address, without a user "
                    "name, password or fragment (its value is not shown: it is a secret)"
                )
                continue
            feeds.append(Feed(name=name, url=url))

        zone = os.environ.get(TIMEZONE_VAR, "").strip() or DEFAULT_TIMEZONE
        if zone_or_none(zone) is None:
            problems.append(
                f"{TIMEZONE_VAR} must be an IANA time zone such as America/Chicago, got {zone!r}"
            )
            zone = DEFAULT_TIMEZONE

        low, high = MIN_FETCH_RANGE
        interval = env_int(MIN_FETCH_VAR, DEFAULT_MIN_FETCH_SECONDS, minimum=low, maximum=high)
        if interval is None:
            got = os.environ.get(MIN_FETCH_VAR, "")
            problems.append(
                f"{MIN_FETCH_VAR} must be a whole number of seconds from {low} to {high}, "
                f"got {got!r}"
            )
            interval = DEFAULT_MIN_FETCH_SECONDS

        timeout = env_seconds(TIMEOUT_VAR, DEFAULT_FETCH_TIMEOUT)
        if timeout is None:
            got = os.environ.get(TIMEOUT_VAR, "")
            problems.append(f"{TIMEOUT_VAR} must be a number of seconds above 0, got {got!r}")
            timeout = DEFAULT_FETCH_TIMEOUT

        low, high = MAX_FEED_RANGE
        megabytes = env_int(MAX_FEED_VAR, DEFAULT_MAX_FEED_MB, minimum=low, maximum=high)
        if megabytes is None:
            got = os.environ.get(MAX_FEED_VAR, "")
            problems.append(
                f"{MAX_FEED_VAR} must be a whole number of megabytes from {low} to {high}, "
                f"got {got!r}"
            )
            megabytes = DEFAULT_MAX_FEED_MB

        return cls(
            feeds=tuple(feeds),
            timezone=zone,
            min_fetch_seconds=float(interval),
            fetch_timeout=timeout,
            max_feed_bytes=megabytes * 1024 * 1024,
            unreadable_values=tuple(problems),
        )
