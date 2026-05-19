"""Pydantic response models returned by the MCP tool layer.

Kept narrow on purpose: each model describes exactly what a tool emits.
Predicate and Measure live in parser.py and are re-exported here so the
tool layer offers one import surface for the agent-facing schema.
Anomaly findings live in anomalies.py to keep that module self-contained.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from .parser import (
    ComparePredicate,
    ContainsPredicate,
    DateRangePredicate,
    EqPredicate,
    Measure,
    Predicate,
    RegexPredicate,
)

__all__ = [
    "AggregateResponse",
    "CategorizeResponse",
    "ComparePredicate",
    "ContainsPredicate",
    "DateRange",
    "DateRangePredicate",
    "EqPredicate",
    "ExportSummary",
    "HeadersResponse",
    "Measure",
    "Predicate",
    "QueryResponse",
    "RegexPredicate",
]


class _ResponseBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DateRange(_ResponseBase):
    start: date
    end: date


class ExportSummary(_ResponseBase):
    """One row of the `list_exports` response."""

    filename: str
    row_count: int | None = None
    header_count: int | None = None
    header_row: int | None = None
    warning_count: int | None = None
    date_range: DateRange | None = None
    parse_error: str | None = None


class HeadersResponse(_ResponseBase):
    headers: list[str]
    column_letters: dict[str, str]
    header_row: int


class QueryResponse(_ResponseBase):
    rows: list[dict[str, Any]]
    total_matched: int
    truncated: bool


class AggregateResponse(_ResponseBase):
    groups: list[dict[str, Any]]


class CategorizeResponse(_ResponseBase):
    rows: list[dict[str, Any]]
    breakdown: dict[str, int]
