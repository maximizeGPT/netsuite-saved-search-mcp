# Contributing to netsuite-saved-search-mcp

Thanks for considering a contribution. This file covers the practical
shape of contributing — setup, tests, reporting, code style, and how
PRs land. The README's `## Quick start` covers the user install path;
this file is for people changing code.

## Setting up locally

```bash
git clone https://github.com/maximizeGPT/netsuite-saved-search-mcp.git
cd netsuite-saved-search-mcp
uv sync
```

The included sanitized fixtures (`tests/fixtures/`) cover the three
breakage modes the parser handles: standard `header_row=0` exports,
metadata-prefixed exports (`header_row=14`), and malformed XML
requiring lxml's recovery mode. They're generated deterministically
from `tests/fixtures/generate_fixtures.py` if you need to regenerate.

## Running tests

```bash
uv run pytest                          # 115 tests
uv run ruff check src tests            # style + lint
uv run mypy src/netsuite_saved_search_mcp   # strict type check
```

CI runs the same three commands. The full suite includes parser
edge-cases (entity encoding recovery, phantom columns, DateTime
recovery) plus per-tool happy + failure paths.

## Filing a useful bug report

Open an issue using the `[BUG]` template. The thing that makes a
report useful — and that I'll ask for if it's missing — is the
MCP server log snippet plus a sanitized fixture that reproduces.

The server log surfaces parse warnings as structured events. Find
yours via:

```bash
export NSMCP_LOG_LEVEL=DEBUG
uvx netsuite-saved-search-mcp 2>&1 | tee /tmp/nsmcp.log
```

Then drive the tool call from Claude Desktop, paste the relevant
log lines into the issue. **Do not paste production exports** —
sanitize first by running through `tests/fixtures/generate_fixtures.py`
as a template, or run the export through `scripts/sanitize.py`
(planned).

## Code style

Python code follows the shape of what's already there — pydantic
discriminated unions for predicates and measures, narrow protocol
surfaces, per-tool response models. `ruff check` enforces line
length, import order, and unused locals. `mypy --strict` catches
type regressions across the parser → tool boundary.

Commits follow [Conventional Commits](https://www.conventionalcommits.org/) —
`fix:`, `feat:`, `docs:`, `chore:`, `ci:`, `refactor:`, `test:`.
That's also what shows up in the auto-generated CHANGELOG sections.

## Pull request flow

I (Mohammed Wasif, [@maximizeGPT](https://github.com/maximizeGPT))
am the sole maintainer right now. Expect ~48-hour response time on
PRs.

For anything that changes the tool schemas, the `Predicate` or
`Measure` model, the `NetSuiteExport` parser API, or the MCP
response shape — **open an issue first** so the design discussion
happens before the code review. PRs against a solid issue land in
days; PRs that surface design questions in the diff take weeks
because the conversation happens twice.

For everything else — bug fixes, parser improvements,
new categorization rules, doc additions — just open the PR.
**A new tool requires**: a Pydantic response model, a happy-path
test against the included GL fixture, a failure-path test (missing
file or unknown column), an entry in the README's tool table, and
a note in `CHANGELOG.md` under `[Unreleased]`.
