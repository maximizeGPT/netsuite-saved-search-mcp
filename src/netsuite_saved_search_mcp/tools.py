"""MCP tool layer wrapping NetSuiteExport.

Each public function is a single MCP tool; private helpers handle path
resolution and the parsed-export cache. Parsed `NetSuiteExport`
instances are cached per (path, mtime) so repeated tool calls on the
same file skip lxml parsing entirely. `clear_cache()` resets it for
tests.
"""

from __future__ import annotations

import difflib
import os
from datetime import date
from pathlib import Path

from .anomalies import detect_anomalies
from .errors import ColumnNotFoundError, ExportNotFoundError, ParseError, PathTraversalError
from .models import (
    AggregateResponse,
    CategorizeResponse,
    DateRange,
    ExportSummary,
    HeadersResponse,
    Measure,
    Predicate,
    QueryResponse,
)
from .parser import NetSuiteExport, ParseWarning

__all__ = [
    "DEFAULT_QUERY_LIMIT",
    "UNCATEGORIZED",
    "aggregate_export",
    "categorize_by_memo",
    "clear_cache",
    "detect_anomalies",
    "get_headers",
    "get_parse_warnings",
    "list_exports",
    "query_export",
]

UNCATEGORIZED = "Uncategorized"

DEFAULT_QUERY_LIMIT = 1000

# ---------------------------------------------------------------------------
# Working-root + path resolution.
# ---------------------------------------------------------------------------

_ROOT_ENV = "NSMCP_ROOT"


def _get_root() -> Path:
    return Path(os.environ.get(_ROOT_ENV, os.getcwd())).resolve()


def _resolve_under_root(supplied: str, root: Path) -> Path:
    """Resolve `supplied` relative to root, rejecting paths that escape it."""
    p = Path(supplied)
    if not p.is_absolute():
        p = root / p
    resolved = p.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as e:
        raise PathTraversalError(
            f"path {supplied!r} resolves to {resolved} which is outside the working root {root}"
        ) from e
    return resolved


def _resolve_file(file_path: str) -> Path:
    root = _get_root()
    resolved = _resolve_under_root(file_path, root)
    if not resolved.exists() or not resolved.is_file():
        available = sorted(f.name for f in root.glob("*.xls"))
        raise ExportNotFoundError(
            f"File not found: {file_path}. Available files in {root}: {available}"
        )
    return resolved


def _resolve_directory(directory: str) -> Path:
    root = _get_root()
    resolved = _resolve_under_root(directory, root)
    if not resolved.exists() or not resolved.is_dir():
        raise ExportNotFoundError(f"Directory not found: {directory}")
    return resolved


# ---------------------------------------------------------------------------
# Export cache.
# ---------------------------------------------------------------------------

_EXPORT_CACHE: dict[Path, tuple[float, NetSuiteExport]] = {}


def _get_export(path: Path) -> NetSuiteExport:
    mtime = path.stat().st_mtime
    cached = _EXPORT_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    export = NetSuiteExport(path)
    _EXPORT_CACHE[path] = (mtime, export)
    return export


def clear_cache() -> None:
    _EXPORT_CACHE.clear()


# ---------------------------------------------------------------------------
# Date-range detection helper used by list_exports.
# ---------------------------------------------------------------------------

