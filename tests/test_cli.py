"""The command line: check's report, and serve's refusals."""

import ssl

import pytest
from build import calendar, event
from conftest import ServeFeed, feed_text, settings_with

from second_brain_ics import cli
from second_brain_ics.fetch import Fetcher


def run_check(monkeypatch: pytest.MonkeyPatch, trusted: ssl.SSLContext) -> int | None:
    monkeypatch.setattr(cli, "Fetcher", lambda settings: Fetcher(settings, verify=trusted))
    try:
        cli.main(["check"])
    except SystemExit as exited:
        return exited.code  # type: ignore[return-value]
    return 0


def test_check_reports_counts_per_feed(
    monkeypatch: pytest.MonkeyPatch,
    serve_feed: ServeFeed,
    trusted: ssl.SSLContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_with(monkeypatch, {"main": serve_feed(feed_text("proton.ics"))})
    assert run_check(monkeypatch, trusted) == 0
    out = capsys.readouterr().out
    assert out.startswith("ok     main: ")
    assert "3 events, 3 duplicates dropped, 0 set aside, 0 floating" in out
    assert "calendar zone yes" in out
    assert "Stand-up" not in out  # never an event's text


def test_check_fails_when_a_feed_fails(
    monkeypatch: pytest.MonkeyPatch, trusted: ssl.SSLContext, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_with(monkeypatch, {"gone": "https://127.0.0.1:1/x.ics"})
    assert run_check(monkeypatch, trusted) == 1
    assert "error  gone: The calendar feed 'gone' could not be reached" in capsys.readouterr().out


def test_check_says_when_a_calendar_zone_was_ignored(
    monkeypatch: pytest.MonkeyPatch,
    serve_feed: ServeFeed,
    trusted: ssl.SSLContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = calendar(event("a@t", "DTSTART:20270101T000000Z"), zone="Not/AZone")
    settings_with(monkeypatch, {"main": serve_feed(body)})
    assert run_check(monkeypatch, trusted) == 0
    assert "calendar zone no (named but not a zone: ignored)" in capsys.readouterr().out


def test_unusable_settings_are_refused_with_exit_status_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exited:
        cli.main(["serve"])
    assert exited.value.code == 2
    assert "SBICS_FEEDS must name at least one feed" in capsys.readouterr().err


def test_stdio_refuses_http_flags(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_with(monkeypatch, {"main": "https://example.com/a.ics"})
    with pytest.raises(SystemExit) as exited:
        cli.main(["serve", "--port", "9000"])
    assert exited.value.code == 2


def test_http_without_sign_in_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_with(monkeypatch, {"main": "https://example.com/a.ics"})
    with pytest.raises(SystemExit) as exited:
        cli.main(["serve", "--transport", "http"])
    assert exited.value.code == 2


def test_serve_runs_stdio_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    settings_with(monkeypatch, {"main": "https://example.com/a.ics"})
    ran: list[object] = []
    monkeypatch.setattr(cli, "run_stdio", ran.append)
    cli.main(["serve"])
    assert len(ran) == 1


def test_serve_over_http_on_loopback_without_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    settings_with(monkeypatch, {"main": "https://example.com/a.ics"})
    ran: list[object] = []
    monkeypatch.setattr(cli, "run_http", lambda server, options: ran.append(options))
    cli.main(["serve", "--transport", "http", "--no-auth"])
    assert len(ran) == 1


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main([])
    assert "usage: second-brain-ics" in capsys.readouterr().out
