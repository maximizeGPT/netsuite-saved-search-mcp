# netsuite-saved-search-mcp

[![CI](https://github.com/maximizeGPT/netsuite-saved-search-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/maximizeGPT/netsuite-saved-search-mcp/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/netsuite-saved-search-mcp.svg)](https://pypi.org/project/netsuite-saved-search-mcp/)
[![Release](https://img.shields.io/github/v/release/maximizeGPT/netsuite-saved-search-mcp?include_prereleases&sort=semver)](https://github.com/maximizeGPT/netsuite-saved-search-mcp/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

NetSuite saved search exports look like Excel files but are actually XML SpreadsheetML with a .xls extension — Excel opens them, pandas trips on them, and most teams writing Claude integrations against NetSuite end up rebuilding the same parser by hand. This MCP server exposes those exports as a structured query interface for Claude: discover files, inspect columns, filter and aggregate rows, categorize by memo keywords, surface anomalies. Seven tools wrap a parser that handles the format's real quirks — entity-encoded text, empty cells flagged with `ss:Index`, drifting column layouts between saved searches, DateTime cells that won't parse. The parser runs in lxml's recovery mode and reports broken cells as structured warnings rather than crashing on them.

## What an auditable agent run looks like

A condensed exchange reproduced from the [`claude-eval-harness`](https://github.com/maximizeGPT/claude-eval-harness)
NetSuite suite's `anomaly_zero_activity_june` case (`runs/baseline-sonnet-4-6.json`).
The agent's tool calls are auditable, the parser's `total_supporting_count`
is citable, and the model never has to fabricate numbers it can't point at.

```
user      What stands out in this GL? Run detect_anomalies on
          sample_gl_export.xls (account_column=Account,
          amount_column=Amount, period_column=Period).

agent     → detect_anomalies(file_path="sample_gl_export.xls",
                             account_column="Account",
                             amount_column="Amount",
                             period_column="Period")
          ← {findings: [
              {severity:"HIGH", category:"zero_activity_period",
               description:"No rows recorded for Jun 2024",
               total_supporting_count:0},
              {severity:"MEDIUM", category:"ratio_anomaly",
               description:"Account 1200 in Sep 2024 totals 319,219.74
                            — 3.1x the 1200 median of 103,259.01",
               supporting_rows:[...10 rows...],
               total_supporting_count:17}]}

agent     HIGH: no rows posted to June 2024 inside an otherwise continuous
          range — likely an extraction gap or skipped close. MEDIUM:
          Account 1200 spiked to 3.1× its own median in September across
          17 cited rows. supporting_rows trims to 10; total_supporting_count
          carries the full count so it's verifiable.
```

The full anomaly response also lives in [`examples/walkthrough.md`](examples/walkthrough.md).

## Quick start

```bash
uvx netsuite-saved-search-mcp           # or: pip install netsuite-saved-search-mcp
export NSMCP_ROOT=/path/to/your/exports
```

Add to Claude Desktop's config (full version in [examples/claude_desktop_config.json](examples/claude_desktop_config.json)):

```json
{
  "mcpServers": {
    "netsuite-saved-search": {
      "command": "uvx",
      "args": ["netsuite-saved-search-mcp"],
      "env": {"NSMCP_ROOT": "/path/to/your/exports"}
    }
  }
}
```

Then any tool call lands directly:

```json
{
  "tool": "query_export",
  "arguments": {
    "file_path": "Q3_GL.xls",
    "filters": [{"op": "eq", "column": "Account", "value": "4000"}]
  }
}
```

## Standalone Python usage

The parser is usable directly without the MCP transport — useful for notebooks, batch scripts, or pytest fixtures that don't want stdio in the loop:

```python
from netsuite_saved_search_mcp.parser import NetSuiteExport

export = NetSuiteExport("tests/fixtures/sample_gl_export.xls")
print(export.headers)            # ['Order Type', 'Date', 'Period', ...]
print(len(export.rows))          # 212
print(export.rows[0]["Account"]) # '4000'
```

`NetSuiteExport(path)` parses the file end-to-end on construction. `.rows` is a list of dicts keyed by header name; cell values are typed (`str`, `int`, `float`, `datetime.date`, `bool`, or `None`). `.parse_warnings` exposes any recoverable issues lxml hit on the way through.

## Security boundary

For audit and accounting use, the server enforces a tight blast radius on what it can touch. (Reporting channel for vulnerabilities is in [`SECURITY.md`](SECURITY.md).)

- **All reads constrained under `NSMCP_ROOT`.** Every tool resolves its file-path argument relative to this env var (or `os.getcwd()` if unset). Paths that resolve outside the root raise `PathTraversalError` before any I/O.
- **Symlink-escape blocked via realpath comparison.** [`_resolve_under_root`](src/netsuite_saved_search_mcp/tools.py#L59) calls `Path.resolve()` on the candidate then checks `relative_to(root)`. `resolve()` collapses `..` segments and follows symlinks to their real target, so a symlink inside the root that points outside it fails the check.
- **No writes.** The parser opens `.xls` files for reading only. No tool writes to the filesystem.
- **No network calls.** Runtime dependencies are `mcp`, `lxml`, `python-dateutil`, `pydantic` — none of them dial out during a tool call.
- **Stderr logs are scoped.** Startup logs the `NSMCP_ROOT` path. Tool calls log nothing by default; row data, column values, and financial figures never reach the log handler.

## Why this exists

NetSuite saved search exports use XML SpreadsheetML, not Excel binary, despite the .xls extension. Column layouts drift between saved searches, so code that hardcodes column letters breaks on the next export. Empty cells are silently omitted from each row with `ss:Index` attributes marking where they were, which trips naive sequential parsers. Every finance team using Claude with NetSuite ends up rebuilding the same parser. This server solves it once.

## Tools

| Tool | Description | Key parameters |
|---|---|---|
| `list_exports` | Scan a directory for .xls files; return one summary per file with row counts, header counts, warning counts, and detected date range. | `directory` |
| `get_headers` | Return column headers, their spreadsheet column letters, and the 0-indexed header row. | `file_path` |
| `query_export` | Filter rows by a list of predicates (AND-combined), optionally project to a subset of columns, cap results. | `file_path, filters, columns?, limit?` |
| `aggregate_export` | Group rows by one or more columns; compute sum/count/avg/min/max per group. | `file_path, group_by, measures` |
| `categorize_by_memo` | Tag every row with a `_category` derived from case-insensitive keyword rules across one or more memo columns. | `file_path, memo_columns, rules` |
| `detect_anomalies` | Three checks: zero-activity periods (HIGH), ratio anomalies (MEDIUM), document-count variance (MEDIUM). | `file_path, account_column, amount_column, period_column` |
| `get_parse_warnings` | Return parse warnings (phantom_column, bad_datetime, encoding_recovery, empty_row_skipped) captured during parsing of the specified file. | `file_path` |

Predicates are a discriminated union keyed on `op`. Example query with two predicates:

```json
{
  "file_path": "deferred_commissions_2024.xls",
  "filters": [
    {"op": "eq", "column": "Account", "value": "1321"},
    {"op": "date_range", "column": "Date", "start": "2024-01-01", "end": "2024-12-31"}
  ],
  "columns": ["Date", "Document Number", "Amount", "Memo (line)"],
  "limit": 100
}
```

Measures for `aggregate_export`:

```json
[{"column": "Amount", "op": "sum", "alias": "total"}, {"column": "Document Number", "op": "count"}]
```

## Example walkthrough

See [examples/walkthrough.md](examples/walkthrough.md) for an end-to-end example using the included sanitized fixtures.

## Limitations

- Only handles saved search exports, not raw transaction-level XML from SuiteScript or RESTlets.
- All-string exports with no typed columns may misidentify the header row; an explicit `header_row` override is planned.
- Memo categorization uses case-insensitive substring matching against US-English keywords. No stemming, no fuzzy matching.
- `detect_anomalies` only recognises period labels in three formats — `Jan 2024`, `January 2024`, `2024-01`. Quarter labels (`Q1 2024`) and fiscal-period labels are silently skipped. The ratio and document-count checks also need ≥3 distinct periods to produce a finding.
- Not optimized for exports larger than 100k rows. The cache holds parsed `NetSuiteExport` instances in memory keyed by `(path, mtime)` and is unbounded — a long-running session against a large directory will keep every parsed export resident.
- Coverage tested against three synthesized fixtures that exercise typed cells, lxml recovery, and 14-row-metadata header detection. Real-world saved searches with column layouts beyond those shapes may surface gaps.
- v0.1. The MCP tool schemas and the parser's `Predicate`/`Measure` models may change before v1.0.

## Contributing

Issues and PRs welcome. Run `uv run pytest`, `uv run mypy src`, and `uv run ruff check src tests` before submitting; all three should be clean. Commits follow [Conventional Commits](https://www.conventionalcommits.org/). New tools require a Pydantic response model, a happy-path test against the included GL fixture, a failure-path test (missing file or unknown column), and an entry in the table above.

## License

MIT. See [LICENSE](LICENSE).
