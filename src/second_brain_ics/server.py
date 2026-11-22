"""MCP server construction: the store, the tools and ``/healthz``."""

import logging
import ssl

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from second_brain_core.audit import QUIET_LOGGERS, RegisteredClients
from second_brain_core.health import make_healthz
from second_brain_core.remote import build_middleware

from . import __version__
from .config import Settings
from .fetch import Fetcher
from .identity import NAMED_CAUSES, SERVER
from .models import UNTRUSTED_NOTICE
from .store import Store
from .tools import register_tools

# What a client is told about this server before it calls anything.
INSTRUCTIONS = (
    "Read-only access to one person's calendars, read from their calendar feeds: list the "
    "calendars, list the events in a date range (repeating events expanded), search them, "
    "and read one in full. Nothing here can create, change or delete an event, or answer "
    "an invitation. Times are given in the time zone each answer names; an all-day "
    f"event's end is the last day it covers. {UNTRUSTED_NOTICE}"
)
# How long one answer of the health check is reused. The route is not behind
# sign-in; with the store's minimum interval, an outsider polling it causes at
# most one fetch per feed per interval anyway.
HEALTH_CACHE_SECONDS = 30.0


def quiet_http_loggers() -> None:
    """Hold the HTTP libraries' loggers at WARNING, in every transport.

    They log each request's URL at INFO, and a feed's URL is a secret. The shared
    logging set-up does this for HTTP mode; stdio needs it as much.
    """
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def create_server(
    *,
    auth: AuthProvider | None = None,
    allowed_ids: frozenset[str] | None = None,
    rate_limit_per_minute: int = 0,
    settings: Settings | None = None,
    verify: ssl.SSLContext | None = None,
) -> FastMCP:
    """Build the MCP server with its tools and the ``/healthz`` route.

    The store lives as long as the server: every request (HTTP mode is stateless)
    reads the same copies. With ``auth`` (HTTP mode), ``allowed_ids`` adds the
    per-tool allowlist check as a second layer behind the token verifier.
    ``rate_limit_per_minute`` (0 = off) limits each caller. ``verify`` is the TLS
    context the fetches trust (tests pass their own).
    """
    quiet_http_loggers()
    settings = settings or Settings.from_env()
    store = Store(settings, Fetcher(settings, verify=verify))
    middleware = build_middleware(
        SERVER,
        allowed_ids=allowed_ids,
        rate_limit_per_minute=rate_limit_per_minute,
        named_causes=NAMED_CAUSES,
        signed_in=auth is not None,
        clients=auth if isinstance(auth, RegisteredClients) else None,
    )
    server = FastMCP(
        SERVER.name,
        version=__version__,
        instructions=INSTRUCTIONS,
        auth=auth,
        middleware=middleware,
        # A failure outside the tools' own handling reaches the client as "error
        # calling tool", never with the exception's text (tools/failure.py).
        mask_error_details=True,
    )
    register_tools(server, store)

    async def calendars_answer() -> bool:
        # Settings that can't be used count as down: serve would refuse them.
        return not settings.unreadable_values and await store.healthy()

    server.custom_route("/healthz", methods=["GET"])(
        make_healthz(calendars_answer, "calendar", cache_seconds=HEALTH_CACHE_SECONDS)
    )
    return server
