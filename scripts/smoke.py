#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["fastmcp>=4.1,<5"]
# ///
"""Start second-brain-ics the way a user does, with uvx, and talk MCP to it.

    uv run scripts/smoke.py                                  # the latest release on PyPI
    uv run scripts/smoke.py --from "second-brain-ics==0.1.0"
    uv run scripts/smoke.py --from dist/second_brain_ics-0.1.0-py3-none-any.whl
    uv run scripts/smoke.py --python 3.13                    # on a chosen Python
    uv run scripts/smoke.py --refresh --from "second-brain-ics==0.1.0"   # just released
    uv run scripts/smoke.py --live --env-file ~/.config/second-brain-ics/serve.env

Without --live it needs no feed: it points the server at a closed port and checks
that the server installs, starts on both transports, lists the right tools, and
turns "the feed isn't there" into a clear error. CI runs it that way on Linux,
macOS and Windows against the wheel it just built.

--live reads the feeds named in the environment or in --env-file. It prints
counts only, never an address, a title or any other event text.
"""

import argparse
import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

TOOLS = {"calendar_list", "calendar_events", "calendar_search", "calendar_read"}
# A first run installs the package and its dependencies before the server speaks.
START_TIMEOUT = 300
# What the stdio servers write to stderr; shown only when a check fails.
SERVER_LOG = Path(tempfile.gettempdir()) / f"sbics-smoke-{os.getpid()}.log"
failures: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> bool:
    print(f"{'ok  ' if ok else 'FAIL'}  {what}{f': {detail}' if detail else ''}", flush=True)
    if not ok:
        failures.append(what)
    return ok


def free_port() -> int:
    """A port nothing listens on: taken, then given back."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and name and not name.startswith("#"):
            values[name.strip()] = value.strip().strip("'\"")
    return values


def base_env() -> dict[str, str]:
    """This process's environment without any server setting, so each check sets its own."""
    return {k: v for k, v in os.environ.items() if not k.startswith("SBICS_")}


def text_of(result: Any) -> str:
    return " ".join(getattr(block, "text", "") for block in result.content)


class Server:
    """How to start the server under test: `uvx [--python X] --from SPEC second-brain-ics`."""

    def __init__(self, spec: str, python: str | None) -> None:
        self.options = [*(["--python", python] if python else []), "--from", spec]
        self.prefix = ["uvx", *self.options]

    def install(self, refresh: bool) -> list[str]:
        """The first run, which installs the package: `--version`, asking PyPI again
        with ``refresh`` (uv otherwise answers from what it fetched minutes ago)."""
        refreshed = ["--refresh"] if refresh else []
        return ["uvx", *refreshed, *self.options, "second-brain-ics", "--version"]

    def command(self, *args: str) -> list[str]:
        return [*self.prefix, "second-brain-ics", *args]

    def stdio(self, env: dict[str, str]) -> Client[Any]:
        command = self.command("serve")
        transport = StdioTransport(
            command[0], command[1:], env=env, keep_alive=False, log_file=SERVER_LOG
        )
        return Client(transport, init_timeout=START_TIMEOUT)


async def tool_names(client: Client[Any]) -> set[str]:
    return {tool.name for tool in await client.list_tools()}


async def check_stdio(server: Server, feed_down: dict[str, str]) -> None:
    async with server.stdio(feed_down) as client:
        info = client.server_info
        check(
            info is not None and info.name == "second-brain-ics",
            "stdio: the server says who it is",
            f"{info.name if info else None}, MCP {client.protocol_version}",
        )
        tools = await client.list_tools()
        check({tool.name for tool in tools} == TOOLS, f"stdio: exactly the {len(TOOLS)} tools")
        check(
            all(tool.annotations and tool.annotations.read_only_hint for tool in tools),
            "stdio: every tool is marked read-only",
        )
        result = await client.call_tool("calendar_events", raise_on_error=False)
        message = text_of(result)
        check(
            result.is_error and "could not be reached" in message,
            "stdio: a call with the feed down says so",
            message[:90],
        )
        result = await client.call_tool("calendar_read", {"id": "nope"}, raise_on_error=False)
        check(result.is_error, "stdio: an id that isn't one is refused")


