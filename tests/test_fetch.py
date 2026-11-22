"""Fetching over real HTTPS: limits, redirects, failures, and the copy in memory."""

import asyncio
import gzip
import ssl
import time
from collections.abc import Iterator

import pytest
from build import simple
from conftest import FEED_PATH, MARKER, ServeFeed, feed_url, settings_with
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from second_brain_ics import errors
from second_brain_ics.config import Feed
from second_brain_ics.errors import FeedError
from second_brain_ics.fetch import Fetcher, looks_like_a_calendar
from second_brain_ics.store import Store

BODY = simple()


def fetcher(
    monkeypatch: pytest.MonkeyPatch, url: str, trusted: ssl.SSLContext | None, **extra: str
) -> tuple[Fetcher, Feed]:
    settings = settings_with(monkeypatch, {"main": url}, **extra)
    return Fetcher(settings, verify=trusted), settings.feeds[0]


async def reason_for(fetch: Fetcher, feed: Feed) -> str:
    with pytest.raises(FeedError) as caught:
        await fetch.fetch(feed)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    return caught.value.reason


async def test_a_feed_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
    serve_feed: ServeFeed,
    trusted: ssl.SSLContext,
) -> None:
    fetch, feed = fetcher(monkeypatch, serve_feed(BODY), trusted)
    got = await fetch.fetch(feed)
    assert got.body == BODY and got.seconds >= 0


async def test_webcal_is_read_as_https(
    monkeypatch: pytest.MonkeyPatch,
    serve_feed: ServeFeed,
    trusted: ssl.SSLContext,
) -> None:
    url = serve_feed(BODY).replace("https://", "webcal://")
    fetch, feed = fetcher(monkeypatch, url, trusted)
    assert (await fetch.fetch(feed)).body == BODY


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (404, errors.NOT_FOUND_STATUS),
        (500, "answered with HTTP status 500"),
        (204, "answered with HTTP status 204"),
    ],
)
async def test_an_answer_other_than_200_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    httpserver: HTTPServer,
    trusted: ssl.SSLContext,
    status: int,
    reason: str,
) -> None:
    httpserver.expect_request(FEED_PATH).respond_with_data(b"", status=status)
    fetch, feed = fetcher(monkeypatch, feed_url(httpserver), trusted)
    assert await reason_for(fetch, feed) == reason


async def test_an_html_page_is_not_a_calendar(
    monkeypatch: pytest.MonkeyPatch,
    serve_feed: ServeFeed,
    trusted: ssl.SSLContext,
) -> None:
    fetch, feed = fetcher(
        monkeypatch, serve_feed(b"<!doctype html><title>Sign in</title>"), trusted
    )
    assert await reason_for(fetch, feed) == errors.NOT_A_CALENDAR


@pytest.mark.parametrize(
    "body",
    [b"BEGIN:VCALENDAR\r\n", b"\xef\xbb\xbfBEGIN:VCALENDAR", b"\r\n  begin:vcalendar\r\n"],
)
def test_what_counts_as_a_calendar(body: bytes) -> None:
    assert looks_like_a_calendar(body)


async def test_a_feed_over_the_size_limit_is_refused_by_its_length(
    monkeypatch: pytest.MonkeyPatch,
    serve_feed: ServeFeed,
    trusted: ssl.SSLContext,
) -> None:
    big = b"BEGIN:VCALENDAR\r\n" + b"X" * (1024 * 1024 + 1)
    fetch, feed = fetcher(monkeypatch, serve_feed(big), trusted, SBICS_MAX_FEED_MB="1")
    assert await reason_for(fetch, feed) == errors.TOO_LARGE


