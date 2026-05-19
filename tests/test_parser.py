"""Parser test suite — helpers in Round 1, class behavior in subsequent rounds."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from conftest import write_synthetic_export
from pydantic import TypeAdapter

from netsuite_saved_search_mcp.parser import (
    DEFAULT_ID_COLUMN_EXACT,
    ColumnNotFoundError,
    ComparePredicate,
    ContainsPredicate,
    DateRangePredicate,
    EqPredicate,
    Measure,
    NetSuiteExport,
    ParseError,
    ParseWarning,
    Predicate,
    RegexPredicate,
    _coerce_cell,
    _decode_entities,
    _detect_header_row,
    _RawCell,
    resolve_id_columns,
)

# ---------------------------------------------------------------------------
# ParseWarning dataclass — Round 1, item 1.
# ---------------------------------------------------------------------------

def test_parse_warning_is_frozen() -> None:
    w = ParseWarning(row=3, kind="bad_datetime", message="x")
    with pytest.raises((AttributeError, TypeError)):
        w.row = 5  # type: ignore[misc]


def test_parse_warning_equality() -> None:
    a = ParseWarning(row=3, kind="bad_datetime", message="x")
    b = ParseWarning(row=3, kind="bad_datetime", message="x")
    assert a == b


# ---------------------------------------------------------------------------
# _decode_entities — Round 1, item 2.
# ---------------------------------------------------------------------------

def test_decode_entities_handles_all_five() -> None:
    assert _decode_entities("Smith &amp; Co") == "Smith & Co"
    assert _decode_entities("She said &quot;hi&quot;") == 'She said "hi"'
    assert _decode_entities("a &lt; b &gt; c") == "a < b > c"
    assert _decode_entities("John&apos;s") == "John's"


def test_decode_entities_does_not_double_decode() -> None:
    # &amp;lt; should decode to &lt;, not to <.
    assert _decode_entities("&amp;lt;") == "&lt;"


def test_decode_entities_passthrough() -> None:
    assert _decode_entities("plain text") == "plain text"
    assert _decode_entities("") == ""


# ---------------------------------------------------------------------------
# _coerce_cell — Round 1, item 3.
# ---------------------------------------------------------------------------

EMPTY: frozenset[str] = frozenset()
ACCOUNT_IS_ID: frozenset[str] = frozenset({"Account"})


def test_coerce_string_passthrough_and_decodes() -> None:
    assert _coerce_cell("hello", "String", "Memo", EMPTY) == ("hello", None)
    assert _coerce_cell("Smith &amp; Co", "String", "Name", EMPTY) == ("Smith & Co", None)


def test_coerce_number_integer() -> None:
    val, warn = _coerce_cell("1234", "Number", "Amount", EMPTY)
    assert val == 1234
    assert isinstance(val, int)
    assert warn is None


def test_coerce_number_float() -> None:
    val, warn = _coerce_cell("12.34", "Number", "Amount", EMPTY)
    assert val == pytest.approx(12.34)
    assert isinstance(val, float)
    assert warn is None


def test_coerce_number_with_trailing_zero_decimal_is_float() -> None:
    # "1200.0" has an explicit decimal point — preserve as float to keep
    # the input's typing intent.
    val, _ = _coerce_cell("1200.0", "Number", "Amount", EMPTY)
    assert val == pytest.approx(1200.0)
    assert isinstance(val, float)


def test_coerce_datetime_success_returns_date() -> None:
    val, warn = _coerce_cell("2024-01-15T00:00:00", "DateTime", "Date", EMPTY)
    assert val == date(2024, 1, 15)
    assert warn is None


def test_coerce_datetime_failure_returns_raw_and_flags() -> None:
    val, warn = _coerce_cell("not-a-date", "DateTime", "Date", EMPTY)
    assert val == "not-a-date"
    assert warn == "bad_datetime"


def test_coerce_boolean_values() -> None:
    assert _coerce_cell("1", "Boolean", "Flag", EMPTY) == (True, None)
    assert _coerce_cell("TRUE", "Boolean", "Flag", EMPTY) == (True, None)
    assert _coerce_cell("0", "Boolean", "Flag", EMPTY) == (False, None)
    assert _coerce_cell("false", "Boolean", "Flag", EMPTY) == (False, None)


def test_coerce_empty_string_is_none() -> None:
    assert _coerce_cell("", "String", "Memo", EMPTY) == (None, None)
    assert _coerce_cell("", "Number", "Amount", EMPTY) == (None, None)
    assert _coerce_cell("", "DateTime", "Date", EMPTY) == (None, None)


def test_coerce_id_column_forces_string_from_number_type() -> None:
    val, warn = _coerce_cell("1200", "Number", "Account", ACCOUNT_IS_ID)
    assert val == "1200"
    assert isinstance(val, str)
    assert warn is None


def test_coerce_id_column_normalizes_trailing_zero() -> None:
    val, _ = _coerce_cell("1200.0", "Number", "Account", ACCOUNT_IS_ID)
    assert val == "1200"


def test_coerce_id_column_non_integer_keeps_decimal() -> None:
    val, _ = _coerce_cell("12.5", "Number", "Account", ACCOUNT_IS_ID)
    assert val == "12.5"


def test_coerce_id_column_already_string_passes_through() -> None:
    val, _ = _coerce_cell("OPP-00042", "String", "Opportunity ID", frozenset({"Opportunity ID"}))
    assert val == "OPP-00042"


# ---------------------------------------------------------------------------
# resolve_id_columns — Round 1 helper for the default policy.
# ---------------------------------------------------------------------------

def test_resolve_id_columns_default_policy() -> None:
    headers = [
        "Order Type", "Date", "Document Number", "Account",
        "Opportunity ID", "Customer ID", "Stripe ID", "Some Other_ID", "Amount",
    ]
    ids = resolve_id_columns(headers, overrides=None)
    assert "Account" in ids
    assert "Document Number" in ids
    assert "Opportunity ID" in ids
    assert "Customer ID" in ids
    assert "Stripe ID" in ids
    assert "Some Other_ID" in ids
    assert "Amount" not in ids
    assert "Date" not in ids


def test_resolve_id_columns_overrides_replace_defaults() -> None:
    ids = resolve_id_columns(["Account", "Amount"], overrides=["Amount"])
    assert ids == frozenset({"Amount"})
    assert "Account" not in ids


def test_resolve_id_columns_only_includes_headers_present() -> None:
    # Default exact-match set lists "Stripe ID" but if the export has no
    # such column, we shouldn't claim it.
    ids = resolve_id_columns(["Order Type", "Amount"], overrides=None)
    assert "Stripe ID" not in ids
    # Sanity: the exact set still exists at module level.
    assert "Stripe ID" in DEFAULT_ID_COLUMN_EXACT


# ---------------------------------------------------------------------------
# _detect_header_row — Round 1, item 4.
# ---------------------------------------------------------------------------

def _string_row(*values: str) -> list[_RawCell]:
    return [_RawCell(column=i, ss_type="String", value=v) for i, v in enumerate(values, 1)]


def _mixed_row(*items: tuple[str, str]) -> list[_RawCell]:
    return [_RawCell(column=i, ss_type=t, value=v) for i, (t, v) in enumerate(items, 1)]


def test_detect_header_at_row_zero() -> None:
    rows = [
        _string_row("Order Type", "Date", "Account"),
        _mixed_row(("String", "Invoice"), ("DateTime", "2024-01-15T00:00:00"), ("String", "1010")),
    ]
    assert _detect_header_row(rows) == 0


def test_detect_header_after_metadata() -> None:
    metadata = [
        [_RawCell(1, "String", "Saved Search: Quarterly Pipeline")],
        [_RawCell(1, "String", "Generated: 2024-12-31 10:30:00")],
        [_RawCell(1, "String", "User: Alex Rivera")],
    ]
    header = _string_row("Opportunity ID", "Account Name", "Close Date", "Amount", "Stage", "Owner")
    data = _mixed_row(
        ("String", "OPP-00001"),
        ("String", "Acme"),
        ("DateTime", "2024-01-01T00:00:00"),
        ("Number", "10000"),
        ("String", "Closed Won"),
        ("String", "Alex"),
    )
    rows = [*metadata, header, data]
    assert _detect_header_row(rows) == 3


def test_detect_header_rejects_rows_with_colons() -> None:
    # "User: Alex Rivera" has multiple cells if you split on spaces, but
    # here we model it as one cell — still rejected on the < 3 cells check.
    # This test models the colon-rejection path explicitly.
    rows = [
        _string_row("Header: bad", "still: bad", "also: bad"),  # colons fail
        _string_row("Order Type", "Date", "Account"),
    ]
    assert _detect_header_row(rows) == 1


def test_detect_header_takes_last_of_consecutive_run() -> None:
    rows = [
        _string_row("a", "b", "c"),
        _string_row("d", "e", "f"),
        _mixed_row(("String", "x"), ("DateTime", "2024-01-01T00:00:00"), ("String", "y")),
    ]
    # Both row 0 and row 1 are header candidates; pick row 1.
    assert _detect_header_row(rows) == 1


def test_detect_header_requires_three_non_empty_cells() -> None:
    rows = [
        _string_row("Order", "Date"),  # only 2 cells
        _string_row("Order Type", "Date", "Account"),
    ]
    assert _detect_header_row(rows) == 1


def test_detect_header_rejects_typed_cells() -> None:
    rows = [
        _mixed_row(("String", "Order"), ("Number", "123"), ("String", "Account")),
        _string_row("Order Type", "Date", "Account"),
    ]
    assert _detect_header_row(rows) == 1


def test_detect_header_raises_when_none_found() -> None:
    rows = [
        _mixed_row(("String", "Generated: foo"), ("String", "bar")),
        _mixed_row(("Number", "1"), ("Number", "2"), ("Number", "3")),
    ]
    with pytest.raises(ParseError):
        _detect_header_row(rows)


# ===========================================================================
# Round 2 — NetSuiteExport class against the on-disk fixtures.
# ===========================================================================

GL_HEADERS_EXPECTED = [
    "Order Type", "Date", "Period", "Type", "Document Number",
    "Name", "Memo (main)", "Account", "Memo (line)", "Amount", "Created By",
]
OPP_HEADERS_EXPECTED = [
    "Opportunity ID", "Account Name", "Close Date", "Amount", "Stage", "Owner",
]


def test_parse_gl_export_smoke(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    assert export.header_row == 0
    assert export.headers == GL_HEADERS_EXPECTED
    assert len(export.rows) == 212  # 200 base rows + 12 ratio-anomaly rows
    assert export.parse_warnings == []


def test_parse_metadata_fixture(metadata_export_path: Path) -> None:
    export = NetSuiteExport(metadata_export_path)
    assert export.header_row == 14
    assert export.headers == OPP_HEADERS_EXPECTED
    assert len(export.rows) == 50
    assert export.parse_warnings == []


def test_parse_malformed_fixture(malformed_export_path: Path) -> None:
    export = NetSuiteExport(malformed_export_path)
    # All 20 data rows parsed despite the breakages.
    assert len(export.rows) == 20

    kinds = sorted(w.kind for w in export.parse_warnings)
    assert kinds == ["bad_datetime", "encoding_recovery", "phantom_column"], (
        f"expected exactly the three breakage kinds, got warnings: {export.parse_warnings}"
    )

    # bad_datetime row 7 (1-indexed in file) is data_idx=6 in 0-indexed.
    bad_dt = next(w for w in export.parse_warnings if w.kind == "bad_datetime")
    assert bad_dt.row == 6
    assert "not-a-date" in bad_dt.message

    # phantom on row 12 → data_idx=11; single occurrence so NOT collapsed.
    phantom = next(w for w in export.parse_warnings if w.kind == "phantom_column")
    assert phantom.row == 11
    assert "column 15" in phantom.message
    assert "PHANTOM-COL-15" in phantom.message


def test_malformed_preserves_raw_bad_datetime_value(malformed_export_path: Path) -> None:
    export = NetSuiteExport(malformed_export_path)
    # Row 7 (1-indexed in file) is the 6th data row.
    row = export.rows[6]
    assert row["Date"] == "not-a-date"


def test_ss_index_skip_recovery(tmp_path: Path) -> None:
    # Build a 3-row export where row 2 omits Memo (main) and the parser
    # must recover via ss:Index="8" on the Account cell.
    p = tmp_path / "skip.xls"
    write_synthetic_export(
        p,
        headers=GL_HEADERS_EXPECTED,
        rows=[
            [
                ("String", "Invoice"),
                ("DateTime", "2024-01-15T00:00:00"),
                ("String", "Jan 2024"),
                ("String", "Standard"),
                ("String", "INV-1001"),
                ("String", "Acme Corp"),
                None,  # Memo (main) omitted → next cell gets ss:Index="8"
                ("String", "1200"),
                ("String", "line detail"),
                ("Number", "5000"),
                ("String", "Alex Rivera"),
            ],
            [
                ("String", "Bill"),
                ("DateTime", "2024-02-01T00:00:00"),
                ("String", "Feb 2024"),
                ("String", "Standard"),
                ("String", "BILL-2002"),
                ("String", "PartnerCo Inc."),
                ("String", "Standard memo"),
                ("String", "2010"),
                ("String", "line detail"),
                ("Number", "1500"),
                ("String", "Jordan Kim"),
            ],
        ],
    )
    export = NetSuiteExport(p)
    assert export.rows[0]["Memo (main)"] is None
    assert export.rows[0]["Account"] == "1200"
    assert export.rows[0]["Amount"] == 5000  # number cell still landed in the right column
    assert export.rows[1]["Memo (main)"] == "Standard memo"


def test_id_column_string_coercion(tmp_path: Path) -> None:
    # Force ss:Type="Number" for an Account cell and confirm the value is
    # coerced to str by the default ID-column policy.
    p = tmp_path / "id_coerce.xls"
    write_synthetic_export(
        p,
        headers=["Order Type", "Account", "Amount"],
        rows=[
            [("String", "Invoice"), ("Number", "1200"), ("Number", "500")],
            [("String", "Bill"),    ("Number", "4100"), ("Number", "750")],
        ],
    )
    export = NetSuiteExport(p)
    assert export.rows[0]["Account"] == "1200"
    assert isinstance(export.rows[0]["Account"], str)
    assert export.rows[1]["Account"] == "4100"
    assert export.rows[0]["Amount"] == 500
    assert isinstance(export.rows[0]["Amount"], int)


def test_bad_datetime_keeps_row(tmp_path: Path) -> None:
    p = tmp_path / "bad_dt.xls"
    write_synthetic_export(
        p,
        headers=["Order Type", "Date", "Amount"],
        rows=[
            [("String", "Invoice"), ("DateTime", "not-a-date"), ("Number", "500")],
            [("String", "Bill"),    ("DateTime", "2024-02-01T00:00:00"), ("Number", "750")],
        ],
    )
    export = NetSuiteExport(p)
    assert len(export.rows) == 2
    assert export.rows[0]["Date"] == "not-a-date"
    assert export.rows[1]["Date"] == date(2024, 2, 1)
    warns = [w for w in export.parse_warnings if w.kind == "bad_datetime"]
    assert len(warns) == 1
    assert warns[0].row == 0


def test_phantom_column_collapse(tmp_path: Path) -> None:
    # 10 rows each carrying a phantom <Cell ss:Index="15"> → one collapsed
    # warning with row=None and the rolled-up count in the message.
    p = tmp_path / "phantom_many.xls"
    headers = ["Order Type", "Account", "Amount"]
    rows: list[list[tuple[str, str] | None]] = [
        [("String", "Invoice"), ("String", f"100{i}"), ("Number", str(500 + i))]
        for i in range(10)
    ]
    extras = {i: [(15, "String", f"ghost-{i}")] for i in range(10)}
    write_synthetic_export(p, headers=headers, rows=rows, extra_indexed_per_row=extras)

    export = NetSuiteExport(p)
    phantom_warnings = [w for w in export.parse_warnings if w.kind == "phantom_column"]
    assert len(phantom_warnings) == 1
    w = phantom_warnings[0]
    assert w.row is None
    assert "column 15" in w.message
    assert "10 rows" in w.message


def test_phantom_column_under_threshold_emits_individual(tmp_path: Path) -> None:
    # 5 phantom rows → still individual warnings (threshold is >5).
    p = tmp_path / "phantom_few.xls"
    headers = ["Order Type", "Account", "Amount"]
    rows: list[list[tuple[str, str] | None]] = [
        [("String", "Invoice"), ("String", f"100{i}"), ("Number", str(500 + i))]
        for i in range(5)
    ]
    extras = {i: [(15, "String", f"ghost-{i}")] for i in range(5)}
    write_synthetic_export(p, headers=headers, rows=rows, extra_indexed_per_row=extras)

    export = NetSuiteExport(p)
    phantom_warnings = [w for w in export.parse_warnings if w.kind == "phantom_column"]
    assert len(phantom_warnings) == 5
    assert all(w.row is not None for w in phantom_warnings)


def test_column_letter_resolution(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    assert export.column_letter("Order Type") == "A"
    assert export.column_letter("Account") == "H"
    assert export.column_letter("Created By") == "K"


def test_column_letter_unknown_raises(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    with pytest.raises(KeyError):
        export.column_letter("Nope")


def test_summary_and_repr(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    expected = (
        "NetSuiteExport(file='sample_gl_export.xls', headers=11, rows=212, "
        "header_row=0, warnings=0)"
    )
    assert export.summary() == expected
    assert repr(export) == expected


# ===========================================================================
# Round 3 — filter, aggregate, predicate model round-trip.
# ===========================================================================

def test_filter_eq(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    results = export.filter([EqPredicate(op="eq", column="Order Type", value="Invoice")])
    assert results, "expected at least one Invoice row in fixture"
    assert all(r["Order Type"] == "Invoice" for r in results)


def test_filter_ne(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    results = export.filter([EqPredicate(op="ne", column="Order Type", value="Invoice")])
    assert results
    assert all(r["Order Type"] != "Invoice" for r in results)


def test_filter_gt(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    results = export.filter([ComparePredicate(op="gt", column="Amount", value=10_000)])
    assert results
    assert all(isinstance(r["Amount"], (int, float)) and r["Amount"] > 10_000 for r in results)


def test_filter_gte_lte_chain(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    results = export.filter(
        [
            ComparePredicate(op="gte", column="Amount", value=1_000),
            ComparePredicate(op="lte", column="Amount", value=5_000),
        ]
    )
    assert results
    assert all(1_000 <= r["Amount"] <= 5_000 for r in results)  # type: ignore[operator]


def test_filter_contains_case_insensitive_default(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    results = export.filter(
        [ContainsPredicate(op="contains", column="Memo (main)", value="reclass")]
    )
    assert results
    assert all("reclass" in str(r["Memo (main)"]).lower() for r in results)


def test_filter_contains_case_sensitive(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    results = export.filter(
        [ContainsPredicate(op="contains", column="Memo (main)", value="reclass", case_sensitive=True)]
    )
    # Fixture writes "NC Reclass" with capital R, not lowercase.
    assert results == []


def test_filter_not_contains(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    results = export.filter(
        [ContainsPredicate(op="not_contains", column="Memo (main)", value="Amortization")]
    )
    assert results
    assert all("amortization" not in str(r["Memo (main)"]).lower() for r in results)


def test_filter_regex(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    results = export.filter(
        [RegexPredicate(op="regex", column="Document Number", pattern=r"^INV-\d{4}$")]
    )
    assert results
    import re
    assert all(re.fullmatch(r"INV-\d{4}", str(r["Document Number"])) for r in results)


def test_filter_date_range_inclusive(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    results = export.filter(
        [DateRangePredicate(op="date_range", column="Date", start="2024-01-01", end="2024-01-31")]
    )
    assert results
    assert all(isinstance(r["Date"], date) for r in results)
    assert all(date(2024, 1, 1) <= r["Date"] <= date(2024, 1, 31) for r in results)  # type: ignore[operator]


def test_filter_date_range_exclusive_drops_endpoints(tmp_path: Path) -> None:
    p = tmp_path / "edges.xls"
    write_synthetic_export(
        p,
        headers=["Order Type", "Date", "Amount"],
        rows=[
            [("String", "Invoice"), ("DateTime", "2024-01-01T00:00:00"), ("Number", "1")],
            [("String", "Invoice"), ("DateTime", "2024-01-15T00:00:00"), ("Number", "2")],
            [("String", "Invoice"), ("DateTime", "2024-01-31T00:00:00"), ("Number", "3")],
        ],
    )
    export = NetSuiteExport(p)
    inclusive = export.filter(
        [DateRangePredicate(op="date_range", column="Date",
                            start="2024-01-01", end="2024-01-31", inclusive=True)]
    )
    exclusive = export.filter(
        [DateRangePredicate(op="date_range", column="Date",
                            start="2024-01-01", end="2024-01-31", inclusive=False)]
    )
    assert len(inclusive) == 3
    assert len(exclusive) == 1
    assert exclusive[0]["Date"] == date(2024, 1, 15)


def test_filter_date_range_ignores_bad_datetime(malformed_export_path: Path) -> None:
    # Row 7 (data_idx 6) has Date='not-a-date'; date_range must skip it.
    export = NetSuiteExport(malformed_export_path)
    results = export.filter(
        [DateRangePredicate(op="date_range", column="Date", start="2020-01-01", end="2030-01-01")]
    )
    assert all(isinstance(r["Date"], date) for r in results)
    assert len(results) == 19  # 20 rows minus the bad-date row


def test_filter_empty_list_returns_all_rows(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    assert export.filter([]) == export.rows


def test_filter_unknown_column_raises_with_suggestion(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    with pytest.raises(ColumnNotFoundError) as exc:
        export.filter([EqPredicate(op="eq", column="Acount", value="1200")])
    msg = str(exc.value)
    assert "predicate at index 0" in msg
    assert "'Acount'" in msg
    assert "Did you mean 'Account'" in msg
    assert "Available columns" in msg


def test_filter_predicate_type_adapter_round_trip() -> None:
    # Confirms the discriminated union deserializes correctly from JSON-ish
    # dicts — this is the path tools.py will use to accept MCP arguments.
    adapter = TypeAdapter(list[Predicate])
    payload = [
        {"op": "eq", "column": "Account", "value": "1200"},
        {"op": "ne", "column": "Order Type", "value": "Credit Memo"},
        {"op": "gt", "column": "Amount", "value": 1000},
        {"op": "contains", "column": "Memo (main)", "value": "Reclass"},
        {"op": "regex", "column": "Document Number", "pattern": r"^INV-"},
        {"op": "date_range", "column": "Date", "start": "2024-01-01", "end": "2024-12-31"},
    ]
    parsed = adapter.validate_python(payload)
    assert isinstance(parsed[0], EqPredicate) and parsed[0].op == "eq"
    assert isinstance(parsed[1], EqPredicate) and parsed[1].op == "ne"
    assert isinstance(parsed[2], ComparePredicate)
    assert isinstance(parsed[3], ContainsPredicate)
    assert isinstance(parsed[4], RegexPredicate)
    assert isinstance(parsed[5], DateRangePredicate)


def test_aggregate_sum_by_account(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    groups = export.aggregate(
        group_by=["Account"],
        measures=[Measure(column="Amount", function="sum", alias="total")],
    )
    # 6 distinct accounts in the fixture.
    accounts = {g["Account"] for g in groups}
    assert accounts == {"1010", "1200", "1500", "2010", "4000", "4100"}

    # Conservation: sum of group totals == sum of every row's amount.
    row_total = sum(float(r["Amount"]) for r in export.rows if isinstance(r["Amount"], (int, float)))
    grouped_total = sum(g["total"] for g in groups)
    assert grouped_total == pytest.approx(row_total)


def test_aggregate_count_avg_min_max(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    groups = export.aggregate(
        group_by=["Order Type"],
        measures=[
            Measure(column="Amount", function="count"),
            Measure(column="Amount", function="avg"),
            Measure(column="Amount", function="min"),
            Measure(column="Amount", function="max"),
        ],
    )
    assert groups
    for g in groups:
        assert g["count_Amount"] > 0
        assert g["min_Amount"] <= g["avg_Amount"] <= g["max_Amount"]  # type: ignore[operator]


def test_aggregate_multi_column_group_by(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    groups = export.aggregate(
        group_by=["Order Type", "Account"],
        measures=[Measure(column="Amount", function="sum", alias="total")],
    )
    assert groups
    # Each group key (Order Type, Account) is unique.
    keys = {(g["Order Type"], g["Account"]) for g in groups}
    assert len(keys) == len(groups)


def test_aggregate_unknown_group_by_column_raises(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    with pytest.raises(ColumnNotFoundError) as exc:
        export.aggregate(
            group_by=["Acount"],
            measures=[Measure(column="Amount", function="sum")],
        )
    assert "group_by column" in str(exc.value)


def test_aggregate_unknown_measure_column_raises(gl_export_path: Path) -> None:
    export = NetSuiteExport(gl_export_path)
    with pytest.raises(ColumnNotFoundError):
        export.aggregate(
            group_by=["Account"],
            measures=[Measure(column="Amout", function="sum")],
        )


def test_empty_data_row_emits_warning(tmp_path: Path) -> None:
    p = tmp_path / "empty_row.xls"
    headers = ["Order Type", "Account", "Amount"]
    rows: list[list[tuple[str, str] | None]] = [
        [("String", "Invoice"), ("String", "1010"), ("Number", "500")],
        [],
        [("String", "Bill"),    ("String", "2010"), ("Number", "750")],
    ]
    write_synthetic_export(p, headers=headers, rows=rows)
    export = NetSuiteExport(p)
    assert len(export.rows) == 2
    skipped = [w for w in export.parse_warnings if w.kind == "empty_row_skipped"]
    assert len(skipped) == 1
    assert skipped[0].row == 1
