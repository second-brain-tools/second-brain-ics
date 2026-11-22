"""Fetching a feed: the only module that touches the network or a feed's address.

The address is a secret, and an HTTP library's exceptions quote it, so:

- every failure becomes a :class:`~.errors.FeedError` with a fixed sentence,
  chosen by the failure's kind;
- that error is raised *after* the ``except`` block, so it carries no
  ``__context__`` holding the library's exception (whose request holds the URL);
- nothing here logs (the HTTP library's own loggers are held at WARNING by
  ``server.quiet_http_loggers``, since they log every request's URL at INFO).

What is fetched is limited: https only, redirects followed by hand (at most
three, https on the same host only), one deadline for the whole fetch (connect,
redirects and body, since the library's timeouts are per read and a body sent a
byte at a time would never trip them), a size cap on the decoded body (so a
compressed bomb stops too), status 200 only, and a body that must be a calendar.
"""

import asyncio
import ssl
import time
from dataclasses import dataclass

import httpx2

from . import __version__
from .config import Feed, Settings
from .errors import (
    NOT_A_CALENDAR,
    REDIRECT_ELSEWHERE,
    TIMED_OUT,
    TLS,
    TOO_LARGE,
    TOO_MANY_REDIRECTS,
    UNREACHABLE,
    UNREADABLE,
    FeedError,
    status_reason,
)

MAX_REDIRECTS = 3
REDIRECTS = frozenset({301, 302, 303, 307, 308})
USER_AGENT = f"second-brain-ics/{__version__}"
_BOM = b"\xef\xbb\xbf"
_START = b"BEGIN:VCALENDAR"


@dataclass(frozen=True)
class Fetched:
    """A feed's body as it arrived, and how long fetching it took."""

    body: bytes
    seconds: float


def looks_like_a_calendar(body: bytes) -> bool:
    """True when ``body`` starts as an iCalendar file does (after a BOM and blank space)."""
    head = body[:64].removeprefix(_BOM).lstrip()
    return head[: len(_START)].upper() == _START


def _chain(exc: BaseException) -> list[BaseException]:
    """``exc`` and what it was raised from, outermost first (a TLS error is wrapped twice)."""
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen and len(seen) < 10:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _reason_of(exc: BaseException) -> str:
    """The fixed sentence for an exception raised while fetching. Never its text."""
    if isinstance(exc, httpx2.TimeoutException):
        return TIMED_OUT
    if any(isinstance(link, ssl.SSLError) for link in _chain(exc)):
        return TLS
    if isinstance(exc, httpx2.DecodingError):
        return UNREADABLE
    return UNREACHABLE


class Fetcher:
    """Fetches feeds with the limits in :class:`~.config.Settings`.

    ``verify`` is the TLS context to trust (the system's by default); tests pass
    one that trusts their own certificate authority. A client is made per fetch:
    fetches are rare (one per feed per minimum interval), and a client held
    across them would tie its connection pool to whichever event loop used it
    first.
    """

    def __init__(self, settings: Settings, *, verify: ssl.SSLContext | None = None) -> None:
        self.timeout = settings.fetch_timeout
        self.max_bytes = settings.max_feed_bytes
        self.verify = verify if verify is not None else ssl.create_default_context()

    async def fetch(self, feed: Feed) -> Fetched:
        """Feed ``feed``'s body, or a :class:`~.errors.FeedError` saying why not."""
        started = time.monotonic()
        reason: str | None = None
        try:
            async with asyncio.timeout(self.timeout):
                body = await self._fetch(feed)
        except FeedError as exc:
            reason = exc.reason
        except TimeoutError:
            reason = TIMED_OUT
        except Exception as exc:  # noqa: BLE001 - every failure is reported by its kind only
            reason = _reason_of(exc)
        if reason is not None:
            # Raised here, outside the except blocks, so it chains nothing.
            raise FeedError(feed.name, reason)
        return Fetched(body=body, seconds=time.monotonic() - started)

    async def _fetch(self, feed: Feed) -> bytes:
        url = httpx2.URL(feed.url)
        origin = (url.host, url.port)
        async with httpx2.AsyncClient(
            verify=self.verify,
            follow_redirects=False,
            # No proxy, no SSL_CERT_FILE, nothing else from the environment.
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept": "text/calendar, */*;q=0.1"},
            timeout=httpx2.Timeout(self.timeout),
        ) as client:
            for hop in range(MAX_REDIRECTS + 1):
                async with client.stream("GET", url) as response:
                    if response.status_code in REDIRECTS:
                        location = response.headers.get("location", "")
                        if not location:
                            raise FeedError(feed.name, status_reason(response.status_code))
                        if hop == MAX_REDIRECTS:
                            raise FeedError(feed.name, TOO_MANY_REDIRECTS)
                        target = url.join(location)
                        if target.scheme != "https" or (target.host, target.port) != origin:
                            raise FeedError(feed.name, REDIRECT_ELSEWHERE)
                        url = target
                        continue
                    if response.status_code != 200:
                        raise FeedError(feed.name, status_reason(response.status_code))
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > self.max_bytes:
                        raise FeedError(feed.name, TOO_LARGE)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > self.max_bytes:
                            raise FeedError(feed.name, TOO_LARGE)
                    if not looks_like_a_calendar(bytes(body)):
                        raise FeedError(feed.name, NOT_A_CALENDAR)
                    return bytes(body)
        raise FeedError(feed.name, TOO_MANY_REDIRECTS)  # pragma: no cover - the loop returns
