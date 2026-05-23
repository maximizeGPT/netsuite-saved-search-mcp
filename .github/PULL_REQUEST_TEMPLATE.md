## Summary

One or two sentences on what this PR does and why.

## Checklist

- [ ] Tests pass locally (`uv run pytest`)
- [ ] `uv run ruff check src tests` clean
- [ ] `uv run mypy src/netsuite_saved_search_mcp` clean
- [ ] `CHANGELOG.md` updated under `[Unreleased]` if user-facing
- [ ] `README.md` tool table updated if a tool was added / changed
- [ ] New tool: response model, happy-path test, failure-path test
- [ ] If this changes the `Predicate` / `Measure` model, tool schema,
      or parser API — linked issue with prior design discussion: #
