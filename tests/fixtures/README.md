# Test fixtures

Three sanitized NetSuite-shaped XML SpreadsheetML files, generated
deterministically by [`generate_fixtures.py`](generate_fixtures.py)
with `SEED=0xC0FFEE`. Each fixture targets a specific failure mode
the parser handles — the names are intentionally bland because
they're what real NetSuite exports look like.

Regenerate any time with:

```bash
uv run python tests/fixtures/generate_fixtures.py
```

## `sample_gl_export.xls`

The clean case. `header_row=0`, 212 rows total (200 base GL entries
+ 12 additional September Account-1200 rows that exercise the
ratio-anomaly detector), 11 columns. Active for 11 months of 2024;
**June is intentionally zero-activity** so the
`detect_anomalies` zero-activity check has a HIGH-severity case to
surface. 6 distinct accounts (1000–1500, 1200, 4000, etc.), 5 memo
flavors for `categorize_by_memo`.

**What this tests:** the happy path — straightforward parsing,
header detection, every tool against a well-formed export.

## `sample_with_metadata.xls`

NetSuite exports often include a metadata block above the actual
data — saved-search title, run timestamp, filters, etc. This
fixture reproduces that pattern: 14 rows of metadata, then the
real header at `header_row=14`, then 50 opportunity rows. The
parser must auto-detect the header row rather than assume it's at
index 0.

**What this tests:** `get_headers` metadata-block detection, and
the cascading effect on every downstream tool (a wrong header row
would shift every subsequent column lookup).

## `sample_malformed.xls`

A 20-row GL-style fixture with three injected breakages that
exercise lxml's recovery mode:

1. **Row 3 — raw ampersand.** Entity-decoded `&` in a memo column,
   which would crash a strict XML parser. The recovery parser
   surfaces a structured `encoding_recovery` warning instead.
2. **Row 7 — bad DateTime.** Cell content `"not-a-date"` where a
   DateTime is expected. Reported as `bad_datetime` warning; the
   row is kept (other columns parse), DateTime cell stored as
   `None`.
3. **Row 12 — phantom column.** An extra cell appears at column
   index 15 with no corresponding header. Reported as
   `phantom_column` warning; the cell is dropped, row otherwise
   intact.

**What this tests:** the recovery-mode parser's structured warning
output (via `get_parse_warnings`), and that the parser keeps
producing usable data instead of crashing on broken cells.
