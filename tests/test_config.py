"""Reading the settings: what is accepted, what is refused, and that reading never raises."""

import pytest

from second_brain_ics.config import Settings, feed_var, normalise_url


def test_nothing_set_is_refused_with_a_sentence() -> None:
    settings = Settings.from_env()
    assert settings.feeds == ()
    assert settings.unreadable_values == (
        "SBICS_FEEDS must name at least one feed, separated by commas",
    )


def test_feeds_and_their_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SBICS_FEEDS", "home, work_2")
    monkeypatch.setenv("SBICS_FEED_HOME", "webcal://calendar.example.com/a.ics?t=1")
    monkeypatch.setenv("SBICS_FEED_WORK_2", "https://example.org/b")
    settings = Settings.from_env()
    assert settings.unreadable_values == ()
    assert settings.names == ("home", "work_2")
    assert settings.feeds[0].url == "https://calendar.example.com/a.ics?t=1"
    assert settings.timezone == "UTC"
    assert settings.min_fetch_seconds == 60 and settings.fetch_timeout == 30
    assert settings.max_feed_bytes == 10 * 1024 * 1024
    assert "calendar.example.com" not in repr(settings)
    assert settings.feed("nope") is None


@pytest.mark.parametrize(
    ("names", "words"),
    [
        ("Home", "is not a feed name"),
        ("home-work", "is not a feed name"),
        ("a" * 33, "is not a feed name"),
        ("home,home", "names 'home' twice"),
        ("absent", "must hold the address of feed 'absent'"),
    ],
)
def test_bad_feed_names(monkeypatch: pytest.MonkeyPatch, names: str, words: str) -> None:
    monkeypatch.setenv("SBICS_FEEDS", names)
    monkeypatch.setenv("SBICS_FEED_HOME", "https://example.com/a")
    assert any(words in problem for problem in Settings.from_env().unreadable_values)


@pytest.mark.parametrize(
    ("variable", "value", "words"),
    [
        ("SBICS_TIMEZONE", "Mars/Olympus", "must be an IANA time zone"),
        ("SBICS_TIMEZONE", " UTC", None),
        ("SBICS_MIN_FETCH_SECONDS", "5", "from 10 to 86400"),
        ("SBICS_MIN_FETCH_SECONDS", "soon", "from 10 to 86400"),
        ("SBICS_FETCH_TIMEOUT", "-1", "above 0"),
        ("SBICS_MAX_FEED_MB", "51", "from 1 to 50"),
    ],
)
def test_bad_values_keep_their_default_and_are_refused(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, words: str | None
) -> None:
    monkeypatch.setenv("SBICS_FEEDS", "home")
    monkeypatch.setenv("SBICS_FEED_HOME", "https://example.com/a")
    monkeypatch.setenv(variable, value)
    settings = Settings.from_env()
    if words is None:
        assert settings.unreadable_values == ()
    else:
        assert any(words in problem for problem in settings.unreadable_values)
    assert settings.timezone == "UTC" and settings.min_fetch_seconds == 60


def test_the_address_variable_name() -> None:
    assert feed_var("work_2") == "SBICS_FEED_WORK_2"


@pytest.mark.parametrize(
    ("raw", "url"),
    [
        ("https://example.com", "https://example.com/"),
        ("  WEBCAL://Example.com/x.ics  ", "https://Example.com/x.ics"),
        ("https://example.com:8443/x?a=1&b=2", "https://example.com:8443/x?a=1&b=2"),
        ("http://example.com/x", None),
        ("https:///x", None),
        ("https://u@example.com/x", None),
        ("https://example.com/x#frag", None),
        ("https://[::1/x", None),
    ],
)
def test_addresses(raw: str, url: str | None) -> None:
    assert normalise_url(raw) == url


def test_an_address_put_in_the_list_of_names_is_not_printed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SBICS_FEEDS", "https://cal.example/secret/TOKEN123.ics")
    problems = " ".join(Settings.from_env().unreadable_values)
    assert "TOKEN123" not in problems and "an entry (not shown)" in problems
