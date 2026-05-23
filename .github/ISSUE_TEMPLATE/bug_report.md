---
name: Bug report
about: Something the MCP server did that you didn't expect
title: "[BUG] "
labels: bug
assignees: ''
---

## What happened

A clear description of the surprise. One or two sentences.

## What you expected

What the server should have done instead. One or two sentences.

## Reproduction steps

The tool call that triggered the bug:

```json
{
  "tool": "...",
  "arguments": { ... }
}
```

Plus any prior calls in the same session that establish state.

## MCP server log

Run with `NSMCP_LOG_LEVEL=DEBUG` and paste the relevant log lines:

```
<paste here>
```

Trim or redact any line containing your production data — the
server log surfaces parse warnings and tool dispatch events; both
contain enough context to be diagnostic without raw row contents.

## Fixture

If the bug is parser-shaped, attach a **sanitized** fixture that
reproduces. **Do not attach production exports.** The fixtures in
[`tests/fixtures/`](../../tests/fixtures/) are the right shape; if
yours doesn't match an existing fixture's pattern, mention what
makes it different (column layout, encoding, metadata block).

## Environment

- Python version: <output of `python --version`>
- Server version: <`uvx netsuite-saved-search-mcp --version` or pip show>
- MCP client: <Claude Desktop / claude-eval-harness / other>
- OS: <macOS / Linux / Windows + version>

## Anything else

Optional. Related issues, hypotheses, prior workarounds you tried.
