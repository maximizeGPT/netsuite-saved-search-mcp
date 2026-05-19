# End-to-end walkthrough

This walkthrough drives all seven tools against the three sanitized fixtures shipped under [`tests/fixtures/`](../tests/fixtures/). Every response below is real output from running the tool against the fixture, not invented.

## 0. Configure Claude Desktop

Point the MCP server at the directory holding your exports. For this walkthrough, that's the included fixtures directory.

```json
{
  "mcpServers": {
    "netsuite-saved-search": {
      "command": "uvx",
      "args": ["netsuite-saved-search-mcp"],
      "env": {
        "NSMCP_ROOT": "/absolute/path/to/netsuite-saved-search-mcp/tests/fixtures",
        "NSMCP_LOG_LEVEL": "INFO"
      }
    }
  }
}
```

Full config: [examples/claude_desktop_config.json](claude_desktop_config.json).

Restart Claude Desktop. The seven tools appear in the tools menu.

## 1. Discover what's in the directory

Always start here when you don't know what files are present. The summary tells you which files parsed cleanly, which have warnings worth investigating, and what date range each covers.

**Call**:

```json
{
  "tool": "list_exports",
  "arguments": {"directory": "."}
}
```

**Response**:

```json
[
  {
    "filename": "sample_gl_export.xls",
    "row_count": 212,
    "header_count": 11,
    "header_row": 0,
    "warning_count": 0,
    "date_range": {"start": "2024-01-01", "end": "2024-12-28"},
    "parse_error": null
  },
  {
    "filename": "sample_malformed.xls",
    "row_count": 20,
    "header_count": 11,
    "header_row": 0,
    "warning_count": 3,
    "date_range": {"start": "2024-01-08", "end": "2024-05-20"},
    "parse_error": null
  },
  {
    "filename": "sample_with_metadata.xls",
    "row_count": 50,
    "header_count": 6,
    "header_row": 14,
    "warning_count": 0,
    "date_range": {"start": "2024-01-01", "end": "2024-12-31"},
    "parse_error": null
  }
]
```

Notice:
- `sample_with_metadata.xls` has `header_row: 14` — the parser walked past 14 metadata rows above the headers.
- `sample_malformed.xls` has `warning_count: 3` despite parsing all 20 rows; lxml recovered the file.
- Every file has `parse_error: null`, meaning all three parsed. If lxml failed completely on a file, `parse_error` would carry the message and the other fields would be null.

## 2. Inspect the schema

Before filtering or aggregating, find out what columns exist. The response includes spreadsheet column letters so you can cross-reference with the source export in Excel.

**Call**:

```json
{
  "tool": "get_headers",
  "arguments": {"file_path": "sample_gl_export.xls"}
}
```

**Response**:

```json
{
  "headers": [
    "Order Type", "Date", "Period", "Type", "Document Number",
    "Name", "Memo (main)", "Account", "Memo (line)", "Amount", "Created By"
  ],
  "column_letters": {
    "Order Type": "A", "Date": "B", "Period": "C", "Type": "D",
    "Document Number": "E", "Name": "F", "Memo (main)": "G",
    "Account": "H", "Memo (line)": "I", "Amount": "J", "Created By": "K"
  },
  "header_row": 0
}
```

## 3. Query a slice with two predicates

Filter the export to Q1 2024 rows posted to account 4000, project to five columns, cap the result. Predicates are AND-combined.

**Call**:

```json
{
  "tool": "query_export",
  "arguments": {
    "file_path": "sample_gl_export.xls",
    "filters": [
      {"op": "eq", "column": "Account", "value": "4000"},
      {"op": "date_range", "column": "Date", "start": "2024-01-01", "end": "2024-03-31"}
    ],
    "columns": ["Date", "Document Number", "Account", "Amount", "Memo (main)"],
    "limit": 20
  }
}
```

**Response** (first 3 rows shown):

```json
{
  "rows": [
    {
      "Date": "2024-01-01",
      "Document Number": "BILL-2984",
      "Account": "4000",
      "Amount": 38589.7,
      "Memo (main)": "Monthly amortization of Q2 entry"
    },
    {
      "Date": "2024-01-17",
      "Document Number": "CM-2951",
      "Account": "4000",
      "Amount": 33947.94,
      "Memo (main)": "Invoice from prepaid insurance"
    },
    {
      "Date": "2024-02-11",
      "Document Number": "CM-2100",
      "Account": "4000",
      "Amount": 7888.89,
      "Memo (main)": "Invoice from Q2 entry"
    }
  ],
  "total_matched": 8,
  "truncated": false
}
```

`total_matched: 8, truncated: false` means there are exactly 8 matching rows and you got all of them. If `total_matched` had exceeded `limit`, `truncated` would be `true` and `rows` would carry the first `limit` entries.

## 4. Categorize rows by memo keywords

Most close-process work eventually boils down to "which rows are NC Reclass, which are amortizations, which are corrections, which are accruals." Pass keyword rules and the tool tags every row with a `_category` column.

**Call**:

```json
{
  "tool": "categorize_by_memo",
  "arguments": {
    "file_path": "sample_gl_export.xls",
    "memo_columns": ["Memo (main)", "Memo (line)"],
    "rules": {
      "NC Reclass": ["reclass", "nc reclass"],
      "Amortization": ["amortization"],
      "Correction": ["correction"],
      "Accrual": ["accrual"]
    }
  }
}
```

**Response** (first 2 rows shown plus the breakdown):

