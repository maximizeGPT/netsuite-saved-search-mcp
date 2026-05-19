"""Shared fixtures and helpers for the parser test suite."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture(scope="session")
def gl_export_path(fixture_dir: Path) -> Path:
    return fixture_dir / "sample_gl_export.xls"


@pytest.fixture(scope="session")
def metadata_export_path(fixture_dir: Path) -> Path:
    return fixture_dir / "sample_with_metadata.xls"


@pytest.fixture(scope="session")
def malformed_export_path(fixture_dir: Path) -> Path:
    return fixture_dir / "sample_malformed.xls"


def write_synthetic_export(
    path: Path,
    headers: list[str],
    rows: Iterable[list[tuple[str, str] | None]],
    *,
    metadata_lines: list[str] | None = None,
    extra_indexed_per_row: dict[int, list[tuple[int, str, str]]] | None = None,
) -> None:
    """Build a minimal SpreadsheetML file for unit tests.

    Each cell is either (ss_type, raw_value) or None to omit it — the
    next non-None cell carries ss:Index for the parser to recover from.
    `extra_indexed_per_row` lets a test inject phantom-index cells past
    the header on specific row offsets (0-indexed against the data set).
    """
    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"'
        ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">'
    )
    out.append('  <Worksheet ss:Name="Sheet1"><Table>')

    if metadata_lines:
        for line in metadata_lines:
            if line:
                out.append(
                    f'    <Row><Cell><Data ss:Type="String">{line}</Data></Cell></Row>'
                )
            else:
                out.append("    <Row/>")

    header_cells = "".join(
        f'<Cell><Data ss:Type="String">{h}</Data></Cell>' for h in headers
    )
    out.append(f"    <Row>{header_cells}</Row>")

    extras = extra_indexed_per_row or {}
    for row_idx, row in enumerate(rows):
        cells: list[str] = []
        pending_gap = False
        for col_idx, cell in enumerate(row, start=1):
            if cell is None:
                pending_gap = True
                continue
            ss_type, val = cell
            attr = f' ss:Index="{col_idx}"' if pending_gap else ""
            cells.append(
                f'<Cell{attr}><Data ss:Type="{ss_type}">{val}</Data></Cell>'
            )
            pending_gap = False
        for ss_idx, ss_type, val in extras.get(row_idx, []):
            cells.append(
                f'<Cell ss:Index="{ss_idx}">'
                f'<Data ss:Type="{ss_type}">{val}</Data></Cell>'
            )
        out.append(f"    <Row>{''.join(cells)}</Row>")

    out.append("    </Table></Worksheet>")
    out.append("</Workbook>")

    path.write_text("\n".join(out), encoding="utf-8")