def stop(process: subprocess.Popen[bytes]) -> None:
    """Stop uvx and the server it started (on Windows the child outlives a plain kill)."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=30)


def get_json(url: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:  # /healthz answers 503 with a body
        return error.code, json.load(error)


@contextlib.asynccontextmanager
async def serving_http(
    server: Server, env: dict[str, str]
) -> AsyncIterator[tuple[str, tuple[int, Any] | None]]:
    """`serve --transport http --no-auth` on a free port: its address and first /healthz answer."""
    port = free_port()
    group: dict[str, Any] = {} if sys.platform == "win32" else {"start_new_session": True}
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            server.command("serve", "--transport", "http", "--no-auth", "--port", str(port)),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            **group,
        )
        try:
            health: tuple[int, Any] | None = None
            deadline = time.monotonic() + START_TIMEOUT
            while health is None and process.poll() is None and time.monotonic() < deadline:
                try:
                    health = get_json(f"http://127.0.0.1:{port}/healthz")
                except OSError:
                    await asyncio.sleep(0.5)
            if health is None:
                log.seek(0)
                print(log.read().decode(errors="replace")[-2000:])
            yield f"http://127.0.0.1:{port}", health
        finally:
            stop(process)


async def check_http(server: Server, feed_down: dict[str, str]) -> None:
    async with serving_http(server, feed_down) as (address, health):
        if not check(health is not None, "http: the server starts with --no-auth on loopback"):
            return
        check(
            health == (503, {"server": True, "calendar": False}),
            "http: /healthz reports the feed down with 503",
            str(health),
        )
        async with Client(f"{address}/mcp", init_timeout=30) as client:
            check(await tool_names(client) == TOOLS, f"http: MCP lists the same {len(TOOLS)} tools")


async def check_live(server: Server, env: dict[str, str]) -> None:
    """Read the real feeds. Counts only: no address and no event text is printed."""
    async with server.stdio(env) as client:
        listed = (await client.call_tool("calendar_list")).structured_content
        for calendar in listed["calendars"]:
            status = calendar["status"]
            check(
                status["error"] is None and status["fetched_at"] is not None,
                f"live: {calendar['calendar']} is read",
                f"{calendar['events']} events" if status["error"] is None else status["error"],
            )
        week = (await client.call_tool("calendar_events")).structured_content
        check(
            isinstance(week["occurrences"], list),
            "live: calendar_events answers for this week",
            f"{len(week['occurrences'])} occurrences in {week['timezone']}",
        )


async def run(args: argparse.Namespace) -> None:
    server = Server(args.spec, args.python)
    version = subprocess.run(
        server.install(args.refresh), capture_output=True, text=True, check=False, timeout=600
    )
    shown = version.stdout.strip()
    if not check(version.returncode == 0, "uvx installs and runs the package", shown):
        print(version.stderr[-2000:])
        return
    if args.expect:
        check(shown == f"second-brain-ics {args.expect}", f"the version is {args.expect}")
    python = subprocess.run(
        [*server.prefix, "python", "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    print(f"      the server runs on Python {python.stdout.strip() or '?'}", flush=True)

    feed_down = {
        **base_env(),
        "SBICS_FEEDS": "smoke",
        "SBICS_FEED_SMOKE": f"https://127.0.0.1:{free_port()}/calendar.ics",
        "SBICS_FETCH_TIMEOUT": "5",
    }
    await check_stdio(server, feed_down)
    await check_http(server, feed_down)
    check_cmd = subprocess.run(
        server.command("check"), env=feed_down, capture_output=True, text=True, check=False
    )
    check(
        check_cmd.returncode == 1 and "could not be reached" in check_cmd.stdout,
        "check: says the feed can't be reached, with exit status 1",
        check_cmd.stdout.strip()[:90],
    )
    if args.live:
        settings = read_env_file(args.env_file) if args.env_file else dict(os.environ)
        live = {k: v for k, v in settings.items() if k.startswith("SBICS_")}
        live = {
            k: v for k, v in live.items() if not k.startswith(("SBICS_TRANSPORT", "SBICS_PORT"))
        }
        if check("SBICS_FEEDS" in live, "live: feeds are named"):
            await check_live(server, {**base_env(), **live})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--from",
        dest="spec",
        default="second-brain-ics",
        help="what uvx installs: a requirement or a wheel (default: the latest on PyPI)",
    )
    parser.add_argument("--python", help="the Python uvx runs it on, e.g. 3.13")
    parser.add_argument("--expect", metavar="VERSION", help="fail unless this version starts")
    parser.add_argument(
        "--refresh", action="store_true", help="ask PyPI again first (for a version just released)"
    )
    parser.add_argument("--live", action="store_true", help="also read the real feeds")
    parser.add_argument("--env-file", type=Path, help="with --live: read the feeds' settings here")
    args = parser.parse_args(argv)
    print(f"{sys.platform}, Python {sys.version.split()[0]}, testing {args.spec}", flush=True)
    try:
        asyncio.run(run(args))
        if failures and SERVER_LOG.exists():
            print(SERVER_LOG.read_text(encoding="utf-8", errors="replace")[-4000:])
    finally:
        with contextlib.suppress(OSError):  # Windows: a server may still hold it
            SERVER_LOG.unlink(missing_ok=True)
    print(f"{len(failures)} failed" if failures else "all passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
