# netsuite-saved-search-mcp

MCP server that exposes NetSuite saved search .xls exports as a structured query interface to Claude.

NetSuite saved search exports look like Excel files but are actually XML SpreadsheetML with a .xls extension. Column layouts drift between saved searches. Every team using Claude with NetSuite ends up rebuilding the same parser. This server solves it once.

**Status:** v0.1 in progress. Parser complete (59 tests passing). MCP tool wrappers and stdio server next.

## Components

- `NetSuiteExport` parser class — XML SpreadsheetML parsing with entity decoding, dynamic header detection, ID-column type coercion, `ss:Index` skip recovery, three-tier DateTime fallback, phantom-column handling
- Predicate-based filter API with Pydantic discriminated unions
- (Coming) MCP tool wrappers: `list_exports`, `get_headers`, `query_export`, `aggregate_export`, `categorize_by_memo`, `detect_anomalies`, `get_parse_warnings`
- (Coming) stdio MCP server entrypoint
- (Coming) Claude Desktop config example and end-to-end walkthrough

## Known limitations

- Header detection assumes at least one data column is typed (DateTime, Number, or Boolean). All-string exports with no typed columns may misidentify the header row. If you hit this in practice, file an issue — an explicit `header_row` override is the planned fix.

## License

MIT
