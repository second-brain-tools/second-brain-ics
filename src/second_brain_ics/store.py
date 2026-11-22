"""Each feed's last good copy, in memory only, and when to fetch it again.

A feed is fetched when a call needs it and its last attempt is older than the
minimum interval (``SBICS_MIN_FETCH_SECONDS``). Attempts count, not successes, so
a feed whose server is down is still asked at most once per interval. Calls that
arrive together share one fetch: the first takes the feed's lock and fetches, the
others wait for it and read what it got.

When a fetch fails, the last good copy is served, marked stale, with the reason.
With no copy yet, the call fails with the reason. Nothing is written to disk: a
restart starts from nothing, and the first call fetches.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .config import Settings
from .errors import UNREADABLE, FeedError
from .feed import ParsedFeed, parse_feed
from .fetch import Fetcher

logger = logging.getLogger("second_brain_ics.store")


@dataclass(frozen=True)
class Copy:
    """A parsed copy of a feed, and when and how it was fetched."""

    feed: ParsedFeed
    fetched_at: datetime
    seconds: float


@dataclass(frozen=True)
class View:
    """What a call gets for one feed: its copy (if any) and whether it is current."""

    name: str
    copy: Copy | None
    # The reason the latest attempt failed; the copy, if any, is then stale.
    error: str | None

    @property
    def stale(self) -> bool:
        return self.copy is not None and self.error is not None


@dataclass
class _State:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    copy: Copy | None = None
    attempted: float | None = None
    error: str | None = None


class Store:
    """The feeds named in the settings, each fetched when due."""

    def __init__(
        self,
        settings: Settings,
        fetcher: Fetcher,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.fetcher = fetcher
        self.clock = clock
        self._states = {feed.name: _State() for feed in settings.feeds}

    @property
    def names(self) -> tuple[str, ...]:
        return self.settings.names

    def _due(self, state: _State) -> bool:
        return (
            state.attempted is None
            or self.clock() - state.attempted >= self.settings.min_fetch_seconds
        )

    async def view(self, name: str) -> View:
        """Feed ``name`` as it is now, fetching it first if that is due."""
        state = self._states[name]
        async with state.lock:
            if self._due(state):
                await self._refresh(name, state)
            return View(name=name, copy=state.copy, error=state.error)

    async def _refresh(self, name: str, state: _State) -> None:
        feed = self.settings.feed(name)
        assert feed is not None  # the states are made from the settings' feeds
        state.attempted = self.clock()
        try:
            fetched = await self.fetcher.fetch(feed)
            parsed = await asyncio.to_thread(parse_feed, name, fetched.body)
        except FeedError as exc:
            # The sentence names the feed and the kind of failure, never the address.
            state.error = str(exc)
            logger.warning("%s", exc)
            return
        except Exception as exc:  # noqa: BLE001 - a bug reading a feed: report it, keep the copy
            # Its text may quote the feed; the type is enough to find it.
            state.error = str(FeedError(name, UNREADABLE))
            logger.warning("%s (%s while reading it)", state.error, type(exc).__name__)
            return
        state.copy = Copy(feed=parsed, fetched_at=datetime.now(UTC), seconds=fetched.seconds)
        state.error = None

    async def healthy(self) -> bool:
        """True when every feed's latest attempt succeeded (fetching those that are due)."""
        views = [await self.view(name) for name in self.names]
        return bool(views) and all(view.error is None and view.copy is not None for view in views)
