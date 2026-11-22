"""HTTP mode: the app over real HTTP, /healthz, the Host check, and the audit line."""

import logging
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from build import simple
from conftest import FEED_PATH, feed_url, settings_with
from fastmcp import FastMCP
from fastmcp.utilities.tests import ASGIServer, asgi_server
from pytest_httpserver import HTTPServer
from second_brain_core.remote import HttpOptions, http_app_kwargs, http_options

from second_brain_ics.identity import SERVER
from second_brain_ics.server import create_server


def opts(**kwargs: Any) -> HttpOptions:
    return HttpOptions(**{"host": "127.0.0.1", "port": SERVER.default_port, **kwargs})


def test_this_servers_defaults() -> None:
    assert opts().port == 8777
    assert http_options(SERVER, environ={}) == opts()
    assert http_options(SERVER, environ={"SBICS_PORT": "9001"}).port == 9001
    assert http_options(SERVER, environ={"SBICS_HTTP_PATH": "/x"}).path == "/x"


@asynccontextmanager
async def serving(server: FastMCP, options: HttpOptions) -> AsyncIterator[ASGIServer]:
    kwargs = http_app_kwargs(options)
    path = kwargs.pop("path")
    async with asgi_server(server, path=path, **kwargs) as running:
        yield running


def raw_client(running: ASGIServer, host: str | None = None) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=running.app),
        base_url="http://127.0.0.1",
        headers={"Host": host} if host else None,
    )


@pytest.fixture
def server(
    monkeypatch: pytest.MonkeyPatch, httpserver: HTTPServer, trusted: ssl.SSLContext
) -> FastMCP:
    httpserver.expect_request(FEED_PATH).respond_with_data(simple())
    settings = settings_with(monkeypatch, {"main": feed_url(httpserver)})
    return create_server(settings=settings, verify=trusted)


async def test_round_trip_over_http_with_no_session(server: FastMCP) -> None:
    async with serving(server, opts(no_auth=True)) as running:
        for _ in range(2):
            async with running.client() as client:
                result = await client.call_tool("calendar_list", {})
                assert result.is_error is False


async def test_healthz_up_and_cached(server: FastMCP, httpserver: HTTPServer) -> None:
    async with serving(server, opts(no_auth=True)) as running, raw_client(running) as http:
        for _ in range(5):
            response = await http.get("/healthz")
            assert response.status_code == 200
            assert response.json() == {"server": True, "calendar": True}
    assert len(httpserver.log) == 1


async def test_healthz_down_when_a_feed_cant_be_read(
    monkeypatch: pytest.MonkeyPatch, trusted: ssl.SSLContext
) -> None:
    settings = settings_with(monkeypatch, {"main": "https://127.0.0.1:1/x.ics"})
    server = create_server(settings=settings, verify=trusted)
    async with serving(server, opts(no_auth=True)) as running, raw_client(running) as http:
        response = await http.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"server": True, "calendar": False}


async def test_healthz_down_with_settings_that_cant_be_used(trusted: ssl.SSLContext) -> None:
    from second_brain_ics.config import Settings

    server = create_server(settings=Settings.from_env(), verify=trusted)  # no feeds at all
    async with serving(server, opts(no_auth=True)) as running, raw_client(running) as http:
        assert (await http.get("/healthz")).status_code == 503


async def test_an_unknown_host_header_is_refused(server: FastMCP) -> None:
    options = opts(public_url="https://ics.example.com", no_auth=True)
    async with serving(server, options) as running:
        async with raw_client(running, host="evil.example.com") as http:
            response = await http.post("/mcp", json={})
    assert response.status_code == 421


async def test_the_audit_line_holds_no_arguments(
    server: FastMCP, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="second_brain_ics.audit")
    async with serving(server, opts(no_auth=True)) as running, running.client() as client:
        await client.call_tool("calendar_search", {"query": "a-very-private-word"})
    lines = [
        record.getMessage() for record in caplog.records if record.name == "second_brain_ics.audit"
    ]
    assert lines and all("tool=calendar_search" in line for line in lines)
    assert not any("a-very-private-word" in line for line in lines)