async def test_a_feed_over_the_size_limit_is_refused_without_a_length(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    def chunks() -> Iterator[bytes]:
        yield b"BEGIN:VCALENDAR\r\n"
        for _ in range(20):
            yield b"X" * 65536

    httpserver.expect_request(FEED_PATH).respond_with_response(
        Response(chunks(), content_type="text/calendar")
    )
    fetch, feed = fetcher(monkeypatch, feed_url(httpserver), trusted, SBICS_MAX_FEED_MB="1")
    assert await reason_for(fetch, feed) == errors.TOO_LARGE


async def test_a_compressed_bomb_stops_at_the_limit(
    monkeypatch: pytest.MonkeyPatch,
    serve_feed: ServeFeed,
    trusted: ssl.SSLContext,
) -> None:
    bomb = gzip.compress(b"BEGIN:VCALENDAR\r\n" + b"\0" * (64 * 1024 * 1024))
    assert len(bomb) < 1024 * 1024
    url = serve_feed(bomb, **{"Content-Encoding": "gzip"})
    fetch, feed = fetcher(monkeypatch, url, trusted, SBICS_MAX_FEED_MB="1")
    assert await reason_for(fetch, feed) == errors.TOO_LARGE


async def test_a_redirect_on_the_same_host_is_followed(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    httpserver.expect_request(FEED_PATH).respond_with_data(
        b"", status=302, headers={"Location": "/moved.ics?x=1"}
    )
    httpserver.expect_request("/moved.ics").respond_with_data(BODY)
    fetch, feed = fetcher(monkeypatch, feed_url(httpserver), trusted)
    assert (await fetch.fetch(feed)).body == BODY


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        ("https://localhost:{port}/other.ics", errors.REDIRECT_ELSEWHERE),
        ("http://127.0.0.1:{port}/other.ics", errors.REDIRECT_ELSEWHERE),
        ("https://127.0.0.1:1/other.ics", errors.REDIRECT_ELSEWHERE),
        ("", "answered with HTTP status 302"),
    ],
)
async def test_a_redirect_elsewhere_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    httpserver: HTTPServer,
    trusted: ssl.SSLContext,
    location: str,
    reason: str,
) -> None:
    headers = {"Location": location.format(port=httpserver.port)} if location else {}
    httpserver.expect_request(FEED_PATH).respond_with_data(b"", status=302, headers=headers)
    fetch, feed = fetcher(monkeypatch, feed_url(httpserver), trusted)
    assert await reason_for(fetch, feed) == reason


