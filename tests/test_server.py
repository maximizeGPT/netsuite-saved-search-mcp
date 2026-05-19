"""Server-layer tests — registration, descriptions, smoke."""

from __future__ import annotations

import asyncio

import pytest

from netsuite_saved_search_mcp.server import _TOOL_DESCRIPTIONS, mcp

EXPECTED_TOOL_NAMES = {
    "list_exports",
    "get_headers",
    "query_export",
    "aggregate_export",
    "categorize_by_memo",
    "detect_anomalies",
    "get_parse_warnings",
}


@pytest.fixture(scope="module")
def registered_tools() -> list:
    return asyncio.run(mcp.list_tools())


def test_server_metadata() -> None:
    assert mcp.name == "netsuite-saved-search-mcp"


def test_all_seven_tools_registered(registered_tools: list) -> None:
    names = {t.name for t in registered_tools}
    assert names == EXPECTED_TOOL_NAMES, f"diff: {names ^ EXPECTED_TOOL_NAMES}"


def test_every_tool_has_a_description(registered_tools: list) -> None:
    for tool in registered_tools:
        assert tool.description, f"{tool.name} has no description"
        # Descriptions must be specific enough to disambiguate from peers —
        # cheap proxy: at least 60 chars (anything shorter is a stub).
        assert len(tool.description) >= 60, (
            f"{tool.name} description is too short: {tool.description!r}"
        )


def test_descriptions_dict_matches_registered_set() -> None:
    assert set(_TOOL_DESCRIPTIONS.keys()) == EXPECTED_TOOL_NAMES


def test_every_tool_has_an_input_schema(registered_tools: list) -> None:
    for tool in registered_tools:
        schema = tool.inputSchema
        assert schema is not None, f"{tool.name} has no input schema"
        assert "properties" in schema, f"{tool.name} schema has no properties"


def test_tool_schemas_reference_their_arguments(registered_tools: list) -> None:
    by_name = {t.name: t for t in registered_tools}

    # file_path is required across every tool that takes a single file.
    for name in ("get_headers", "query_export", "aggregate_export",
                 "categorize_by_memo", "detect_anomalies", "get_parse_warnings"):
        props = by_name[name].inputSchema["properties"]
        assert "file_path" in props, f"{name} input schema missing file_path"

    # list_exports takes `directory` instead.
    assert "directory" in by_name["list_exports"].inputSchema["properties"]


def test_main_callable_exists() -> None:
    from netsuite_saved_search_mcp.server import main
    assert callable(main)
