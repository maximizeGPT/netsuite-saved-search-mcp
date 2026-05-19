"""Anomaly checks against a parsed NetSuite export.

Three checks are run and rolled up into AnomalyResponse:

  zero_activity_period      — month-year gaps inside the observed period
                              range. HIGH severity.
  ratio_anomaly             — (account, period) total > 2x the median
                              total for that account across periods.
                              MEDIUM severity.
  document_count_variance   — period whose row count is >2 stdev from
                              the mean across periods. MEDIUM severity.

Each Finding carries up to MAX_SUPPORTING_ROWS_PER_FINDING supporting
rows plus a total_supporting_count so the caller knows how many were
truncated.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .parser import NetSuiteExport

__all__ = [
    "MAX_SUPPORTING_ROWS_PER_FINDING",
    "AnomalyResponse",
    "Finding",
    "detect_anomalies",
]


MAX_SUPPORTING_ROWS_PER_FINDING = 10


# ---------------------------------------------------------------------------
# Response shape.
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    category: str
    description: str
    supporting_rows: list[dict[str, Any]] = Field(default_factory=list)
    total_supporting_count: int = 0


class AnomalyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[Finding]


# ---------------------------------------------------------------------------
# Period parsing — accepts the three formats NetSuite emits in practice.
# ---------------------------------------------------------------------------

_PERIOD_FORMATS: tuple[str, ...] = ("%b %Y", "%B %Y", "%Y-%m")


def _parse_period(value: object) -> tuple[int, int] | None:
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


# ---------------------------------------------------------------------------
# Individual checks.
# ---------------------------------------------------------------------------

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
            matching = [
                r
                for r in export.rows
                if str(r[account_column]) == acc and str(r[period_column]) == per
            ]
            findings.append(
                Finding(
                    severity="MEDIUM",
                    category="ratio_anomaly",
                    description=(
                        f"Account {acc} in {per} totals {total:,.2f} — "
                        f"{total / median:.1f}x the {acc} median of {median:,.2f}"
                    ),
                    supporting_rows=matching[:MAX_SUPPORTING_ROWS_PER_FINDING],
                    total_supporting_count=len(matching),
                )
            )
    return findings


def _check_document_count_variance(
    export: NetSuiteExport, period_column: str
) -> list[Finding]:
    period_rows: dict[str, list[dict[str, Any]]] = {}
    for row in export.rows:
        per = row[period_column]
        if per is None:
            continue
        period_rows.setdefault(str(per), []).append(row)
    if len(period_rows) < 3:
        return []

    counts = {p: len(rows) for p, rows in period_rows.items()}
    values = list(counts.values())
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return []

    findings: list[Finding] = []
    for per, count in counts.items():
        z = (count - mean) / stdev
        if abs(z) <= 2:
            continue
        rows = period_rows[per]
        findings.append(
            Finding(
                severity="MEDIUM",
                category="document_count_variance",
                description=(
                    f"Period {per} has {count} documents "
                    f"({z:+.1f} stdev from mean of {mean:.1f})"
                ),
                supporting_rows=rows[:MAX_SUPPORTING_ROWS_PER_FINDING],
                total_supporting_count=count,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Public entrypoint.
# ---------------------------------------------------------------------------

def detect_anomalies(
    file_path: str,
    account_column: str,
    amount_column: str,
    period_column: str,
) -> AnomalyResponse:
    """Run the three anomaly checks against the parsed export."""
    # Lazy import to avoid a circular dependency between tools.py and
    # this module: tools.py re-exports detect_anomalies at module level.
    from .tools import _get_export, _resolve_file, _validate_projection

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
