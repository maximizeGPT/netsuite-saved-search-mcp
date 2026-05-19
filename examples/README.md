# examples

Two artifacts for setting up the server and learning the tools.

- [`claude_desktop_config.json`](claude_desktop_config.json) — minimal Claude Desktop MCP config. Edit `NSMCP_ROOT` to point at the directory holding your saved-search exports, drop it into `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or the equivalent on Windows, restart Claude Desktop.
- [`walkthrough.md`](walkthrough.md) — end-to-end run through all seven tools against the three sanitized fixtures in [`../tests/fixtures/`](../tests/fixtures/). Every tool call and response is real output from running against the fixtures, not invented. Read this once before pointing the server at production exports.
