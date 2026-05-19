# netsuite-saved-search-mcp

NetSuite saved search exports look like Excel files but are actually XML SpreadsheetML with a .xls extension — Excel opens them, pandas trips on them, and most teams writing Claude integrations against NetSuite end up rebuilding the same parser by hand. This MCP server exposes those exports as a structured query interface for Claude: discover files, inspect columns, filter and aggregate rows, categorize by memo keywords, surface anomalies. Seven tools wrap a parser that handles the format's real quirks — entity-encoded text, empty cells flagged with `ss:Index`, drifting column layouts between saved searches, DateTime cells that won't parse. The parser runs in lxml's recovery mode and reports broken cells as structured warnings rather than crashing on them.

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
| `get_parse_warnings` | Return parse warnings (phantom_column, bad_datetime, encoding_recovery, empty_row_skipped) captured during the most recent parse. | `file_path` |

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