async def test_four_redirects_in_a_row_are_refused(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    httpserver.expect_request(FEED_PATH).respond_with_data(
        b"", status=301, headers={"Location": "/r1"}
    )
    for hop in range(1, 5):
        httpserver.expect_request(f"/r{hop}").respond_with_data(
            b"", status=307, headers={"Location": f"/r{hop + 1}"}
        )
    fetch, feed = fetcher(monkeypatch, feed_url(httpserver), trusted)
    assert await reason_for(fetch, feed) == errors.TOO_MANY_REDIRECTS


async def test_a_slow_answer_times_out(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    def slow(_request: Request) -> Response:
        time.sleep(0.8)
        return Response(BODY)

    httpserver.expect_request(FEED_PATH).respond_with_handler(slow)
    fetch, feed = fetcher(monkeypatch, feed_url(httpserver), trusted, SBICS_FETCH_TIMEOUT="0.3")
    assert await reason_for(fetch, feed) == errors.TIMED_OUT


async def test_a_body_dripped_a_byte_at_a_time_times_out(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    """Each read is quick, so only a deadline over the whole fetch stops it."""

    def drip() -> Iterator[bytes]:
        # 1.5 s in all: past the 1 s deadline, and short enough that the test
        # server (one request at a time) is free again for the next test.
        yield b"BEGIN:VCALENDAR\r\n"
        for _ in range(30):
            time.sleep(0.05)
            yield b"X"

    httpserver.expect_request(FEED_PATH).respond_with_response(Response(drip()))
    fetch, feed = fetcher(monkeypatch, feed_url(httpserver), trusted, SBICS_FETCH_TIMEOUT="1")
    started = time.monotonic()
    assert await reason_for(fetch, feed) == errors.TIMED_OUT
    assert time.monotonic() - started < 3


async def test_a_refused_connection_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, trusted: ssl.SSLContext
) -> None:
    fetch, feed = fetcher(monkeypatch, "https://127.0.0.1:1/feed.ics", trusted)
    assert await reason_for(fetch, feed) == errors.UNREACHABLE


async def test_a_certificate_nobody_vouches_for_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    serve_feed: ServeFeed,
) -> None:
    fetch, feed = fetcher(monkeypatch, serve_feed(BODY), None)  # the system's trust store
    assert await reason_for(fetch, feed) == errors.TLS


# -- the copy in memory -----------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def requests_to(httpserver: HTTPServer, path: str) -> int:
    """Requests this test made: the server is shared, and another test's late one may land."""
    return sum(1 for request, _ in httpserver.log if request.path == path)


async def test_the_minimum_interval_holds_between_fetches(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    path = f"/interval/{MARKER}.ics"
    httpserver.expect_request(path).respond_with_data(BODY)
    settings = settings_with(monkeypatch, {"main": feed_url(httpserver, path)})
    clock = Clock()
    store = Store(settings, Fetcher(settings, verify=trusted), clock=clock)
    for _ in range(3):
        await store.view("main")
    assert requests_to(httpserver, path) == 1
    clock.now += 59
    await store.view("main")
    assert requests_to(httpserver, path) == 1
    clock.now += 1
    await store.view("main")
    assert requests_to(httpserver, path) == 2


async def test_calls_that_arrive_together_share_one_fetch(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    def slowish(_request: Request) -> Response:
        time.sleep(0.3)
        return Response(BODY)

    path = f"/together/{MARKER}.ics"
    httpserver.expect_request(path).respond_with_handler(slowish)
    settings = settings_with(monkeypatch, {"main": feed_url(httpserver, path)})
    store = Store(settings, Fetcher(settings, verify=trusted))
    views = await asyncio.gather(*(store.view("main") for _ in range(10)))
    assert requests_to(httpserver, path) == 1
    assert all(view.copy is views[0].copy and view.copy is not None for view in views)


async def test_a_failed_fetch_serves_the_last_good_copy_as_stale(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    answers = [Response(BODY), Response(b"", status=503)]
    httpserver.expect_request(FEED_PATH).respond_with_handler(lambda _r: answers.pop(0))
    settings = settings_with(monkeypatch, {"main": feed_url(httpserver)})
    clock = Clock()
    store = Store(settings, Fetcher(settings, verify=trusted), clock=clock)
    first = await store.view("main")
    assert first.copy is not None and not first.stale and await store.healthy()
    clock.now += 60
    second = await store.view("main")
    assert second.copy is first.copy and second.stale
    assert second.error == "The calendar feed 'main' answered with HTTP status 503."
    assert not await store.healthy()


async def test_with_no_copy_a_failure_leaves_nothing_to_serve(
    monkeypatch: pytest.MonkeyPatch, trusted: ssl.SSLContext
) -> None:
    settings = settings_with(monkeypatch, {"main": "https://127.0.0.1:1/feed.ics"})
    store = Store(settings, Fetcher(settings, verify=trusted))
    view = await store.view("main")
    assert view.copy is None and not view.stale and view.error is not None
    assert not await store.healthy()


async def test_a_feed_that_wont_parse_keeps_the_last_good_copy(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    answers = [Response(BODY), Response(b"BEGIN:VCALENDAR\r\ngarbage")]
    httpserver.expect_request(FEED_PATH).respond_with_handler(lambda _r: answers.pop(0))
    settings = settings_with(monkeypatch, {"main": feed_url(httpserver)})
    clock = Clock()
    store = Store(settings, Fetcher(settings, verify=trusted), clock=clock)
    first = await store.view("main")
    clock.now += 60
    second = await store.view("main")
    assert second.copy is first.copy and second.error is not None
    assert "could not be read as a calendar" in second.error


async def test_a_bug_while_reading_a_feed_is_an_error_and_keeps_the_copy(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> None:
    from second_brain_ics import store as store_module

    httpserver.expect_request(FEED_PATH).respond_with_data(BODY)
    settings = settings_with(monkeypatch, {"main": feed_url(httpserver)})
    clock = Clock()
    store = Store(settings, Fetcher(settings, verify=trusted), clock=clock)
    first = await store.view("main")
    assert first.copy is not None

    def broken(*_args: object) -> None:
        raise AssertionError("text that may quote the feed")

    monkeypatch.setattr(store_module, "parse_feed", broken)
    clock.now += 60
    second = await store.view("main")
    assert second.copy is first.copy and second.stale
    assert second.error == "The calendar feed 'main' could not be read as a calendar."
