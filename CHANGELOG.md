# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.3] — 2026-05-23

### Changed
- "What an auditable agent run looks like" worked transcript now uses real data reproduced from the [`claude-eval-harness`](https://github.com/maximizeGPT/claude-eval-harness) `anomaly_zero_activity_june` case (`runs/baseline-sonnet-4-6.json`) instead of an illustrative one, closing the credibility gap with the sibling eval-harness repo.

### Added
- README "Standalone Python usage" section — surfaces that `NetSuiteExport` is usable directly from `from netsuite_saved_search_mcp.parser import NetSuiteExport` without the MCP transport. Useful for notebooks, batch scripts, pytest fixtures.
- README "Security boundary" section — documents `NSMCP_ROOT` enforcement, `_resolve_under_root` symlink-escape protection (hyperlinked to the implementation in `src/`), and the no-writes / no-network / scoped-stderr-logs guarantees that an audit audience needs as a Section 1 concern. Complements (does not replace) `SECURITY.md`.
- README Limitations: three new honest items — `detect_anomalies` period-label format constraint, unbounded parsed-export cache caveat, fixture-only test-coverage scope.

### Fixed
- Stale `__version__ = "0.1.0"` in `src/netsuite_saved_search_mcp/__init__.py` (the v0.1.1 release bumped only `pyproject.toml`). Now tracks `pyproject.toml`.

## [0.1.2] — 2026-05-23

### Added
- README badges row: CI / PyPI / GitHub release / MIT license.
- README "What an auditable agent run looks like" — condensed 4-turn
  worked example showing tool-cited row IDs, before the deep walkthrough.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
- `CONTRIBUTING.md` — setup, tests, useful-bug-report shape, code style.
- `SECURITY.md` — supported versions + reporting via rayedwasif@hotmail.com.
- `.github/ISSUE_TEMPLATE/{bug_report.md, feature_request.md}` —
  bug template requires the MCP server log + sanitized fixture.
- `.github/PULL_REQUEST_TEMPLATE.md` — pytest / ruff / mypy / changelog /
  tool-table checklist.
- `tests/fixtures/README.md` — documents what each of the three
  sanitized fixtures tests (clean GL, metadata-block, malformed-recovery).

## [0.1.1] — 2026-05-19

### Changed
- `get_parse_warnings` now requires a `file_path` parameter (breaking change). Previous "most recent parse" semantics were ambiguous in long-running sessions where multiple files have been queried. The tool now parses on demand if the file is not yet cached.

### Added
- `examples/claude_desktop_config.json` — minimal working Claude Desktop config
- `examples/walkthrough.md` — end-to-end walkthrough using the sanitized fixtures
- `examples/README.md` — index

## [0.1.0] — 2026-05-19

Initial release.

- `NetSuiteExport` parser for NetSuite XML SpreadsheetML .xls exports
- Seven MCP tools: `list_exports`, `get_headers`, `query_export`, `aggregate_export`, `categorize_by_memo`, `detect_anomalies`, `get_parse_warnings`
- Pydantic discriminated-union predicate model
- Stdio MCP server entrypoint
- 115 tests, mypy strict, ruff clean
