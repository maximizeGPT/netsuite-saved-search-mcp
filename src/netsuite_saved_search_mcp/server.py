"""Stdio MCP server entrypoint for netsuite-saved-search-mcp.

stdout is reserved for the MCP transport. Every log line, every traceback,
every status update must go to stderr — anything that touches stdout
will corrupt the JSON-RPC frame and the client will drop the connection.
"""

from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("netsuite_saved_search_mcp")


SERVER_INSTRUCTIONS = """\
Expose NetSuite saved-search exports (.xls files that are actually XML
SpreadsheetML) as a structured query interface.

Set the NSMCP_ROOT environment variable to the directory holding your
exports before launching. All tools resolve file paths relative to that
root and reject any path that escapes it.

Optional environment:
  NSMCP_LOG_LEVEL  one of DEBUG / INFO / WARNING / ERROR (default INFO)
"""


def _configure_logging() -> None:
    """Send every log record to stderr. stdout is the MCP transport."""
    level_name = os.environ.get("NSMCP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def _build_server() -> FastMCP:
    server = FastMCP(
        name="netsuite-saved-search-mcp",
        instructions=SERVER_INSTRUCTIONS,
    )
    _register_tools(server)
    return server


# Tool descriptions are agent-facing — they're how Claude decides which
# tool to call. Each one names the inputs, the output shape, and one
# hint about WHEN to pick this tool vs an alternative.

_TOOL_DESCRIPTIONS: dict[str, str] = {
    "list_exports": (
        "List every NetSuite saved-search export (.xls) in a directory under "
        "NSMCP_ROOT. Returns one ExportSummary per file with row count, header "
        "count, 0-indexed header_row, warning count, and detected date range. "
        "Files that can't be parsed even with lxml recovery come back with "
        "parse_error populated and the other fields None. Call this first "
        "when you don't already know which exports are available."
    ),
    "get_headers": (
        "Return the column headers of a NetSuite saved-search export plus their "
        "spreadsheet column letters (A, B, ..., AA, AB) and the 0-indexed "
        "header_row. Call this before query_export or aggregate_export when you "
        "don't already know the column names — every other tool takes column "
        "names verbatim and errors on typos with a difflib suggestion."
    ),
    "query_export": (
        "Filter rows from a NetSuite export by a list of predicates (AND-"
        "combined; empty list returns everything). Predicate ops: eq/ne, "
        "gt/gte/lt/lte, contains/not_contains (case-insensitive by default), "
        "regex, date_range (ISO 8601 start/end, inclusive by default). "
        "Optionally project to a subset of columns via the `columns` argument. "
        "Implicit limit=1000; pass limit=0 to get total_matched without "
        "fetching any rows. Returns rows, total_matched, and a truncated flag."
    ),
    "aggregate_export": (
        "Group rows from a NetSuite export by one or more columns and compute "
        "aggregations per group. Each Measure carries column, op "
        "(sum/count/avg/min/max), and optional alias for the output key "
        "(defaults to {op}_{column}). Groups are returned in first-seen order. "
        "Use this instead of query_export when you want summary statistics "
        "rather than raw rows."
    ),
    "categorize_by_memo": (
        "Tag every row with a derived `_category` based on case-insensitive "
        "substring matches across one or more memo columns. NetSuite GL "
        "exports usually carry both 'Memo (main)' and 'Memo (line)'; pass both "
        "so the keyword sweep covers all the prose. `rules` maps category "
        "name to a list of keywords; the first rule whose keyword appears in "
        "any memo wins; rows matching nothing fall into 'Uncategorized'. "
        "Returns the tagged rows plus a per-category count breakdown."
    ),
    "detect_anomalies": (
        "Run three anomaly checks against a NetSuite GL-style export and "
        "return Findings: (1) zero_activity_period — month gaps inside the "
        "observed period range (HIGH); (2) ratio_anomaly — (account, period) "
        "total greater than 2x the account's median total across periods "
        "(MEDIUM); (3) document_count_variance — period row count more than "
        "2 stdev from the mean across periods (MEDIUM). Each Finding includes "
        "severity, description, up to 10 supporting_rows, and "
        "total_supporting_count for the true un-truncated count. The period "
        "column should contain labels like 'Jan 2024', 'January 2024', or "
        "'2024-01'."
    ),
    "get_parse_warnings": (
        "Return the parse warnings captured during the most recent parse of a "
        "NetSuite export. Warning kinds: phantom_column (cell at a column "
        "index beyond the header count), bad_datetime (DateTime cell that "
        "wouldn't parse — raw string is preserved in the row), "
        "encoding_recovery (lxml had to recover from invalid XML), "
        "empty_row_skipped. Use this after any other tool reports a non-zero "
        "warning_count to see exactly which rows are affected."
    ),
}


def _register_tools(server: FastMCP) -> None:
    """Attach the 7 tool implementations to the FastMCP server.

    Tools live in tools.py and anomalies.py as plain functions — no MCP
    imports leak into those modules. Descriptions stay co-located here
    so refining the agent-facing copy doesn't require touching the
    implementation.
    """
    from .anomalies import detect_anomalies
    from .tools import (
        aggregate_export,
        categorize_by_memo,
        get_headers,
        get_parse_warnings,
        list_exports,
        query_export,
    )

    registry: list[tuple[str, object]] = [
        ("list_exports", list_exports),
        ("get_headers", get_headers),
        ("query_export", query_export),
        ("aggregate_export", aggregate_export),
        ("categorize_by_memo", categorize_by_memo),
        ("detect_anomalies", detect_anomalies),
        ("get_parse_warnings", get_parse_warnings),
    ]
    for name, fn in registry:
        server.tool(description=_TOOL_DESCRIPTIONS[name])(fn)  # type: ignore[arg-type]


# Module-level instance so `from .server import mcp` works for callers
# that want to introspect or register additional tools. Constructed
# eagerly on import — FastMCP construction is side-effect-free.
mcp: FastMCP = _build_server()


def main() -> None:
    """CLI entrypoint declared in pyproject.toml."""
    _configure_logging()
    root_dir = os.environ.get("NSMCP_ROOT", os.getcwd())
    logger.info(
        "netsuite-saved-search-mcp starting (transport=stdio, NSMCP_ROOT=%s)",
        root_dir,
    )
    try:
        mcp.run(transport="stdio")
    except KeyboardInterrupt:
        # FastMCP's anyio backend surfaces SIGINT as KeyboardInterrupt;
        # treat it as a clean shutdown rather than an error.
        logger.info("received SIGINT; shutting down cleanly")
    except Exception:
        logger.exception("server terminated with an unhandled exception")
        raise


if __name__ == "__main__":
    main()