```json
{
  "rows": [
    {
      "Order Type": "Bill",
      "Date": "2024-01-01",
      "Period": "Jan 2024",
      "Document Number": "BILL-2984",
      "Name": "Sunrise Ventures",
      "Memo (main)": "Monthly amortization of Q2 entry",
      "Account": "4000",
      "Memo (line)": "Amortization: line detail",
      "Amount": 38589.7,
      "Created By": "Jordan Kim",
      "_category": "Amortization"
    },
    {
      "Order Type": "Bill",
      "Date": "2024-01-25",
      "Period": "Jan 2024",
      "Document Number": "BILL-7932",
      "Name": "Sunrise Ventures",
      "Memo (main)": "Correction to subscription revenue entry",
      "Account": "1500",
      "Memo (line)": "Correction: line detail",
      "Amount": 5394.09,
      "Created By": "Jordan Kim",
      "_category": "Correction"
    }
  ],
  "breakdown": {
    "NC Reclass": 31,
    "Amortization": 47,
    "Correction": 45,
    "Accrual": 51,
    "Uncategorized": 38
  }
}
```

First match wins. Reorder the rules dict to change precedence.

## 5. Aggregate by account

Group rows by account and sum the amounts. Useful for spotting where the dollars went without paging through every row.

**Call**:

```json
{
  "tool": "aggregate_export",
  "arguments": {
    "file_path": "sample_gl_export.xls",
    "group_by": ["Account"],
    "measures": [
      {"column": "Amount", "op": "sum", "alias": "total_amount"},
      {"column": "Amount", "op": "count", "alias": "row_count"}
    ]
  }
}
```

**Response**:

```json
{
  "groups": [
    {"Account": "4000", "total_amount": 376782.5,  "row_count": 29},
    {"Account": "1500", "total_amount": 279556.58, "row_count": 33},
    {"Account": "1200", "total_amount": 555587.74, "row_count": 53},
    {"Account": "4100", "total_amount": 716518.17, "row_count": 40},
    {"Account": "2010", "total_amount": -510144.49, "row_count": 24},
    {"Account": "1010", "total_amount": 589414.99, "row_count": 33}
  ]
}
```

## 6. Surface anomalies

Three checks run in one call: missing periods, account-period totals that spike against the account's own median, and periods whose document count is more than 2 stdev from the mean.

**Call**:

```json
{
  "tool": "detect_anomalies",
  "arguments": {
    "file_path": "sample_gl_export.xls",
    "account_column": "Account",
    "amount_column": "Amount",
    "period_column": "Period"
  }
}
```

**Response** (representative findings, supporting rows trimmed for brevity):

```json
{
  "findings": [
    {
      "severity": "HIGH",
      "category": "zero_activity_period",
      "description": "No rows recorded for Jun 2024",
      "supporting_rows": [],
      "total_supporting_count": 0
    },
    {
      "severity": "MEDIUM",
      "category": "ratio_anomaly",
      "description": "Account 1200 in Sep 2024 totals 319,219.74 — 3.1x the 1200 median of 103,259.01",
      "supporting_rows": [
        {
          "Date": "2024-09-22",
          "Document Number": "BILL-3547",
          "Account": "1200",
          "Amount": 20251.56,
          "Memo (main)": "Monthly amortization of consulting fees"
        }
      ],
      "total_supporting_count": 17
    },
    {
      "severity": "MEDIUM",
      "category": "document_count_variance",
      "description": "Period Sep 2024 has 30 documents (+3.0 stdev from mean of 19.3)",
      "supporting_rows": [],
      "total_supporting_count": 30
    }
  ]
}
```

Read the findings as a triage list:
- `zero_activity_period` for Jun 2024 → confirms June had no postings (intentional gap in the fixture).
- Account 1200 in Sep 2024 ran 3.1x its own median → the fixture seeds 12 extra rows there to exercise the detector.
- Sep 2024's 30 documents sit 3 stdev above the mean of 19.3 → the same seed shows up as a count anomaly too.

`total_supporting_count` reports the true count even when `supporting_rows` is truncated to the 10-row cap.

## 7. Read the parse warnings

When `list_exports` reports a non-zero `warning_count`, this is where you go to see exactly which rows are affected.

**Call**:

```json
{
  "tool": "get_parse_warnings",
  "arguments": {"file_path": "sample_malformed.xls"}
}
```

**Response**:

```json
[
  {
    "row": null,
    "kind": "encoding_recovery",
    "message": "line 50: xmlParseEntityRef: no name"
  },
  {
    "row": 6,
    "kind": "bad_datetime",
    "message": "column 'Date' value 'not-a-date' not parseable as DateTime; raw string preserved"
  },
  {
    "row": 11,
    "kind": "phantom_column",
    "message": "Cell at column 15 has no matching header (export has 11 columns); cell value 'PHANTOM-COL-15' discarded"
  }
]
```

The malformed fixture intentionally seeds one of each recoverable failure:
- **Row 3** of the file (data index `null` — the warning is per-line not per-row) contains a raw `&` in a Name cell. lxml's recovery handled it and logged an `encoding_recovery`.
- **Row 7** of the file (data index `6`) carries `ss:Type="DateTime"` with value `"not-a-date"`. The cell stays in the result as the raw string; the row isn't dropped.
- **Row 12** of the file (data index `11`) has a phantom `<Cell ss:Index="15">` beyond the 11-column header. The cell is silently discarded.

The malformed file's `row_count` from step 1 was still 20: every row landed, every breakage was reported.

---

That's the loop. `list_exports` finds work, `get_headers` tells you what's there, `query_export` and `aggregate_export` slice it, `categorize_by_memo` adds derived columns for downstream prompts, `detect_anomalies` triages, and `get_parse_warnings` keeps recoverable parse issues visible instead of swallowing them.
