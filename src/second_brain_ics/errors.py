"""Typed errors: each carries a sentence that is safe to show a remote caller.

No sentence here ever holds a feed's address. The address is a secret (anyone
with it can read the calendar), and the text of an HTTP library's exception
quotes it, so a fetch failure is reported by its kind, from the fixed sentences
below, never by the exception's own text.
"""

from second_brain_core.errors import ServeConfigError

__all__ = [
    "CalendarError",
    "FeedError",
    "NotFoundError",
    "RequestError",
    "ServeConfigError",
]


class CalendarError(Exception):
    """Base class for the failures a tool reports with its own sentence."""


# Why a feed could not be read. Each finishes the sentence "The calendar feed
# '<name>' ...".
UNREACHABLE = "could not be reached (the network, or its server, is down)"
TIMED_OUT = "did not answer in time"
TLS = "did not present a certificate this machine trusts"
REDIRECT_ELSEWHERE = "redirected to another address, which is refused"
TOO_MANY_REDIRECTS = "redirected too many times"
TOO_LARGE = "is larger than the server's limit (SBICS_MAX_FEED_MB)"
NOT_A_CALENDAR = "answered with something that is not a calendar"
UNREADABLE = "could not be read as a calendar"
NOT_FOUND_STATUS = "answered 404: the share link may have been deleted"


def status_reason(status: int) -> str:
    """The reason for an answer other than 200."""
    if status == 404:
        return NOT_FOUND_STATUS
    return f"answered with HTTP status {status}"


class FeedError(CalendarError):
    """A named feed could not be fetched or read, and there is no copy to serve."""

    def __init__(self, feed: str, reason: str) -> None:
        self.feed = feed
        self.reason = reason
        super().__init__(f"The calendar feed {feed!r} {reason}.")


class NotFoundError(CalendarError):
    """An id names no event the feed holds now."""


class RequestError(CalendarError):
    """A tool's arguments can't be used: an unknown calendar, a bad zone or date."""
