# Security policy

## Supported versions

Only the latest minor release of the MCP server receives security
fixes during the v0.1.x line.

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |
| < 0.1.0 | :x:                |

## Reporting a vulnerability

**Email rayedwasif@hotmail.com.** Do not file a public GitHub issue
for security reports — that just publishes the vulnerability before
there's a fix.

A useful report includes: a description of the issue, the affected
server version, repro steps, and (if relevant) a sanitized copy of
the export file that triggers the issue. **Do not paste production
exports** — the sanitized fixtures in `tests/fixtures/` are the right
shape to attach. I'll acknowledge within 48 hours and ship a patch
on the v0.1.x line if the issue confirms.

## Scope

**In scope** — anything that lets the MCP server escape its
configured `NSMCP_ROOT` (path traversal in `file_path` arguments,
symlink escape, etc.), leak file contents from outside the root,
crash on malformed XML, or execute arbitrary code via a crafted
export file. The XML recovery-mode parser is in scope.

**Out of scope** — vulnerabilities in third-party dependencies
(lxml, pydantic, mcp), in the MCP protocol itself, in Claude
Desktop or the consuming agent runtime. File those upstream.
Cases that produce incorrect aggregation results due to
unanticipated column layouts in your specific exports are bugs,
not security issues — file them as feature requests with a
sanitized fixture.
