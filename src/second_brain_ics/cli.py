"""Command-line entry point: ``serve`` and ``check``."""

import argparse
import asyncio
import logging
from pathlib import Path

from second_brain_core.audit import configure_logging
from second_brain_core.auth import AuthSettings, build_auth
from second_brain_core.env import load_configuration
from second_brain_core.remote import (
    TRANSPORTS,
    choose_transport,
    http_options,
    refuse_http_flags,
    run_http,
    run_stdio,
    validate,
)

from . import __version__
from .config import Settings
from .errors import UNREADABLE, FeedError, ServeConfigError
from .feed import parse_feed
from .fetch import Fetcher
from .identity import SERVER
from .server import create_server, quiet_http_loggers

logger = logging.getLogger("second_brain_ics.cli")

ENV_FILE_HELP = "load settings from this file first (variables already set win)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="second-brain-ics",
        description="A read-only MCP server for calendar feeds (iCalendar share links).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser(
        "serve",
        help="Start the MCP server (stdio by default, or streamable HTTP).",
        description=(
            "Start the MCP server. stdio is for a client on this machine (Claude Code); "
            "http is for a remote client such as a Claude custom connector "
            "(docs/remote-access.md). Every option falls back to the SBICS_* variable named."
        ),
    )
    serve.add_argument(
        "--transport", choices=TRANSPORTS, help="stdio (default) or http  [SBICS_TRANSPORT]"
    )
    serve.add_argument("--host", help="HTTP: address to bind (default 127.0.0.1)  [SBICS_HOST]")
    serve.add_argument(
        "--port", type=int, help="HTTP: port to listen on (default 8777)  [SBICS_PORT]"
    )
    serve.add_argument("--path", help="HTTP: MCP endpoint path (default /mcp)  [SBICS_HTTP_PATH]")
    serve.add_argument(
        "--public-url",
        help="HTTP: the server's public origin, e.g. https://ics.example.com  [SBICS_PUBLIC_URL]",
    )
    serve.add_argument(
        "--no-auth",
        action="store_true",
        help="HTTP: run without sign-in, on a loopback address only (local testing)",
    )
    serve.add_argument("--env-file", type=Path, help=ENV_FILE_HELP)
    check = subparsers.add_parser(
        "check",
        help="Fetch each feed once and say whether it worked.",
        description=(
            "Fetch and read every feed in SBICS_FEEDS once. Prints counts and timings "
            "only, never an address or any event's text. Exit status 0 when every feed "
            "was read."
        ),
    )
    check.add_argument("--env-file", type=Path, help=ENV_FILE_HELP)
    return parser


def refuse_unreadable_settings(settings: Settings) -> None:
    """Refuse settings that can't be used. Their sentences never quote a feed's address."""
    if settings.unreadable_values:
        raise ServeConfigError("; ".join(settings.unreadable_values))


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


async def _check_one(fetcher: Fetcher, settings: Settings, name: str) -> tuple[bool, str]:
    feed = settings.feed(name)
    assert feed is not None
    try:
        fetched = await fetcher.fetch(feed)
        parsed = await asyncio.to_thread(parse_feed, name, fetched.body)
    except FeedError as exc:
        return False, f"error  {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 - its text may quote the feed; the type is enough
        return False, f"error  {name}: {FeedError(name, UNREADABLE)} ({type(exc).__name__})"
    set_aside = sum(parsed.set_aside().values())
    return True, (
        f"ok     {name}: {parsed.size / 1024:.1f} KB in {fetched.seconds:.1f} s; "
        f"{len(parsed.series)} events, {parsed.duplicates} duplicates dropped, "
        f"{set_aside} set aside, {parsed.floating} floating, "
        f"{parsed.unknown_zones} in unknown zones; "
        f"calendar zone {_yes(parsed.zone is not None)}"
        + (" (named but not a zone: ignored)" if parsed.zone_dropped else "")
    )


def check(_args: argparse.Namespace) -> int:
    quiet_http_loggers()
    settings = Settings.from_env()
    refuse_unreadable_settings(settings)
    fetcher = Fetcher(settings)

    async def run() -> list[tuple[bool, str]]:
        return [await _check_one(fetcher, settings, name) for name in settings.names]

    results = asyncio.run(run())
    for _, text in results:
        print(text)
    return 0 if all(ok for ok, _ in results) else 1


def serve(args: argparse.Namespace) -> None:
    transport = choose_transport(SERVER, args.transport)
    settings = Settings.from_env()
    refuse_unreadable_settings(settings)
    if transport == "stdio":
        refuse_http_flags(
            {
                "--host": args.host,
                "--port": args.port,
                "--path": args.path,
                "--public-url": args.public_url,
                "--no-auth": args.no_auth or None,
            }
        )
        # Never FastMCP's own FASTMCP_TRANSPORT: run_stdio names the transport.
        run_stdio(create_server(settings=settings))
        return
    options = http_options(
        SERVER,
        host=args.host,
        port=args.port,
        path=args.path,
        public_url=args.public_url,
        no_auth=args.no_auth,
    )
    auth_settings = AuthSettings.from_env(SERVER)
    validate(SERVER, options, auth_configured=auth_settings is not None)
    configure_logging(SERVER)
    logger.info("serving %d calendar feeds: %s", len(settings.names), ", ".join(settings.names))
    auth = None
    if auth_settings is not None:
        assert options.public_url is not None  # validate() guarantees it with sign-in
        auth = build_auth(SERVER, auth_settings, options.public_url)
    server = create_server(
        auth=auth,
        allowed_ids=auth_settings.allowed_ids if auth_settings else None,
        rate_limit_per_minute=options.rate_limit_per_minute,
        settings=settings,
    )
    run_http(server, options)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return
    try:
        # The named file is the configuration. A checkout's .env is not read as
        # well: it would fill in whatever the file leaves out.
        load_configuration(SERVER, getattr(args, "env_file", None))
        if args.command == "serve":
            serve(args)
        elif args.command == "check":
            status = check(args)
            if status:
                parser.exit(status)
    except ServeConfigError as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")


if __name__ == "__main__":  # pragma: no cover
    main()
