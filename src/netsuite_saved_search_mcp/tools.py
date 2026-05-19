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
import statistics
from datetime import date, datetime
from pathlib import Path

from .errors import ColumnNotFoundError, ExportNotFoundError, ParseError, PathTraversalError
from .models import (
    AggregateResponse,
    AnomalyResponse,
    CategorizeResponse,
    DateRange,
    ExportSummary,
    Finding,
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
    return only the total_matched count with no rows.
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
# Tool 6 — detect_anomalies.
# ---------------------------------------------------------------------------

# Cap supporting_rows to keep findings small enough for an MCP response.
_SUPPORTING_ROWS_CAP = 10

# Period strings emitted by NetSuite look like "Jan 2024", "Sep 2024".
_PERIOD_FORMATS: tuple[str, ...] = ("%b %Y", "%B %Y", "%Y-%m")


def _parse_period(value: object) -> tuple[int, int] | None:
    """Best-effort parse of a NetSuite period label into (year, month)."""
    if value is None:
        return None
    s = str(value)
    for fmt in _PERIOD_FORMATS:
        try:
            d = datetime.strptime(s, fmt)
        except ValueError:
            continue
        return d.year, d.month
    return None


def _check_zero_activity_periods(
    export: NetSuiteExport, period_column: str
) -> list[Finding]:
    parsed_periods: dict[tuple[int, int], str] = {}
    for row in export.rows:
        key = _parse_period(row[period_column])
        if key is not None:
            parsed_periods.setdefault(key, str(row[period_column]))
    if not parsed_periods:
        return []

    keys = sorted(parsed_periods.keys())
    (first_y, first_m), (last_y, last_m) = keys[0], keys[-1]

    findings: list[Finding] = []
    y, m = first_y, first_m
    while (y, m) <= (last_y, last_m):
        if (y, m) not in parsed_periods:
            label = datetime(y, m, 1).strftime("%b %Y")
            findings.append(
                Finding(
                    severity="HIGH",
                    category="zero_activity_period",
                    description=f"No rows recorded for {label}",
                )
            )
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return findings


def _check_ratio_anomalies(
    export: NetSuiteExport,
    account_column: str,
    amount_column: str,
    period_column: str,
) -> list[Finding]:
    totals: dict[tuple[str, str], float] = {}
    for row in export.rows:
        acc, per, amt = row[account_column], row[period_column], row[amount_column]
        if acc is None or per is None:
            continue
        if not isinstance(amt, (int, float)) or isinstance(amt, bool):
            continue
        key = (str(acc), str(per))
        totals[key] = totals.get(key, 0.0) + abs(float(amt))

    per_account: dict[str, dict[str, float]] = {}
    for (acc, per), total in totals.items():
        per_account.setdefault(acc, {})[per] = total

    findings: list[Finding] = []
    for acc, period_totals in per_account.items():
        if len(period_totals) < 3:
            continue
        values = sorted(period_totals.values())
        median = values[len(values) // 2]
        if median <= 0:
            continue
        for per, total in period_totals.items():
            if total <= 2 * median:
                continue
            supporting = [
                r
                for r in export.rows
                if str(r[account_column]) == acc and str(r[period_column]) == per
            ][:_SUPPORTING_ROWS_CAP]
            findings.append(
                Finding(
                    severity="MEDIUM",
                    category="ratio_anomaly",
                    description=(
                        f"Account {acc} in {per} totals {total:,.2f} — "
                        f"{total / median:.1f}x the {acc} median of {median:,.2f}"
                    ),
                    supporting_rows=supporting,
                )
            )
    return findings


def _check_document_count_variance(
    export: NetSuiteExport, period_column: str
) -> list[Finding]:
    period_counts: dict[str, int] = {}
    for row in export.rows:
        per = row[period_column]
        if per is None:
            continue
        period_counts[str(per)] = period_counts.get(str(per), 0) + 1
    if len(period_counts) < 3:
        return []

    values = list(period_counts.values())
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return []

    findings: list[Finding] = []
    for per, count in period_counts.items():
        z = (count - mean) / stdev
        if abs(z) <= 2:
            continue
        findings.append(
            Finding(
                severity="MEDIUM",
                category="document_count_variance",
                description=(
                    f"Period {per} has {count} documents "
                    f"({z:+.1f} stdev from mean of {mean:.1f})"
                ),
            )
        )
    return findings


def detect_anomalies(
    file_path: str,
    account_column: str,
    amount_column: str,
    period_column: str,
) -> AnomalyResponse:
    """Run three anomaly checks against the parsed export."""
    path = _resolve_file(file_path)
    export = _get_export(path)
    _validate_projection(
        [account_column, amount_column, period_column], export.headers
    )

    findings: list[Finding] = []
    findings.extend(_check_zero_activity_periods(export, period_column))
    findings.extend(
        _check_ratio_anomalies(export, account_column, amount_column, period_column)
    )
    findings.extend(_check_document_count_variance(export, period_column))
    return AnomalyResponse(findings=findings)


# ---------------------------------------------------------------------------
# Tool 7 — get_parse_warnings.
# ---------------------------------------------------------------------------

def get_parse_warnings(file_path: str) -> list[ParseWarning]:
    """Return the parse warnings captured from the most recent parse."""
    path = _resolve_file(file_path)
    export = _get_export(path)
    return list(export.parse_warnings)
