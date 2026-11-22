"""second-brain-core's security regression checks, run against this server's own package.

The rules live in second-brain-core and are tested there; these runs show that this
package keeps them (``second_brain_core.testing``).
"""

from pathlib import Path

from second_brain_core.env import checkout_env_file
from second_brain_core.testing import (
    check_dotenv_cannot_set_fastmcp,
    check_each_imports_alone,
    check_no_invisible_characters,
)

from second_brain_ics.identity import SERVER

ROOT = Path(__file__).resolve().parent.parent
ENTRY_POINTS = (
    "second_brain_ics",
    "second_brain_ics.cli",
    "second_brain_ics.server",
    "second_brain_ics.tools.read",
    "second_brain_ics.feed",
    "second_brain_ics.fetch",
)


def test_the_working_directorys_dotenv_cannot_set_fastmcps_settings(tmp_path: Path) -> None:
    check_dotenv_cannot_set_fastmcp("second_brain_ics", tmp_path)


def test_each_entry_point_imports_on_its_own() -> None:
    check_each_imports_alone(ENTRY_POINTS)


def test_no_file_here_holds_an_invisible_character() -> None:
    check_no_invisible_characters(
        ROOT, ("src/**/*.py", "tests/**/*.py", "tests/feeds/*.ics", "scripts/**/*.py")
    )


def test_this_package_reads_only_its_own_checkouts_dotenv(tmp_path: Path) -> None:
    package_dir = SERVER.package_dir()
    assert package_dir is not None and (package_dir / "cli.py").is_file()
    other = tmp_path / "clone" / "src" / "second_brain_ics"
    other.mkdir(parents=True)
    (tmp_path / "clone" / "pyproject.toml").write_text('[project]\nname = "something-else"\n')
    (tmp_path / "clone" / ".env").write_text("SBICS_FEEDS=x\n")
    assert checkout_env_file(SERVER, other) is None
    (tmp_path / "clone" / "pyproject.toml").write_text('[project]\nname = "second-brain-ics"\n')
    assert checkout_env_file(SERVER, other) == tmp_path / "clone" / ".env"
