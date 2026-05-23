# netsuite-saved-search-mcp

[![CI](https://github.com/maximizeGPT/netsuite-saved-search-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/maximizeGPT/netsuite-saved-search-mcp/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/netsuite-saved-search-mcp.svg)](https://pypi.org/project/netsuite-saved-search-mcp/)
[![Release](https://img.shields.io/github/v/release/maximizeGPT/netsuite-saved-search-mcp?include_prereleases&sort=semver)](https://github.com/maximizeGPT/netsuite-saved-search-mcp/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

NetSuite saved search exports look like Excel files but are actually XML SpreadsheetML with a .xls extension — Excel opens them, pandas trips on them, and most teams writing Claude integrations against NetSuite end up rebuilding the same parser by hand. This MCP server exposes those exports as a structured query interface for Claude: discover files, inspect columns, filter and aggregate rows, categorize by memo keywords, surface anomalies. Seven tools wrap a parser that handles the format's real quirks — entity-encoded text, empty cells flagged with `ss:Index`, drifting column layouts between saved searches, DateTime cells that won't parse. The parser runs in lxml's recovery mode and reports broken cells as structured warnings rather than crashing on them.

## What an auditable agent run looks like

A condensed exchange from [`examples/walkthrough.md`](examples/walkthrough.md).
The agent's tool calls are auditable, the parser's row IDs are
citable, and the model never has to fabricate numbers it can't
point at.

```
user      What GL accounts had unusual activity last quarter?

agent     → get_headers(file_path="Q3_GL.xls")
          ← {header_row: 0, columns: ["Date","Account","Amount","Memo"], row_count: 212}

agent     → detect_anomalies(file_path="Q3_GL.xls",
                             account_column="Account",
                             amount_column="Amount",
                             period_column="Date")
          ← [{level:"HIGH", type:"zero_activity",
              account:"1200", period:"Jun 2024", row_ids:[]},
             {level:"MEDIUM", type:"ratio_anomaly",
              account:"4000", period:"Sep 2024", multiple:3.1, row_ids:[145..157]}]

agent     Account 1200 had zero activity in June 2024 (HIGH — gap in the
          GL). Account 4000 spiked 3.1× in September (MEDIUM — rows
          145–157). Both surfaced by `detect_anomalies`; row IDs are
          quotable for audit.
```

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
- Not optimized for exports larger than 100k rows. The cache holds parsed `NetSuiteExport` instances in memory keyed by `(path, mtime)`.
- v0.1. The MCP tool schemas and the parser's `Predicate`/`Measure` models may change before v1.0.

## Contributing

Issues and PRs welcome. Run `uv run pytest`, `uv run mypy src`, and `uv run ruff check src tests` before submitting; all three should be clean. Commits follow [Conventional Commits](https://www.conventionalcommits.org/). New tools require a Pydantic response model, a happy-path test against the included GL fixture, a failure-path test (missing file or unknown column), and an entry in the table above.

## License

MIT. See [LICENSE](LICENSE).
