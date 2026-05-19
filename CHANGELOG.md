# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
