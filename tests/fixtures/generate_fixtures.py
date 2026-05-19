"""Generate XML SpreadsheetML fixture files for the test suite.

These mimic real NetSuite saved-search exports — same XML dialect, same
quirks (entity-encoded text, omitted empty cells, metadata rows above
the header, mixed ss:Type values). All data is synthesized; no real
company or customer information is present.

Run once to populate tests/fixtures/. Re-running is deterministic — the
RNG is seeded — so committed fixtures stay byte-stable.

Stdlib + lxml only. No other dependencies.
"""

from __future__ import annotations

import random
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from lxml import etree

# lxml's pretty_print splits <Cell>/<Data>/</Cell> across three lines, but real
# NS exports keep each cell on a single line. Collapse them after serialization.
_CELL_COLLAPSE = re.compile(
    r"<Cell([^>]*)>\s*<Data([^>]*)>([^<]*)</Data>\s*</Cell>"
)

# ---------------------------------------------------------------------------
# Parameters — retune fixtures by editing these.
# ---------------------------------------------------------------------------

SEED = 0xC0FFEE

# Fixture 1 — standard NS GL export
GL_TOTAL_ROWS = 200
GL_YEAR = 2024
GL_ZERO_MONTH = 6                     # June: zero rows; exercises zero-activity detector
GL_RATIO_ANOMALY_ACCOUNT = "1200"     # AR
GL_RATIO_ANOMALY_MONTH = 9            # extra AR activity in Sept → ratio anomaly
GL_RATIO_ANOMALY_EXTRA_ROWS = 12

# Fixture 2 — SFDC-style with metadata block
OPP_ROWS = 50
OPP_METADATA_ROWS = 14                # header lands at row 15

# Fixture 3 — malformed
MALFORMED_ROWS = 20

# ---------------------------------------------------------------------------
# Synthetic data pools.
# ---------------------------------------------------------------------------

ENTITIES = [
    "Acme Corp",
    "PartnerCo Inc.",
    "Global Solutions LLC",
    "Sunrise Ventures",
    "NorthStar Ltd.",
]

EMPLOYEES = ["Alex Rivera", "Jordan Kim", "Sam Patel"]

ACCOUNTS = [
    ("1010", "Cash"),
    ("1200", "Accounts Receivable"),
    ("1500", "Inventory"),
    ("2010", "Accounts Payable"),
    ("4000", "Revenue"),
    ("4100", "Service Revenue"),
]

# Memo templates exercising the four buckets categorize_by_memo will look for.
MEMO_TEMPLATES: dict[str, str] = {
    "NC Reclass":   "NC Reclass to {target}",
    "Amortization": "Monthly amortization of {target}",
    "Correction":   "Correction to {target} entry",
    "Accrual":      "Accrual for {target}",
    "Standard":     "Invoice from {target}",
}

MEMO_TARGETS = [
    "operating expenses",
    "prepaid insurance",
    "Q2 entry",
    "unbilled services",
    "consulting fees",
    "subscription revenue",
]

STAGES = [
    "Prospecting",
    "Qualification",
    "Proposal",
    "Negotiation",
    "Closed Won",
    "Closed Lost",
]

ORDER_TYPES = ["Invoice", "Bill", "Journal", "Credit Memo"]
DOC_PREFIXES = {"Invoice": "INV", "Bill": "BILL", "Journal": "JE", "Credit Memo": "CM"}

# SpreadsheetML namespace. Real NetSuite exports declare the urn for both the
# default namespace and the `ss` prefix and point them at the same URI.
SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"
NSMAP = {None: SS_NS, "ss": SS_NS}
SS = f"{{{SS_NS}}}"


# ---------------------------------------------------------------------------
# Low-level cell / row builders.
# ---------------------------------------------------------------------------

def cell(value: Any, type_: str, index: int | None = None) -> etree._Element:
    c = etree.Element(f"{SS}Cell")
    if index is not None:
        c.set(f"{SS}Index", str(index))
    d = etree.SubElement(c, f"{SS}Data")
    d.set(f"{SS}Type", type_)
    d.text = str(value)
    return c


def text_row(values: list[Any], types: list[str]) -> etree._Element:
    """Build a <Row>, omitting None cells and using ss:Index to bridge gaps.

    Real NS exports leave empty cells out entirely instead of writing
    <Cell/>, then mark the next non-empty cell with ss:Index. The parser
    has to recover column position from that attribute, so the fixtures
    must exhibit the same shape.
    """
    row = etree.Element(f"{SS}Row")
    pending_gap = False
    for i, (v, t) in enumerate(zip(values, types, strict=True), start=1):
        if v is None:
            pending_gap = True
            continue
        if pending_gap:
            row.append(cell(v, t, index=i))
            pending_gap = False
        else:
            row.append(cell(v, t))
    return row


def workbook_root() -> etree._Element:
    return etree.Element(f"{SS}Workbook", nsmap=NSMAP)


