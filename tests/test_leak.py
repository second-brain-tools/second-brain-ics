"""A feed's address never leaves the server: not in a log line, an error or an answer.

Every address the tests use carries a marker in its path and its query
(conftest.MARKER). Each case drives one way a fetch can fail (and the ways it
succeeds), with every logger at DEBUG, and then looks for the marker everywhere
the server writes: the log, every tool result and error, /healthz, the ``check``
command's output, the settings' repr, and the sentences that refuse a setting.
"""

import asyncio
import gzip
import logging
import ssl
import time
from contextlib import AbstractContextManager, nullcontext
from typing import Any

import httpx2
import pytest
from build import calendar, event, simple
from conftest import FEED_PATH, MARKER, feed_text, feed_url, settings_with
from fastmcp import Client
from fastmcp.utilities.tests import asgi_server
from pytest_httpserver import HTTPServer
from second_brain_core.remote import HttpOptions, http_app_kwargs
from werkzeug import Request, Response

from second_brain_ics import cli
from second_brain_ics.config import Settings
from second_brain_ics.fetch import Fetcher
from second_brain_ics.server import create_server

TOOLS: list[tuple[str, dict[str, Any]]] = [
    ("calendar_list", {}),
    ("calendar_events", {"start": "2026-12-01", "end": "2027-03-31"}),
    ("calendar_search", {"query": "series"}),
    ("calendar_read", {"id": "main:0000000000000000"}),
]


def respond(httpserver: HTTPServer, case: str) -> str:
    """Make the test server answer the marked path the way ``case`` says; the address."""
    expect = httpserver.expect_request(FEED_PATH)
    if case == "ok":
        expect.respond_with_data(feed_text("spike.ics"))
    elif case == "broken event":
        expect.respond_with_data(
            calendar(
                event("bad@t", f"DTSTART:2027{MARKER}"), event("g@t", "DTSTART:20270101T000000Z")
            )
        )
    elif case in ("404", "500"):
        expect.respond_with_data(b"", status=int(case))
    elif case == "redirect elsewhere":
        expect.respond_with_data(
            b"", status=302, headers={"Location": f"https://localhost:{httpserver.port}/{MARKER}"}
        )
    elif case == "redirect loop":
        expect.respond_with_data(
            b"", status=302, headers={"Location": f"{FEED_PATH}?again={MARKER}"}
        )
    elif case == "not a calendar":
        expect.respond_with_data(f"<html>{MARKER}</html>".encode())
    elif case == "unreadable":
        expect.respond_with_data(f"BEGIN:VCALENDAR\r\n{MARKER}".encode())
    elif case == "too large":
        expect.respond_with_data(
            gzip.compress(b"BEGIN:VCALENDAR\r\n" + b"\0" * (2 * 1024 * 1024)),
            headers={"Content-Encoding": "gzip"},
        )
    elif case == "slow":

        def slow(_request: Request) -> Response:
            time.sleep(0.5)
            return Response(simple())

        expect.respond_with_handler(slow)
    else:  # pragma: no cover - a case the list below doesn't have
        raise AssertionError(case)
    return feed_url(httpserver)


CASES = [
    "ok",
    "broken event",
    "404",
    "500",
    "redirect elsewhere",
    "redirect loop",
    "not a calendar",
    "unreadable",
    "too large",
    "slow",
    "refused",
    "untrusted certificate",
]


def everything_logged(caplog: pytest.LogCaptureFixture) -> str:
    # The test server's own log prints each request line, the address included:
    # that is the test's server, not this one.
    return "\n".join(
        f"{record.name} {record.getMessage()} {record.exc_text or ''}"
        for record in caplog.records
        if not record.name.startswith("werkzeug")
    )


@pytest.mark.parametrize("case", CASES)
async def test_the_address_never_leaves_the_server(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
    httpserver: HTTPServer,
    trusted: ssl.SSLContext,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    if case == "refused":
        url = f"https://127.0.0.1:1/{MARKER}/feed.ics?token={MARKER}"
    else:
        url = respond(httpserver, "ok" if case == "untrusted certificate" else case)
    settings = settings_with(
        monkeypatch, {"main": url}, SBICS_FETCH_TIMEOUT="0.3", SBICS_MAX_FEED_MB="1"
    )
    verify = None if case == "untrusted certificate" else trusted
    server = create_server(settings=settings, verify=verify)

    seen: list[str] = [repr(settings), str(settings.feeds)]
    async with Client(server) as client:
        for tool, arguments in TOOLS:
            result = await client.call_tool(tool, arguments, raise_on_error=False)
            seen.append(str(result.content) + str(result.structured_content))

    kwargs = http_app_kwargs(HttpOptions(host="127.0.0.1", port=8777, no_auth=True))
    path = kwargs.pop("path")
    async with asgi_server(server, path=path, **kwargs) as running:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=running.app), base_url="http://127.0.0.1"
        ) as http:
            seen.append((await http.get("/healthz")).text)

    if verify is not None:  # check uses the system's trust store, as on a real machine
        monkeypatch.setattr(cli, "Fetcher", lambda s: cli_fetcher(s, trusted))
    expect_exit: AbstractContextManager[object] = (
        nullcontext() if case in ("ok", "broken event") else pytest.raises(SystemExit)
    )
    with expect_exit:
        # Off the test's event loop: the command runs its own.
        await asyncio.to_thread(cli.main, ["check"])
    out = capsys.readouterr()
    seen += [out.out, out.err]

    seen.append(everything_logged(caplog))
    leaked = [text for text in seen if MARKER in text]
    assert leaked == []
    assert "token=" not in "\n".join(seen)


def cli_fetcher(settings: Settings, trusted: ssl.SSLContext) -> Fetcher:
    return Fetcher(settings, verify=trusted)


@pytest.mark.parametrize(
    "bad",
    [
        f"http://127.0.0.1/{MARKER}",
        f"https://user:{MARKER}@example.com/feed.ics",
        f"https://example.com/{MARKER}#{MARKER}",
        f"ftp://example.com/{MARKER}",
        f"https://example.com:notaport/{MARKER}",
        f"https://exa mple.com/{MARKER}",
    ],
)
def test_a_refused_address_is_never_quoted(
    bad: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SBICS_FEEDS", "main")
    monkeypatch.setenv("SBICS_FEED_MAIN", bad)
    settings = Settings.from_env()
    assert settings.feeds == () and len(settings.unreadable_values) == 1
    assert MARKER not in " ".join(settings.unreadable_values) + repr(settings)
    with pytest.raises(SystemExit) as exited:
        cli.main(["check"])
    assert exited.value.code == 2
    out = capsys.readouterr()
    assert MARKER not in out.out + out.err
    assert "SBICS_FEED_MAIN" in out.err


async def test_the_control_without_quiet_http_loggers_the_address_does_leak(
    monkeypatch: pytest.MonkeyPatch,
    httpserver: HTTPServer,
    trusted: ssl.SSLContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The check above passes because the loggers are held quiet, not because it can't see."""
    from second_brain_ics import server as server_module

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(server_module, "quiet_http_loggers", lambda: None)
    for name in ("httpx2", "httpcore2"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    url = respond(httpserver, "ok")
    settings = settings_with(monkeypatch, {"main": url})
    async with Client(create_server(settings=settings, verify=trusted)) as client:
        await client.call_tool("calendar_list", {})
    assert MARKER in everything_logged(caplog)
