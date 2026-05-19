"""Tool-layer tests — scaffolding first, then one tool per section."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from conftest import write_synthetic_export

from netsuite_saved_search_mcp import parser as parser_mod
from netsuite_saved_search_mcp.errors import (
    ColumnNotFoundError,
    ExportNotFoundError,
    PathTraversalError,
)
from netsuite_saved_search_mcp.models import (
    ComparePredicate,
    ContainsPredicate,
    EqPredicate,
    Measure,
)
from netsuite_saved_search_mcp.tools import (
    DEFAULT_QUERY_LIMIT,
    UNCATEGORIZED,
    _resolve_directory,
    _resolve_file,
    aggregate_export,
    categorize_by_memo,
    clear_cache,
    detect_anomalies,
    get_headers,
    get_parse_warnings,
    list_exports,
    query_export,
)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    clear_cache()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_dir: Path) -> Path:
    """Use the real fixtures dir as NSMCP_ROOT for happy-path tests."""
    monkeypatch.setenv("NSMCP_ROOT", str(fixture_dir))
    return fixture_dir


# ---------------------------------------------------------------------------
# Path resolution + cache scaffolding.
# ---------------------------------------------------------------------------

def test_resolve_file_relative_under_root(root: Path) -> None:
    resolved = _resolve_file("sample_gl_export.xls")
    assert resolved == (root / "sample_gl_export.xls").resolve()


def test_resolve_file_absolute_under_root(root: Path) -> None:
    abs_path = str(root / "sample_gl_export.xls")
    resolved = _resolve_file(abs_path)
    assert resolved == Path(abs_path).resolve()


def test_resolve_file_traversal_rejected(root: Path) -> None:
    with pytest.raises(PathTraversalError):
        _resolve_file("../../../etc/passwd")


def test_resolve_file_absolute_escape_rejected(root: Path) -> None:
    with pytest.raises(PathTraversalError):
        _resolve_file("/etc/passwd")


def test_resolve_file_missing_lists_available(root: Path) -> None:
    with pytest.raises(ExportNotFoundError) as exc:
        _resolve_file("not_a_real_export.xls")
    msg = str(exc.value)
    assert "sample_gl_export.xls" in msg
    assert "sample_with_metadata.xls" in msg


def test_resolve_directory_happy(root: Path) -> None:
    assert _resolve_directory(".") == root


def test_resolve_directory_traversal_rejected(root: Path) -> None:
    with pytest.raises(PathTraversalError):
        _resolve_directory("../..")


def test_cache_avoids_reparsing(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = {"n": 0}
    real_init = parser_mod.NetSuiteExport.__init__

    def counting_init(self: parser_mod.NetSuiteExport, *args: Any, **kwargs: Any) -> None:
        counter["n"] += 1
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(parser_mod.NetSuiteExport, "__init__", counting_init)

    # Two `_get_export` calls on the same path should parse exactly once.
    from netsuite_saved_search_mcp.tools import _get_export
    path = _resolve_file("sample_gl_export.xls")
    _get_export(path)
    _get_export(path)
    assert counter["n"] == 1


def test_cache_reparses_when_mtime_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture_dir: Path,
) -> None:
    # Copy the GL fixture into a writable tmp_path so we can bump mtime.
    target = tmp_path / "sample_gl_export.xls"
    target.write_bytes((fixture_dir / "sample_gl_export.xls").read_bytes())
    monkeypatch.setenv("NSMCP_ROOT", str(tmp_path))

    counter = {"n": 0}
    real_init = parser_mod.NetSuiteExport.__init__

    def counting_init(self: parser_mod.NetSuiteExport, *args: Any, **kwargs: Any) -> None:
        counter["n"] += 1
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(parser_mod.NetSuiteExport, "__init__", counting_init)

    from netsuite_saved_search_mcp.tools import _get_export
    path = _resolve_file("sample_gl_export.xls")
    _get_export(path)
    # Bump mtime explicitly — touching the file would only change atime on
    # some filesystems.
    import os
    new_mtime = target.stat().st_mtime + 60
    os.utime(target, (new_mtime, new_mtime))
    _get_export(path)
    assert counter["n"] == 2


# ===========================================================================
# Tool 1 — list_exports.
# ===========================================================================




def test_list_exports_returns_all_three_fixtures(root: Path) -> None:
    summaries = list_exports(".")
    by_name = {s.filename: s for s in summaries}
    assert set(by_name) == {
        "sample_gl_export.xls",
        "sample_with_metadata.xls",
        "sample_malformed.xls",
    }


def test_list_exports_summary_fields(root: Path) -> None:
    summaries = list_exports(".")
    gl = next(s for s in summaries if s.filename == "sample_gl_export.xls")
    assert gl.row_count == 212
    assert gl.header_count == 11
    assert gl.header_row == 0
    assert gl.warning_count == 0
    assert gl.date_range is not None
    assert gl.date_range.start == date(2024, 1, 1)
    assert gl.date_range.end.year == 2024


def test_list_exports_metadata_fixture_summary(root: Path) -> None:
    summaries = list_exports(".")
    md = next(s for s in summaries if s.filename == "sample_with_metadata.xls")
    assert md.header_row == 14
    assert md.row_count == 50
    assert md.date_range is not None


def test_list_exports_malformed_fixture_records_warnings(root: Path) -> None:
    summaries = list_exports(".")
    bad = next(s for s in summaries if s.filename == "sample_malformed.xls")
    assert bad.parse_error is None  # recoverable
    assert bad.row_count == 20
    assert bad.warning_count == 3


def test_list_exports_directory_not_found(root: Path) -> None:
    with pytest.raises(ExportNotFoundError):
        list_exports("not_a_real_dir")


def test_list_exports_traversal_rejected(root: Path) -> None:
    with pytest.raises(PathTraversalError):
        list_exports("../..")


def test_list_exports_valid_files_have_no_parse_error(root: Path) -> None:
    # Sanity: parse_error stays None for files that lxml can recover.
    summaries = list_exports(".")
    for s in summaries:
        assert s.parse_error is None, f"unexpected parse_error on {s.filename}"


def test_list_exports_unparseable_file_records_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Drop a truly unparseable file (raw bytes, no XML prologue) into an
    # isolated working root and confirm ExportSummary.parse_error surfaces.
    bad = tmp_path / "sample_unparseable.xls"
    bad.write_bytes(b"\x00\x01\x02 not xml at all")
    monkeypatch.setenv("NSMCP_ROOT", str(tmp_path))

    summaries = list_exports(".")
    entry = next(s for s in summaries if s.filename == "sample_unparseable.xls")
    assert entry.parse_error is not None
    assert entry.row_count is None
    assert entry.header_count is None
    assert entry.header_row is None
    assert entry.warning_count is None
    assert entry.date_range is None


# ===========================================================================
# Tool 2 — get_headers.
# ===========================================================================



def test_get_headers_gl_fixture(root: Path) -> None:
    resp = get_headers("sample_gl_export.xls")
    assert resp.header_row == 0
    assert len(resp.headers) == 11
    assert resp.headers[0] == "Order Type"
    assert resp.column_letters["Order Type"] == "A"
    assert resp.column_letters["Account"] == "H"
    assert resp.column_letters["Created By"] == "K"


def test_get_headers_metadata_fixture_header_row_14(root: Path) -> None:
    resp = get_headers("sample_with_metadata.xls")
    assert resp.header_row == 14
    assert resp.headers == ["Opportunity ID", "Account Name", "Close Date",
                            "Amount", "Stage", "Owner"]
    assert resp.column_letters["Opportunity ID"] == "A"
    assert resp.column_letters["Owner"] == "F"


def test_get_headers_file_not_found(root: Path) -> None:
    with pytest.raises(ExportNotFoundError):
        get_headers("nope.xls")


def test_get_headers_traversal_rejected(root: Path) -> None:
    with pytest.raises(PathTraversalError):
        get_headers("../../../etc/passwd")


# ===========================================================================
# Tool 3 — query_export.
# ===========================================================================


def test_query_export_no_filters_returns_all_rows(root: Path) -> None:
    resp = query_export("sample_gl_export.xls")
    assert resp.total_matched == 212
    assert len(resp.rows) == 212
    assert resp.truncated is False


def test_query_export_with_filters(root: Path) -> None:
    resp = query_export(
        "sample_gl_export.xls",
        filters=[EqPredicate(op="eq", column="Order Type", value="Invoice")],
    )
    assert resp.total_matched > 0
    assert resp.total_matched < 212
    assert all(r["Order Type"] == "Invoice" for r in resp.rows)
    assert resp.truncated is False


def test_query_export_column_projection(root: Path) -> None:
    resp = query_export(
        "sample_gl_export.xls",
        filters=[ContainsPredicate(op="contains", column="Memo (main)", value="Reclass")],
        columns=["Document Number", "Account", "Amount"],
    )
    assert resp.rows
    for r in resp.rows:
        assert set(r.keys()) == {"Document Number", "Account", "Amount"}


def test_query_export_explicit_limit_truncates(root: Path) -> None:
    resp = query_export("sample_gl_export.xls", limit=5)
    assert resp.total_matched == 212
    assert len(resp.rows) == 5
    assert resp.truncated is True


def test_query_export_default_limit_kicks_in_at_1001_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = tmp_path / "big.xls"
    write_synthetic_export(
        p,
        headers=["Order Type", "Account", "Amount"],
        rows=[
            [("String", "Invoice"), ("String", "1010"), ("Number", str(i))]
            for i in range(1001)
        ],
    )
    monkeypatch.setenv("NSMCP_ROOT", str(tmp_path))
    resp = query_export("big.xls")
    assert resp.total_matched == 1001
    assert len(resp.rows) == DEFAULT_QUERY_LIMIT
    assert resp.truncated is True


def test_query_export_limit_zero_returns_count_only(root: Path) -> None:
    resp = query_export("sample_gl_export.xls", limit=0)
    assert resp.total_matched == 212
    assert resp.rows == []
    assert resp.truncated is True


def test_query_export_unknown_filter_column_raises(root: Path) -> None:
    with pytest.raises(ColumnNotFoundError) as exc:
        query_export(
            "sample_gl_export.xls",
            filters=[ComparePredicate(op="gt", column="Acount", value=0)],
        )
    assert "Did you mean 'Account'" in str(exc.value)


def test_query_export_unknown_projection_column_raises(root: Path) -> None:
    with pytest.raises(ColumnNotFoundError) as exc:
        query_export("sample_gl_export.xls", columns=["Acount"])
    assert "projection column" in str(exc.value)
    assert "Did you mean 'Account'" in str(exc.value)


def test_query_export_file_not_found(root: Path) -> None:
    with pytest.raises(ExportNotFoundError):
        query_export("nope.xls")


# ===========================================================================
# Tool 4 — aggregate_export.
# ===========================================================================



def test_aggregate_export_sum_by_account(root: Path) -> None:
    resp = aggregate_export(
        "sample_gl_export.xls",
        group_by=["Account"],
        measures=[Measure(column="Amount", op="sum", alias="total")],
    )
    accounts = {g["Account"] for g in resp.groups}
    assert accounts == {"1010", "1200", "1500", "2010", "4000", "4100"}
    for g in resp.groups:
        assert "total" in g


def test_aggregate_export_multi_measure(root: Path) -> None:
    resp = aggregate_export(
        "sample_gl_export.xls",
        group_by=["Order Type"],
        measures=[
            Measure(column="Amount", op="count"),
            Measure(column="Amount", op="avg"),
        ],
    )
    assert resp.groups
    for g in resp.groups:
        assert g["count_Amount"] > 0
        assert isinstance(g["avg_Amount"], float)


def test_aggregate_export_unknown_group_by_raises(root: Path) -> None:
    with pytest.raises(ColumnNotFoundError):
        aggregate_export(
            "sample_gl_export.xls",
            group_by=["Acount"],
            measures=[Measure(column="Amount", op="sum")],
        )


def test_aggregate_export_file_not_found(root: Path) -> None:
    with pytest.raises(ExportNotFoundError):
        aggregate_export(
            "nope.xls",
            group_by=["Account"],
            measures=[Measure(column="Amount", op="sum")],
        )


# ===========================================================================
# Tool 5 — categorize_by_memo.
# ===========================================================================

GL_MEMO_RULES: dict[str, list[str]] = {
    "NC Reclass": ["NC Reclass", "reclass"],
    "Amortization": ["amortization"],
    "Correction": ["correction"],
    "Accrual": ["accrual"],
}


def test_categorize_by_memo_all_four_categories_present(root: Path) -> None:
    resp = categorize_by_memo(
        "sample_gl_export.xls",
        memo_columns=["Memo (main)", "Memo (line)"],
        rules=GL_MEMO_RULES,
    )
    for cat in ("NC Reclass", "Amortization", "Correction", "Accrual"):
        assert resp.breakdown[cat] > 0, f"category {cat!r} had zero matches"
    assert sum(resp.breakdown.values()) == 212


def test_categorize_by_memo_attaches_category_column(root: Path) -> None:
    resp = categorize_by_memo(
        "sample_gl_export.xls",
        memo_columns=["Memo (main)"],
        rules=GL_MEMO_RULES,
    )
    assert all("_category" in r for r in resp.rows)
    assert all(
        r["_category"] in {*GL_MEMO_RULES.keys(), UNCATEGORIZED}
        for r in resp.rows
    )


def test_categorize_by_memo_first_match_wins(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "fmw.xls"
    write_synthetic_export(
        p,
        headers=["Order Type", "Memo", "Amount"],
        rows=[
            [("String", "Invoice"), ("String", "Reclass and amortization"),
             ("Number", "1")],
        ],
    )
    monkeypatch.setenv("NSMCP_ROOT", str(tmp_path))
    resp = categorize_by_memo(
        "fmw.xls",
        memo_columns=["Memo"],
        rules={"NC Reclass": ["reclass"], "Amortization": ["amortization"]},
    )
    assert resp.rows[0]["_category"] == "NC Reclass"


def test_categorize_by_memo_uncategorized_bucket(root: Path) -> None:
    resp = categorize_by_memo(
        "sample_gl_export.xls",
        memo_columns=["Memo (main)"],
        rules={"NC Reclass": ["xyz_will_not_match"]},  # nothing matches
    )
    assert resp.breakdown["NC Reclass"] == 0
    assert resp.breakdown[UNCATEGORIZED] == 212


def test_categorize_by_memo_unknown_memo_column_raises(root: Path) -> None:
    with pytest.raises(ColumnNotFoundError):
        categorize_by_memo(
            "sample_gl_export.xls",
            memo_columns=["Memmo (main)"],
            rules=GL_MEMO_RULES,
        )


def test_categorize_by_memo_file_not_found(root: Path) -> None:
    with pytest.raises(ExportNotFoundError):
        categorize_by_memo("nope.xls", memo_columns=["Memo"], rules={})


# ===========================================================================
# Tool 6 — detect_anomalies.
# ===========================================================================



def test_detect_anomalies_flags_june_zero_activity(root: Path) -> None:
    resp = detect_anomalies(
        "sample_gl_export.xls",
        account_column="Account",
        amount_column="Amount",
        period_column="Period",
    )
    zero = [f for f in resp.findings if f.category == "zero_activity_period"]
    assert zero, "expected at least one zero_activity_period finding"
    assert any("Jun 2024" in f.description for f in zero)
    assert all(f.severity == "HIGH" for f in zero)


def test_detect_anomalies_flags_september_ratio_for_account_1200(root: Path) -> None:
    resp = detect_anomalies(
        "sample_gl_export.xls",
        account_column="Account",
        amount_column="Amount",
        period_column="Period",
    )
    ratio = [f for f in resp.findings if f.category == "ratio_anomaly"]
    # The fixture seeds an AR (1200) anomaly in Sep 2024.
    matched = [
        f for f in ratio
        if "1200" in f.description and "Sep 2024" in f.description
    ]
    assert matched, f"expected 1200/Sep anomaly, got: {[f.description for f in ratio]}"
    assert matched[0].supporting_rows  # at least one row attached
    assert len(matched[0].supporting_rows) <= 10
    # total_supporting_count must reflect the true count, not the cap.
    assert matched[0].total_supporting_count >= len(matched[0].supporting_rows)
    assert matched[0].total_supporting_count > 10  # we seeded 12+ rows


def test_detect_anomalies_flags_september_document_count(root: Path) -> None:
    resp = detect_anomalies(
        "sample_gl_export.xls",
        account_column="Account",
        amount_column="Amount",
        period_column="Period",
    )
    var = [f for f in resp.findings if f.category == "document_count_variance"]
    # The 12 ratio-anomaly rows piled into September also push doc count well
    # above mean, so this should fire too.
    assert var
    assert any("Sep 2024" in f.description for f in var)


def test_detect_anomalies_clean_export_has_no_findings(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "clean.xls"
    write_synthetic_export(
        p,
        headers=["Account", "Amount", "Period"],
        rows=[
            [("String", "1010"), ("Number", "1000"), ("String", "Jan 2024")],
            [("String", "1010"), ("Number", "1000"), ("String", "Feb 2024")],
            [("String", "1010"), ("Number", "1000"), ("String", "Mar 2024")],
            [("String", "1010"), ("Number", "1000"), ("String", "Apr 2024")],
        ],
    )
    monkeypatch.setenv("NSMCP_ROOT", str(tmp_path))
    resp = detect_anomalies(
        "clean.xls",
        account_column="Account",
        amount_column="Amount",
        period_column="Period",
    )
    assert resp.findings == []


def test_detect_anomalies_unknown_column_raises(root: Path) -> None:
    with pytest.raises(ColumnNotFoundError):
        detect_anomalies(
            "sample_gl_export.xls",
            account_column="Acount",
            amount_column="Amount",
            period_column="Period",
        )


def test_detect_anomalies_file_not_found(root: Path) -> None:
    with pytest.raises(ExportNotFoundError):
        detect_anomalies(
            "nope.xls",
            account_column="Account",
            amount_column="Amount",
            period_column="Period",
        )


# ===========================================================================
# Tool 7 — get_parse_warnings.
# ===========================================================================



def test_get_parse_warnings_clean_fixture(root: Path) -> None:
    assert get_parse_warnings("sample_gl_export.xls") == []


def test_get_parse_warnings_malformed_fixture(root: Path) -> None:
    warnings = get_parse_warnings("sample_malformed.xls")
    kinds = sorted(w.kind for w in warnings)
    assert kinds == ["bad_datetime", "encoding_recovery", "phantom_column"]


def test_get_parse_warnings_file_not_found(root: Path) -> None:
    with pytest.raises(ExportNotFoundError):
        get_parse_warnings("nope.xls")


def test_get_parse_warnings_isolates_by_file_path(root: Path) -> None:
    # Two files in the same session must return independent warning lists,
    # not whichever was touched most recently.
    clean = get_parse_warnings("sample_gl_export.xls")
    dirty = get_parse_warnings("sample_malformed.xls")
    assert clean == []
    assert sorted(w.kind for w in dirty) == [
        "bad_datetime", "encoding_recovery", "phantom_column",
    ]
    # Round-trip — querying the clean file again after the dirty one must
    # still produce its (empty) list, not leak the dirty file's warnings.
    assert get_parse_warnings("sample_gl_export.xls") == []


def test_get_parse_warnings_parses_on_demand_if_uncached(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fresh cache, no prior parse: get_parse_warnings must trigger one.
    clear_cache()
    counter = {"n": 0}
    real_init = parser_mod.NetSuiteExport.__init__

    def counting_init(self: parser_mod.NetSuiteExport, *args: Any, **kwargs: Any) -> None:
        counter["n"] += 1
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(parser_mod.NetSuiteExport, "__init__", counting_init)

    warnings = get_parse_warnings("sample_malformed.xls")
    assert counter["n"] == 1
    assert len(warnings) == 3