def write_workbook(wb: etree._Element, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = etree.tostring(
        wb,
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    ).decode("utf-8")
    compact = _CELL_COLLAPSE.sub(r"<Cell\1><Data\2>\3</Data></Cell>", raw)
    path.write_text(compact, encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixture 1 — sample_gl_export.xls (standard NS GL layout).
# ---------------------------------------------------------------------------

GL_HEADERS = [
    "Order Type", "Date", "Period", "Type", "Document Number",
    "Name", "Memo (main)", "Account", "Memo (line)", "Amount", "Created By",
]
GL_TYPES = [
    "String", "DateTime", "String", "String", "String",
    "String", "String", "String", "String", "Number", "String",
]


def _period(d: date) -> str:
    return d.strftime("%b %Y")


def _gl_amount(rng: random.Random, account: str) -> float:
    base = rng.uniform(500, 50_000)
    # Liability accounts skew negative; everything else has occasional reversals.
    if account.startswith("2") or rng.random() < 0.2:
        base = -base
    return round(base, 2)


def _gl_memo(rng: random.Random) -> tuple[str, str]:
    flavor = rng.choice(list(MEMO_TEMPLATES.keys()))
    target = rng.choice(MEMO_TARGETS)
    main = MEMO_TEMPLATES[flavor].format(target=target)
    line = f"{flavor}: line detail"
    return main, line


def build_gl_export() -> etree._Element:
    rng = random.Random(SEED)
    wb = workbook_root()
    ws = etree.SubElement(wb, f"{SS}Worksheet")
    ws.set(f"{SS}Name", "Sheet1")
    table = etree.SubElement(ws, f"{SS}Table")

    # Header at row 1 — no metadata above.
    table.append(text_row(GL_HEADERS, ["String"] * len(GL_HEADERS)))

    months = [m for m in range(1, 13) if m != GL_ZERO_MONTH]
    per_month: dict[int, int] = {m: GL_TOTAL_ROWS // len(months) for m in months}
    for i in range(GL_TOTAL_ROWS % len(months)):
        per_month[months[i]] += 1

    for month in months:
        for _ in range(per_month[month]):
            account_num, _ = rng.choice(ACCOUNTS)
            order_type = rng.choice(ORDER_TYPES)
            day = rng.randint(1, 28)
            d = date(GL_YEAR, month, day)
            iso_date = d.strftime("%Y-%m-%dT00:00:00")
            doc_num = f"{DOC_PREFIXES[order_type]}-{rng.randint(1000, 9999)}"
            name = rng.choice(ENTITIES)
            main_memo, line_memo = _gl_memo(rng)
            amount = _gl_amount(rng, account_num)
            employee = rng.choice(EMPLOYEES)

            # ~8% of rows omit the main memo, forcing the parser to use
            # ss:Index on the Account cell instead of sequential indexing.
            main_memo_val: str | None = None if rng.random() < 0.08 else main_memo

            row_values: list[Any] = [
                order_type, iso_date, _period(d), "Standard", doc_num,
                name, main_memo_val, account_num, line_memo, amount, employee,
            ]
            table.append(text_row(row_values, GL_TYPES))

    # Ratio anomaly: pile extra AR activity into one month so detect_anomalies
    # sees that account-period as ~2-3x the average for the same account.
    for _ in range(GL_RATIO_ANOMALY_EXTRA_ROWS):
        day = rng.randint(1, 28)
        d = date(GL_YEAR, GL_RATIO_ANOMALY_MONTH, day)
        iso_date = d.strftime("%Y-%m-%dT00:00:00")
        doc_num = f"INV-{rng.randint(1000, 9999)}"
        amount = round(rng.uniform(8_000, 25_000), 2)
        main_memo, line_memo = _gl_memo(rng)
        table.append(text_row([
            "Invoice", iso_date, _period(d), "Standard", doc_num,
            rng.choice(ENTITIES), main_memo,
            GL_RATIO_ANOMALY_ACCOUNT, line_memo, amount, rng.choice(EMPLOYEES),
        ], GL_TYPES))

    return wb


# ---------------------------------------------------------------------------
# Fixture 2 — sample_with_metadata.xls (SFDC-style header at row 15).
# ---------------------------------------------------------------------------

OPP_HEADERS = ["Opportunity ID", "Account Name", "Close Date", "Amount", "Stage", "Owner"]
OPP_TYPES = ["String", "String", "DateTime", "Number", "String", "String"]


def build_opportunities_export() -> etree._Element:
    rng = random.Random(SEED + 1)
    wb = workbook_root()
    ws = etree.SubElement(wb, f"{SS}Worksheet")
    ws.set(f"{SS}Name", "Sheet1")
    table = etree.SubElement(ws, f"{SS}Table")

    metadata_lines: list[str] = [
        "Saved Search: Quarterly Pipeline by Stage",
        "Generated: 2024-12-31 10:30:00 PST",
        "User: Alex Rivera",
        "Filter: Close Date in current fiscal year",
        "Sort: Close Date ascending",
        "Records: 50",
        "",
        "Stage Distribution:",
        "Prospecting: 9",
        "Qualification: 8",
        "Proposal: 9",
        "Negotiation: 8",
        "Closed Won: 8",
        "Closed Lost: 8",
    ]
    assert len(metadata_lines) == OPP_METADATA_ROWS, "metadata block must be exactly OPP_METADATA_ROWS rows"
    for line in metadata_lines:
        row = etree.SubElement(table, f"{SS}Row")
        if line:
            row.append(cell(line, "String"))

    table.append(text_row(OPP_HEADERS, ["String"] * len(OPP_HEADERS)))

    base_date = date(2024, 1, 1)
    for i in range(1, OPP_ROWS + 1):
        opp_id = f"OPP-{i:05d}"
        account = rng.choice(ENTITIES)
        close = base_date + timedelta(days=rng.randint(0, 365))
        iso_close = close.strftime("%Y-%m-%dT00:00:00")
        amount = round(rng.uniform(5_000, 500_000), 2)
        stage = rng.choice(STAGES)
        owner = rng.choice(EMPLOYEES)
        table.append(text_row(
            [opp_id, account, iso_close, amount, stage, owner], OPP_TYPES
        ))

    return wb


# ---------------------------------------------------------------------------
# Fixture 3 — sample_malformed.xls (injected breakage on rows 3 and 7).
# ---------------------------------------------------------------------------

def write_malformed_export(path: Path) -> None:
    """Hand-rolled writer.

    lxml normalizes/escapes the things we want broken, so this one is
    string-built. Three injected breakages:

      Row 3 — raw '&' in the Name field (invalid XML, recoverable).
      Row 7 — ss:Type='DateTime' with value 'not-a-date'
              (well-formed XML, semantic garbage).
      Row 12 — trailing <Cell ss:Index="15"> beyond the 11-column header
               (phantom column from hidden NS grouping cells; the parser
               must drop it without corrupting the row).

    Everything else is clean so a recovering parser still gets most of
    the file.
    """
    rng = random.Random(SEED + 2)
    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"'
        ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
    )
    out.append('  <Worksheet ss:Name="Sheet1">')
    out.append('    <Table>')

    def emit_row(
        values: list[tuple[str, str]],
        indent: str = "      ",
        extra_indexed: list[tuple[int, str, str]] | None = None,
    ) -> None:
        out.append(f"{indent}<Row>")
        for typ, val in values:
            out.append(f'{indent}  <Cell><Data ss:Type="{typ}">{val}</Data></Cell>')
        if extra_indexed:
            for ss_index, typ, val in extra_indexed:
                out.append(
                    f'{indent}  <Cell ss:Index="{ss_index}">'
                    f'<Data ss:Type="{typ}">{val}</Data></Cell>'
                )
        out.append(f"{indent}</Row>")

    emit_row([("String", h) for h in GL_HEADERS])

    for row_idx in range(1, MALFORMED_ROWS + 1):
        d = date(2024, 1, 1) + timedelta(days=row_idx * 7)
        iso_date = d.strftime("%Y-%m-%dT00:00:00")
        amount = round(rng.uniform(500, 10_000), 2)
        account_num, _ = rng.choice(ACCOUNTS)
        order_type = rng.choice(ORDER_TYPES)
        doc_num = f"{DOC_PREFIXES[order_type]}-{rng.randint(1000, 9999)}"
        name = rng.choice(ENTITIES)
        memo_main = "Standard reclass entry"
        memo_line = "line detail"
        employee = rng.choice(EMPLOYEES)

        if row_idx == 3:
            name = "Smith & Co"     # raw ampersand — invalid XML, recoverable
        if row_idx == 7:
            iso_date = "not-a-date"  # well-formed XML, unparseable DateTime

        # Row 12 carries a phantom <Cell ss:Index="15"> past the 11-column
        # header — mimics hidden NS grouping/subtotal cells leaking into
        # the export. The parser must discard it cleanly.
        extra = [(15, "String", "PHANTOM-COL-15")] if row_idx == 12 else None

        emit_row([
            ("String",   order_type),
            ("DateTime", iso_date),
            ("String",   _period(d)),
            ("String",   "Standard"),
            ("String",   doc_num),
            ("String",   name),
            ("String",   memo_main),
            ("String",   account_num),
            ("String",   memo_line),
            ("Number",   f"{amount}"),
            ("String",   employee),
        ], extra_indexed=extra)

    out.append('    </Table>')
    out.append('  </Worksheet>')
    out.append('</Workbook>')

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

def main() -> None:
    here = Path(__file__).parent
    write_workbook(build_gl_export(), here / "sample_gl_export.xls")
    write_workbook(build_opportunities_export(), here / "sample_with_metadata.xls")
    write_malformed_export(here / "sample_malformed.xls")
    for name in ("sample_gl_export.xls", "sample_with_metadata.xls", "sample_malformed.xls"):
        p = here / name
        print(f"wrote {p} ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
