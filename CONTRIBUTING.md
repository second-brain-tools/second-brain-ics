# Contributing

Thanks for your interest in second-brain-ics.

## Where the code lives

Sign-in, the audit log, HTTP serving, the settings files and the text cleaner are shared with the
other Second Brain servers in
[second-brain-core](https://github.com/second-brain-tools/second-brain-core). **Fix shared code
there**, release it, then move this package's pin; this repository holds what is a calendar
feed's: fetching (`fetch.py`, the only module that touches the network or an address), reading
and expanding (`feed.py`), the copies in memory (`store.py`), and the tools. To try an unreleased
core change here, point uv at your checkout for the moment, and don't commit it:

```toml
[tool.uv.sources]
second-brain-core = { path = "../second-brain-core", editable = true }
```

Issues are tracked on the organisation's public tracker,
[second-brain-joplin](https://github.com/second-brain-tools/second-brain-joplin/issues).

## Development setup

```bash
git clone https://github.com/second-brain-tools/second-brain-ics
cd second-brain-ics
uv sync
uv run pre-commit install   # lint and format on commit
```

## Running tests

```bash
uv run pytest --cov-fail-under=90
```

The suite needs no feed and no network: it starts its own HTTPS server
([pytest-httpserver](https://pypi.org/project/pytest-httpserver/)) with a test certificate
authority ([trustme](https://pypi.org/project/trustme/)) and serves hand-made feeds
(`tests/feeds/`, `tests/build.py`), never a real calendar. The 90 % gate is enforced in CI, not in
`addopts`, so a subset run locally doesn't fail it.

Two rules the suite enforces:

- **No feed address in any output** (`tests/test_leak.py`). Every address the tests use carries a
  marker; a change that logs, raises or returns one fails there.
- **No invisible characters in the source** (second-brain-core's check). Write them as escapes;
  `tests/build.py` has the ones the tests need.

## Linting and type checking

```bash
uvx pre-commit run --all-files   # ruff lint + format + actionlint (the authoritative gate)
uv run mypy                      # strict, over src, tests and scripts
```

## How CI works

All quality gates live in one reusable workflow,
[`.github/workflows/checks.yml`](.github/workflows/checks.yml): **lint**, **typecheck**, a **test**
matrix (Python 3.11 to 3.15 on Linux, plus a run each on macOS and Windows), and a **build** job
that builds the package and runs `twine check`. The built wheel is then smoke-tested on all three
systems: started with `uvx` and spoken to over MCP, on stdio and HTTP
([`scripts/smoke.py`](scripts/smoke.py)). Both [`ci.yml`](.github/workflows/ci.yml) and
[`release.yml`](.github/workflows/release.yml) call it, so nothing merges or ships without passing
the same checks.

## Labels

| Prefix | Values | Applied by |
|---|---|---|
| `component:` | `mcp`, `feeds`, `docs`, `ci` | [labeler bot](.github/workflows/labeler.yml) (PRs) |
| meta | `epic`, `backlog`, `needs-triage`, `blocked`, `good first issue`, `help wanted` | Maintainer |

## License of contributions

The server is licensed under the GNU Affero General Public License, version 3 or later
([LICENSE](LICENSE)). By opening a pull request you agree to license your change under it. There
is no contributor agreement to sign. A sign-off (`git commit -s`) says you have the right to
submit the change under those terms ([Developer Certificate of Origin](https://developercertificate.org/)).

## Pull requests

- Target `main`; one logical change per PR, with its tests and its CHANGELOG entry.
- Titles in the conventional style, lower case: `fix: …`, `feat: …`, `docs: …`.
- CI must pass before merge.
