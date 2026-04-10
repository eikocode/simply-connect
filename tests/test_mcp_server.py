"""Tests for simply_connect.mcp_server — MCP tool definitions and dispatch."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_project(tmp_path: Path) -> Path:
    """Create a minimal simply-connect project for MCP tests."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "AGENT.md").write_text("# Test Agent\n")
    (root / "profile.json").write_text(json.dumps({
        "name": "Test",
        "context_files": ["business", "parties"],
        "category_map": {"business": "business.md", "parties": "parties.md", "general": "business.md"},
        "capture_roles": [],
        "extensions": [],
    }))
    ctx = root / "context"
    ctx.mkdir()
    (ctx / "business.md").write_text("## Test Business\nA test business.\n")
    (ctx / "parties.md").write_text("## Test Party\nA test party.\n")
    staging = root / "staging"
    staging.mkdir()
    return root


@pytest.fixture
def mcp_module(temp_project: Path, monkeypatch):
    """Import mcp_server with a controlled project root."""
    monkeypatch.chdir(temp_project)

    # Clear any previously imported simply_connect modules so we get a fresh
    # ContextManager pointed at temp_project.
    for mod_name in list(sys.modules):
        if mod_name.startswith("simply_connect"):
            del sys.modules[mod_name]

    from simply_connect.mcp_server import (
        _all_tools,
        _cm,
        _session_role,
        call_tool,
        list_tools,
    )
    yield {
        "call_tool": call_tool,
        "list_tools": list_tools,
        "cm": _cm,
        "all_tools": _all_tools,
        "session_role": _session_role,
    }


# ---------------------------------------------------------------------------
# Helper: run async function synchronously
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------

class TestToolListing:
    def test_core_tools_are_registered(self, mcp_module):
        tool_names = [t["name"] for t in mcp_module["all_tools"]]
        assert "get_committed_context" in tool_names
        assert "get_staging_entries" in tool_names
        assert "capture_to_staging" in tool_names
        assert "ingest_document" in tool_names

    def test_list_tools_returns_types(self, mcp_module):
        from mcp import types
        tools = _run(mcp_module["list_tools"]())
        assert isinstance(tools, list)
        assert len(tools) >= 4
        for t in tools:
            assert isinstance(t, types.Tool)
            assert t.name
            assert t.description
            assert t.inputSchema


# ---------------------------------------------------------------------------
# get_committed_context
# ---------------------------------------------------------------------------

class TestGetCommittedContext:
    def test_returns_all_categories(self, mcp_module):
        result = _run(mcp_module["call_tool"]("get_committed_context", {}))
        data = json.loads(result[0].text)
        assert "committed_context" in data
        assert "business" in data["committed_context"]
        assert "parties" in data["committed_context"]
        assert "note" in data

    def test_filters_by_category(self, mcp_module):
        result = _run(mcp_module["call_tool"]("get_committed_context", {"category": "business"}))
        data = json.loads(result[0].text)
        assert set(data["committed_context"].keys()) == {"business"}

    def test_returns_empty_for_unknown_category(self, mcp_module):
        result = _run(mcp_module["call_tool"]("get_committed_context", {"category": "nonexistent"}))
        data = json.loads(result[0].text)
        assert data["committed_context"] == {}


# ---------------------------------------------------------------------------
# get_staging_entries
# ---------------------------------------------------------------------------

class TestGetStagingEntries:
    def test_empty_staging(self, mcp_module):
        result = _run(mcp_module["call_tool"]("get_staging_entries", {}))
        data = json.loads(result[0].text)
        assert data["staging_entries"] == []
        assert data["count"] == 0
        assert "UNCONFIRMED" in data["note"]

    def test_lists_pending_entries(self, mcp_module):
        cm = mcp_module["cm"]
        cm.create_staging_entry("Test summary", "Test content", "business")
        result = _run(mcp_module["call_tool"]("get_staging_entries", {"status": "unconfirmed"}))
        data = json.loads(result[0].text)
        assert data["count"] == 1
        assert data["staging_entries"][0]["summary"] == "Test summary"


# ---------------------------------------------------------------------------
# capture_to_staging
# ---------------------------------------------------------------------------

class TestCaptureToStaging:
    def test_creates_staging_entry(self, mcp_module):
        result = _run(mcp_module["call_tool"]("capture_to_staging", {
            "summary": "Test capture",
            "content": "Captured content",
            "category": "business",
        }))
        data = json.loads(result[0].text)
        assert data["status"] == "pending"
        assert "entry_id" in data
        assert "Pending admin review" in data["message"]

    def test_requires_summary_and_content(self, mcp_module):
        with pytest.raises(ValueError, match="required"):
            _run(mcp_module["call_tool"]("capture_to_staging", {
                "summary": "",
                "content": "",
                "category": "business",
            }))

    def test_custom_source(self, mcp_module):
        result = _run(mcp_module["call_tool"]("capture_to_staging", {
            "summary": "Web capture",
            "content": "Content",
            "category": "business",
            "source": "webmcp:test",
        }))
        data = json.loads(result[0].text)
        cm = mcp_module["cm"]
        entry = cm.get_staging_entry(data["entry_id"])
        assert entry["source"] == "webmcp:test"


# ---------------------------------------------------------------------------
# ingest_document
# ---------------------------------------------------------------------------

class TestIngestDocument:
    def test_ingests_text_file(self, mcp_module, tmp_path, monkeypatch):
        monkeypatch.setenv("SC_DOCUMENT_PARSER", "claude")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")

        txt = tmp_path / "test.txt"
        txt.write_text("This is test content for the business.")

        result = _run(mcp_module["call_tool"]("ingest_document", {"filepath": str(txt)}))
        data = json.loads(result[0].text)
        assert "success" in data

    def test_raises_for_missing_filepath_arg(self, mcp_module):
        with pytest.raises(KeyError):
            _run(mcp_module["call_tool"]("ingest_document", {}))

    def test_returns_error_for_unsupported_parser(self, mcp_module, tmp_path, monkeypatch):
        monkeypatch.setenv("SC_DOCUMENT_PARSER", "docling")
        txt = tmp_path / "test.txt"
        txt.write_text("content")

        result = _run(mcp_module["call_tool"]("ingest_document", {"filepath": str(txt)}))
        data = json.loads(result[0].text)
        assert data["success"] is False


# ---------------------------------------------------------------------------
# Unknown tool
# ---------------------------------------------------------------------------

class TestUnknownTool:
    def test_raises_for_unknown_tool(self, mcp_module):
        with pytest.raises(ValueError, match="Unknown tool"):
            _run(mcp_module["call_tool"]("nonexistent_tool", {}))


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_defaults_to_stdio(self, monkeypatch):
        from simply_connect.mcp_server import main

        monkeypatch.setattr(asyncio, "run", lambda coro: None)
        monkeypatch.setattr("sys.argv", ["sc-mcp"])
        main()

    def test_main_http_uses_port(self, monkeypatch):
        from simply_connect.mcp_server import main

        monkeypatch.setattr(asyncio, "run", lambda coro: None)
        monkeypatch.setattr("sys.argv", ["sc-mcp", "--http", "--port", "9999"])
        main()