def _detect_date_range(export: NetSuiteExport) -> DateRange | None:
    """Find the first column where ≥50% of values are dates; return its span.

    The .5 threshold tolerates bad_datetime cells (raw strings) without
    losing the range signal.
    """
    if not export.rows:
        return None
    threshold = max(1, len(export.rows) // 2)
    for header in export.headers:
        dates: list[date] = []
        for r in export.rows:
            v = r[header]
            if isinstance(v, date):
                dates.append(v)
        if len(dates) >= threshold:
            return DateRange(start=min(dates), end=max(dates))
    return None


# ---------------------------------------------------------------------------
# Tool 1 — list_exports.
# ---------------------------------------------------------------------------

def list_exports(directory: str) -> list[ExportSummary]:
    """Scan `directory` for .xls files and return one ExportSummary each.

    Files that fail to parse get an ExportSummary with `parse_error` set
    and other fields None, so the caller can see what's broken without
    losing visibility of the rest of the directory.
    """
    dir_path = _resolve_directory(directory)
    out: list[ExportSummary] = []
    for f in sorted(dir_path.glob("*.xls")):
        try:
            export = _get_export(f)
        except ParseError as e:
            out.append(ExportSummary(filename=f.name, parse_error=str(e)))
            continue
        out.append(
            ExportSummary(
                filename=f.name,
                row_count=len(export.rows),
                header_count=len(export.headers),
                header_row=export.header_row,
                warning_count=len(export.parse_warnings),
                date_range=_detect_date_range(export),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Tool 2 — get_headers.
# ---------------------------------------------------------------------------

def get_headers(file_path: str) -> HeadersResponse:
    """Return the export's headers plus their spreadsheet column letters."""
    path = _resolve_file(file_path)
    export = _get_export(path)
    return HeadersResponse(
        headers=export.headers,
        column_letters={h: export.column_letter(h) for h in export.headers},
        header_row=export.header_row,
    )


# ---------------------------------------------------------------------------
# Column-projection helper shared by query/categorize.
# ---------------------------------------------------------------------------

def _validate_projection(columns: list[str], headers: list[str]) -> None:
    for col in columns:
        if col not in headers:
            close = difflib.get_close_matches(col, headers, n=1)
            suggestion = f" Did you mean {close[0]!r}?" if close else ""
            raise ColumnNotFoundError(
                f"projection column {col!r} not found in export.{suggestion} "
                f"Available columns: {', '.join(headers)}"
            )


# ---------------------------------------------------------------------------
# Tool 3 — query_export.
# ---------------------------------------------------------------------------

def query_export(
    file_path: str,
    filters: list[Predicate] | None = None,
    columns: list[str] | None = None,
    limit: int | None = None,
) -> QueryResponse:
    """Filter rows, optionally project columns, cap the result size.

    The implicit limit is DEFAULT_QUERY_LIMIT (1000). Pass limit=0 to
    get a count without fetching rows; response.rows will be empty,
    total_matched and truncated will reflect the full match.
    """
    path = _resolve_file(file_path)
    export = _get_export(path)

    matched = export.filter(filters or [])
    total = len(matched)

    effective_limit = DEFAULT_QUERY_LIMIT if limit is None else max(0, limit)
    truncated = total > effective_limit
    rows = matched[:effective_limit]

    if columns is not None:
        _validate_projection(columns, export.headers)
        rows = [{c: r[c] for c in columns} for r in rows]

    return QueryResponse(rows=rows, total_matched=total, truncated=truncated)


# ---------------------------------------------------------------------------
# Tool 4 — aggregate_export.
# ---------------------------------------------------------------------------

def aggregate_export(
    file_path: str,
    group_by: list[str],
    measures: list[Measure],
) -> AggregateResponse:
    """Group rows and compute one value per measure per group.

    Parser raises ColumnNotFoundError on unknown group_by or measure
    columns; we let that propagate so the agent sees the difflib
    suggestion.
    """
    path = _resolve_file(file_path)
    export = _get_export(path)
    groups = export.aggregate(group_by=group_by, measures=measures)
    return AggregateResponse(groups=groups)


# ---------------------------------------------------------------------------
# Tool 5 — categorize_by_memo.
# ---------------------------------------------------------------------------

def categorize_by_memo(
    file_path: str,
    memo_columns: list[str],
    rules: dict[str, list[str]],
) -> CategorizeResponse:
    """Tag each row with a `_category` derived from keyword matches in memos.

    `memo_columns` lets the caller scan more than one memo field (NS
    exports often have both "Memo (main)" and "Memo (line)"). Keywords
    are matched case-insensitively as substrings. First rule whose
    keyword appears wins; rows matching nothing get UNCATEGORIZED.
    """
    path = _resolve_file(file_path)
    export = _get_export(path)
    _validate_projection(memo_columns, export.headers)

    compiled = {cat: [k.lower() for k in kws] for cat, kws in rules.items()}

    out_rows: list[dict[str, object]] = []
    breakdown: dict[str, int] = {cat: 0 for cat in rules}
    breakdown[UNCATEGORIZED] = 0

    for row in export.rows:
        memo_text = " ".join(
            str(row[c]) for c in memo_columns if row[c] is not None
        ).lower()

        category = UNCATEGORIZED
        for cat, keywords in compiled.items():
            if any(k in memo_text for k in keywords):
                category = cat
                break

        new_row: dict[str, object] = dict(row)
        new_row["_category"] = category
        out_rows.append(new_row)
        breakdown[category] += 1

    return CategorizeResponse(rows=out_rows, breakdown=breakdown)


# ---------------------------------------------------------------------------
# Tool 6 — detect_anomalies (implementation lives in anomalies.py; the
# re-export at the top of this file is the public tool entrypoint).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tool 7 — get_parse_warnings.
# ---------------------------------------------------------------------------

def get_parse_warnings(file_path: str) -> list[ParseWarning]:
    """Return the parse warnings captured from the most recent parse."""
    path = _resolve_file(file_path)
    export = _get_export(path)
    return list(export.parse_warnings)
