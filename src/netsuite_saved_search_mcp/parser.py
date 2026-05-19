"""Parse NetSuite saved-search XML SpreadsheetML exports into typed rows."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from dateutil import parser as _dateparse
from lxml import etree
from pydantic import BaseModel, Field

# SpreadsheetML namespace — declared identically by NetSuite for both the
# default namespace and the `ss` prefix.
SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"
_NS = f"{{{SS_NS}}}"

WarningKind = Literal[
    "phantom_column",
    "bad_datetime",
    "encoding_recovery",
    "empty_row_skipped",
]


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """A single recoverable issue surfaced during parsing.

    `row` is 0-indexed against the data section (post-header). It is None
    when the warning has been collapsed into a summary across many rows.
    """

    row: int | None
    kind: WarningKind
    message: str


# ---------------------------------------------------------------------------
# Default ID-column policy.
# ---------------------------------------------------------------------------

DEFAULT_ID_COLUMN_EXACT: frozenset[str] = frozenset({
    "Account",
    "Document Number",
    "Opportunity ID",
    "Customer ID",
    "Stripe ID",
})

_ID_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r".+ ID$"),
    re.compile(r".+ Number$"),
    re.compile(r".+_ID$"),
)


def resolve_id_columns(
    headers: list[str], overrides: list[str] | None
) -> frozenset[str]:
    """Build the effective id-column set for an export.

    If overrides is supplied, it replaces the default policy entirely.
    Otherwise: union of DEFAULT_ID_COLUMN_EXACT (intersected with headers
    so unused names don't matter) and any header matching the suffix
    patterns.
    """
    if overrides is not None:
        return frozenset(overrides)
    found: set[str] = {h for h in headers if h in DEFAULT_ID_COLUMN_EXACT}
    for h in headers:
        if any(p.fullmatch(h) for p in _ID_SUFFIX_PATTERNS):
            found.add(h)
    return frozenset(found)


# ---------------------------------------------------------------------------
# Helper 1 — entity decoding.
# ---------------------------------------------------------------------------

_ENTITY_PAIRS: tuple[tuple[str, str], ...] = (
    ("&apos;", "'"),
    ("&quot;", '"'),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&amp;", "&"),  # must come last so we don't double-decode `&amp;lt;`
)


def _decode_entities(s: str) -> str:
    """Decode the five XML entities NetSuite emits inside cell values.

    lxml already decodes these during normal parsing — this exists for
    defense in depth: cells reached via the recovery path, or values
    that arrive as raw text from a non-XML source, still come out clean.
    """
    for needle, repl in _ENTITY_PAIRS:
        s = s.replace(needle, repl)
    return s


# ---------------------------------------------------------------------------
# Helper 2 — typed cell coercion.
# ---------------------------------------------------------------------------

CellValue = str | int | float | date | bool | None
CoerceResult = tuple[CellValue, WarningKind | None]


def _coerce_cell(
    value: str,
    ss_type: str,
    header: str,
    id_columns: frozenset[str],
) -> CoerceResult:
    """Coerce a raw cell text into a typed Python value.

    Returns (value, warning_kind). warning_kind is non-None only for
    semantically broken cells (currently: ss:Type='DateTime' that won't
    parse). The cell is never dropped; on failure we return the raw
    string so callers can still see what was there.
    """
    decoded = _decode_entities(value)

    # Empty cell → None regardless of type.
    if decoded == "":
        return None, None

    # ID columns always render as string, even if ss:Type was Number.
    # Normalize "1200.0" → "1200" so equality comparisons with user-typed
    # strings work without surprises.
    if header in id_columns:
        if ss_type == "Number":
            try:
                f = float(decoded)
                if f.is_integer():
                    return str(int(f)), None
                return str(f), None
            except ValueError:
                return decoded, None
        return decoded, None

    if ss_type == "String":
        return decoded, None

    if ss_type == "Number":
        try:
            f = float(decoded)
        except ValueError:
            return decoded, None
        if f.is_integer() and "." not in decoded and "e" not in decoded.lower():
            return int(f), None
        return f, None

    if ss_type == "DateTime":
        try:
            dt = _dateparse.isoparse(decoded)
        except (ValueError, TypeError):
            return decoded, "bad_datetime"
        return dt.date(), None

    if ss_type == "Boolean":
        lc = decoded.lower()
        if lc in ("1", "true"):
            return True, None
        if lc in ("0", "false"):
            return False, None
        return decoded, None

    # Unknown ss:Type — fall through as string.
    return decoded, None


# ---------------------------------------------------------------------------
# Helper 3 — dynamic header-row detection.
# ---------------------------------------------------------------------------

# Internal row representation used by the detector. Each cell carries its
# resolved 1-indexed column position, ss:Type, and raw string value.
@dataclass(frozen=True, slots=True)
class _RawCell:
    column: int
    ss_type: str
    value: str


_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9 ()_/-]*$")


def _row_is_header_candidate(row: list[_RawCell]) -> bool:
    non_empty = [c for c in row if c.value != ""]
    if len(non_empty) < 3:
        return False
    if any(c.ss_type != "String" for c in non_empty):
        return False
    if any(":" in c.value for c in non_empty):
        return False
    matches = sum(1 for c in non_empty if _HEADER_NAME_PATTERN.fullmatch(c.value))
    return matches / len(non_empty) >= 0.8


def _detect_header_row(rows: list[list[_RawCell]]) -> int:
    """Locate the column-header row by content shape.

    Walks the rows and returns the bottom index of the first run of
    consecutive header-candidate rows — that's the row directly above
    the data block.

    Raises ParseError when no row qualifies.
    """
    in_run = False
    run_end = -1
    for i, row in enumerate(rows):
        if _row_is_header_candidate(row):
            in_run = True
            run_end = i
        elif in_run:
            break
    if run_end < 0:
        raise ParseError("could not locate a header row in export")
    return run_end


class ParseError(ValueError):
    """Raised when a saved-search export cannot be interpreted at all."""


# ---------------------------------------------------------------------------
# Low-level XML walk — pulls Worksheet/Table/Row/Cell out of the parsed tree
# and resolves each cell's 1-indexed column position (recovering from
# ss:Index attributes when the export omits empty cells).
# ---------------------------------------------------------------------------

def _extract_raw_rows(root: etree._Element) -> list[list[_RawCell]]:
    rows: list[list[_RawCell]] = []
    for worksheet in root.iterfind(f"{_NS}Worksheet"):
        for table in worksheet.iterfind(f"{_NS}Table"):
            for row_elem in table.iterfind(f"{_NS}Row"):
                rows.append(_extract_row(row_elem))
    return rows


def _extract_row(row_elem: etree._Element) -> list[_RawCell]:
    cells: list[_RawCell] = []
    next_col = 1
    for cell_elem in row_elem.iterfind(f"{_NS}Cell"):
        explicit = cell_elem.get(f"{_NS}Index")
        if explicit is not None:
            try:
                next_col = int(explicit)
            except ValueError:
                pass  # malformed ss:Index — fall back to running counter
        data_elem = cell_elem.find(f"{_NS}Data")
        if data_elem is None:
            next_col += 1
            continue
        ss_type = data_elem.get(f"{_NS}Type", "String")
        value = data_elem.text or ""
        cells.append(_RawCell(column=next_col, ss_type=ss_type, value=value))
        next_col += 1
    return cells


# ---------------------------------------------------------------------------
# Column-letter resolution (0 → A, 25 → Z, 26 → AA, ...).
# ---------------------------------------------------------------------------

def _column_index_to_letter(idx: int) -> str:
    if idx < 0:
        raise ValueError(f"column index must be >= 0, got {idx}")
    letters = ""
    n = idx
    while True:
        letters = chr(ord("A") + n % 26) + letters
        n = n // 26 - 1
        if n < 0:
            return letters


# ---------------------------------------------------------------------------
# NetSuiteExport — main entry point.
# ---------------------------------------------------------------------------

class NetSuiteExport:
    """A parsed NetSuite saved-search XML export.

    Construction parses the file end-to-end: lxml runs in recovery mode
    so malformed exports yield as much usable data as possible. All
    survivable issues land in `self.parse_warnings`; only truly
    unrecoverable failures raise `ParseError`.
    """

    def __init__(
        self,
        path: Path | str,
        id_columns: list[str] | None = None,
    ) -> None:
        self.path: Path = Path(path)
        self._id_columns_override = id_columns
        self.parse_warnings: list[ParseWarning] = []

        root = self._parse_xml()
        raw_rows = _extract_raw_rows(root)
        if not raw_rows:
            raise ParseError(f"{self.path}: no rows found in worksheet")

        self.header_row: int = _detect_header_row(raw_rows)
        self.headers: list[str] = self._build_headers(raw_rows[self.header_row])
        self._id_columns: frozenset[str] = resolve_id_columns(
            self.headers, self._id_columns_override
        )
        self._header_index: dict[str, int] = {h: i for i, h in enumerate(self.headers)}

        self.rows: list[dict[str, CellValue]] = []
        self._process_data_rows(raw_rows[self.header_row + 1 :])

        self._summary: str = (
            f"NetSuiteExport(file={self.path.name!r}, "
            f"headers={len(self.headers)}, "
            f"rows={len(self.rows)}, "
            f"header_row={self.header_row}, "
            f"warnings={len(self.parse_warnings)})"
        )

    # -- parsing pipeline -------------------------------------------------

    def _parse_xml(self) -> etree._Element:
        parser = etree.XMLParser(recover=True, huge_tree=False)
        try:
            tree = etree.parse(str(self.path), parser)
        except etree.XMLSyntaxError as e:  # pragma: no cover - recover=True swallows most
            raise ParseError(f"{self.path}: {e}") from e

        # lxml logs every recovery event into parser.error_log. Collapse to
        # one warning per affected line so a single bad character doesn't
        # explode the warning list.
        seen_lines: set[int] = set()
        for entry in parser.error_log:
            if entry.line in seen_lines:
                continue
            seen_lines.add(entry.line)
            self.parse_warnings.append(
                ParseWarning(
                    row=None,
                    kind="encoding_recovery",
                    message=f"line {entry.line}: {entry.message}",
                )
            )

        root = tree.getroot()
        if root is None:
            raise ParseError(f"{self.path}: empty document")
        return root

    def _build_headers(self, header_row: list[_RawCell]) -> list[str]:
        ordered = sorted(header_row, key=lambda c: c.column)
        return [_decode_entities(c.value).strip() for c in ordered if c.value != ""]

    def _process_data_rows(self, data_rows: list[list[_RawCell]]) -> None:
        phantom_occurrences: dict[int, list[tuple[int, str]]] = {}
        ncols = len(self.headers)

        for data_idx, raw_row in enumerate(data_rows):
            if not raw_row or all(c.value == "" for c in raw_row):
                self.parse_warnings.append(
                    ParseWarning(
                        row=data_idx,
                        kind="empty_row_skipped",
                        message="row skipped: no non-empty cells",
                    )
                )
                continue

            row_dict: dict[str, CellValue] = {h: None for h in self.headers}
            for cell in raw_row:
                if cell.column > ncols:
                    phantom_occurrences.setdefault(cell.column, []).append(
                        (data_idx, cell.value)
                    )
                    continue
                header = self.headers[cell.column - 1]
                value, warn_kind = _coerce_cell(
                    cell.value, cell.ss_type, header, self._id_columns
                )
                row_dict[header] = value
                if warn_kind == "bad_datetime":
                    self.parse_warnings.append(
                        ParseWarning(
                            row=data_idx,
                            kind="bad_datetime",
                            message=(
                                f"column {header!r} value {cell.value!r} not parseable "
                                "as DateTime; raw string preserved"
                            ),
                        )
                    )
            self.rows.append(row_dict)

        self._emit_phantom_warnings(phantom_occurrences, ncols)

    def _emit_phantom_warnings(
        self,
        phantom_occurrences: dict[int, list[tuple[int, str]]],
        ncols: int,
    ) -> None:
        for col_idx, occ in phantom_occurrences.items():
            if len(occ) > 5:
                self.parse_warnings.append(
                    ParseWarning(
                        row=None,
                        kind="phantom_column",
                        message=(
                            f"Cell at column {col_idx} has no matching header; "
                            f"discarded on {len(occ)} rows"
                        ),
                    )
                )
            else:
                for row_idx, value in occ:
                    self.parse_warnings.append(
                        ParseWarning(
                            row=row_idx,
                            kind="phantom_column",
                            message=(
                                f"Cell at column {col_idx} has no matching header "
                                f"(export has {ncols} columns); cell value "
                                f"{value!r} discarded"
                            ),
                        )
                    )

    # -- public API -------------------------------------------------------

    def column_letter(self, name: str) -> str:
        """Spreadsheet-style column letter (A, B, ..., AA) for `name`."""
        idx = self._header_index.get(name)
        if idx is None:
            raise KeyError(
                f"column {name!r} not in headers: {self.headers}"
            )
        return _column_index_to_letter(idx)

    def summary(self) -> str:
        return self._summary

    def __repr__(self) -> str:
        return self._summary

    # -- query API --------------------------------------------------------

    def filter(self, predicates: list[Predicate]) -> list[dict[str, CellValue]]:
        """Return rows for which every predicate evaluates True.

        Predicates are AND-combined; an empty list returns all rows.
        Raises ColumnNotFoundError with a difflib suggestion when a
        predicate references a column that doesn't exist.
        """
        self._validate_predicate_columns(predicates)
        return [row for row in self.rows if all(_eval_predicate(p, row) for p in predicates)]

    def aggregate(
        self,
        group_by: list[str],
        measures: list[Measure],
    ) -> list[dict[str, Any]]:
        """Group rows by `group_by` and compute each measure per group.

        Output rows carry the group-by columns plus one key per measure,
        named by `measure.alias` when set or `f"{function}_{column}"`
        otherwise. Groups are returned in first-seen order.
        """
        self._validate_columns(group_by, role="group_by")
        self._validate_columns([m.column for m in measures], role="measure")

        groups: dict[tuple[CellValue, ...], list[dict[str, CellValue]]] = {}
        order: list[tuple[CellValue, ...]] = []
        for row in self.rows:
            key = tuple(row[c] for c in group_by)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)

        out: list[dict[str, Any]] = []
        for key in order:
            bucket = groups[key]
            result: dict[str, Any] = dict(zip(group_by, key, strict=True))
            for m in measures:
                alias = m.alias or f"{m.function}_{m.column}"
                result[alias] = _apply_measure(m.function, m.column, bucket)
            out.append(result)
        return out

    # -- internal column validation --------------------------------------

    def _validate_predicate_columns(self, predicates: Iterable[Predicate]) -> None:
        for i, p in enumerate(predicates):
            if p.column not in self._header_index:
                raise ColumnNotFoundError(self._column_not_found_message(p.column, prefix=f"predicate at index {i}: "))

    def _validate_columns(self, columns: Iterable[str], *, role: str) -> None:
        for col in columns:
            if col not in self._header_index:
                raise ColumnNotFoundError(self._column_not_found_message(col, prefix=f"{role} "))

    def _column_not_found_message(self, name: str, *, prefix: str) -> str:
        close = difflib.get_close_matches(name, self.headers, n=1)
        suggestion = f" Did you mean {close[0]!r}?" if close else ""
        return (
            f"{prefix}column {name!r} not found in export.{suggestion} "
            f"Available columns: {', '.join(self.headers)}"
        )


# ---------------------------------------------------------------------------
# Predicate / measure data models.
# ---------------------------------------------------------------------------

class _PredicateBase(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}


class EqPredicate(_PredicateBase):
    op: Literal["eq", "ne"]
    column: str
    value: str | int | float | bool


class ComparePredicate(_PredicateBase):
    op: Literal["gt", "gte", "lt", "lte"]
    column: str
    value: int | float | str  # str carries ISO dates


class ContainsPredicate(_PredicateBase):
    op: Literal["contains", "not_contains"]
    column: str
    value: str
    case_sensitive: bool = False


class RegexPredicate(_PredicateBase):
    op: Literal["regex"]
    column: str
    pattern: str


class DateRangePredicate(_PredicateBase):
    op: Literal["date_range"]
    column: str
    start: str  # ISO 8601
    end: str
    inclusive: bool = True


Predicate = Annotated[
    EqPredicate
    | ComparePredicate
    | ContainsPredicate
    | RegexPredicate
    | DateRangePredicate,
    Field(discriminator="op"),
]


class Measure(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}
    column: str
    function: Literal["sum", "count", "avg", "min", "max"]
    alias: str | None = None


class ColumnNotFoundError(KeyError):
    """Raised by filter/aggregate when a column reference can't be resolved."""

    def __str__(self) -> str:  # KeyError wraps messages in quotes by default
        return self.args[0] if self.args else ""


# ---------------------------------------------------------------------------
# Predicate evaluation against row dicts.
# ---------------------------------------------------------------------------

def _eval_predicate(p: Predicate, row: dict[str, CellValue]) -> bool:
    value = row[p.column]

    if isinstance(p, EqPredicate):
        if p.op == "eq":
            return value == p.value
        return value != p.value

    if isinstance(p, ComparePredicate):
        if value is None or isinstance(value, bool):
            return False
        return _compare(value, p.op, p.value)

    if isinstance(p, ContainsPredicate):
        haystack = "" if value is None else str(value)
        needle = p.value
        if not p.case_sensitive:
            haystack = haystack.lower()
            needle = needle.lower()
        found = needle in haystack
        return found if p.op == "contains" else not found

    if isinstance(p, RegexPredicate):
        haystack = "" if value is None else str(value)
        try:
            return re.search(p.pattern, haystack) is not None
        except re.error:
            return False

    if isinstance(p, DateRangePredicate):
        if not isinstance(value, date):
            return False
        try:
            start = date.fromisoformat(p.start)
            end = date.fromisoformat(p.end)
        except ValueError:
            return False
        return start <= value <= end if p.inclusive else start < value < end

    # Exhaustive — pydantic guarantees one of the above.
    return False  # pragma: no cover


def _compare(value: CellValue, op: str, threshold: int | float | str) -> bool:
    resolved: int | float | str | date = threshold
    if isinstance(value, date) and isinstance(threshold, str):
        try:
            resolved = date.fromisoformat(threshold)
        except ValueError:
            return False
    try:
        if op == "gt":
            return value > resolved  # type: ignore[operator]
        if op == "gte":
            return value >= resolved  # type: ignore[operator]
        if op == "lt":
            return value < resolved  # type: ignore[operator]
        if op == "lte":
            return value <= resolved  # type: ignore[operator]
    except TypeError:
        return False
    return False  # pragma: no cover


# ---------------------------------------------------------------------------
# Measure application.
# ---------------------------------------------------------------------------

def _numeric_values(rows: list[dict[str, CellValue]], column: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r[column]
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out.append(float(v))
    return out


def _comparable_values(rows: list[dict[str, CellValue]], column: str) -> list[CellValue]:
    return [r[column] for r in rows if r[column] is not None and not isinstance(r[column], bool)]


def _apply_measure(
    fn: Literal["sum", "count", "avg", "min", "max"],
    column: str,
    rows: list[dict[str, CellValue]],
) -> Any:
    if fn == "count":
        return sum(1 for r in rows if r[column] is not None)
    if fn == "sum":
        return sum(_numeric_values(rows, column))
    if fn == "avg":
        nums = _numeric_values(rows, column)
        return sum(nums) / len(nums) if nums else None
    if fn == "min":
        vals = _comparable_values(rows, column)
        return min(vals) if vals else None  # type: ignore[type-var]
    if fn == "max":
        vals = _comparable_values(rows, column)
        return max(vals) if vals else None  # type: ignore[type-var]
    return None  # pragma: no cover
