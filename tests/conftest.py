"""Fixtures: a real HTTPS server with a test certificate authority, and feeds to serve."""

import os
import ssl
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import trustme
from pytest_httpserver import HTTPServer

from second_brain_ics.config import Settings

FEEDS = Path(__file__).parent / "feeds"
# Planted in every feed address the tests use: it must never show up in a log line,
# an error, an answer or a command's output (test_leak.py).
MARKER = "SECRETMARKER7f3a"
FEED_PATH = f"/calendar/{MARKER}/basic.ics"


def feed_text(name: str) -> bytes:
    return (FEEDS / name).read_bytes()


@pytest.fixture(scope="session")
def ca() -> trustme.CA:
    return trustme.CA()


@pytest.fixture(scope="session")
def httpserver_ssl_context(ca: trustme.CA) -> ssl.SSLContext:
    """pytest-httpserver serves https with this context (its documented hook)."""
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    # The address, not a name: on Windows "localhost" resolves to ::1 first.
    ca.issue_cert("127.0.0.1").configure_cert(context)
    return context


@pytest.fixture(scope="session")
def httpserver_listen_address() -> tuple[str, int]:
    return ("127.0.0.1", 0)


@pytest.fixture(scope="session")
def make_httpserver(httpserver_ssl_context: ssl.SSLContext) -> Iterator[HTTPServer]:
    """pytest-httpserver's server, but threaded.

    A client that gives up mid-request (the timeout tests) can leave its connection
    open; a server that serves one connection at a time then blocks the next test's
    handshake until it too times out.
    """
    server = HTTPServer(host="127.0.0.1", port=0, ssl_context=httpserver_ssl_context, threaded=True)
    server.start()
    yield server
    server.clear()
    if server.is_running():
        server.stop()


@pytest.fixture(scope="session")
def trusted(ca: trustme.CA) -> ssl.SSLContext:
    """A client context that trusts the test certificate authority."""
    context = ssl.create_default_context()
    ca.configure_trust(context)
    return context


def feed_url(server: HTTPServer, path: str = FEED_PATH) -> str:
    return f"{server.url_for(path)}?token={MARKER}"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No SBICS_* setting leaks into a test from the shell or another test."""
    for name in list(os.environ):
        if name.startswith("SBICS_"):
            monkeypatch.delenv(name)
    yield


def settings_with(monkeypatch: pytest.MonkeyPatch, feeds: dict[str, str], **extra: str) -> Settings:
    """Settings read through the environment, as the server reads them."""
    monkeypatch.setenv("SBICS_FEEDS", ",".join(feeds))
    for name, url in feeds.items():
        monkeypatch.setenv(f"SBICS_FEED_{name.upper()}", url)
    for name, value in extra.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.unreadable_values == (), settings.unreadable_values
    return settings


# serve_feed(body, path=FEED_PATH, **headers) -> the feed's address
ServeFeed = Callable[..., str]


@pytest.fixture
def serve_feed(httpserver: HTTPServer) -> ServeFeed:
    """Serve ``body`` at the marked path; returns the feed's address."""

    def serve(body: bytes, path: str = FEED_PATH, **headers: str) -> str:
        httpserver.expect_request(path).respond_with_data(
            body, content_type="text/calendar; charset=utf-8", headers=headers or None
        )
        return feed_url(httpserver, path)

    return serve
