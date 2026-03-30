"""
Tests for brain.respond_with_tools() — tool_use loop.

Uses mock Anthropic client with sequential responses — no live API calls.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def force_sdk_path():
    """Ensure all brain tests use the mocked SDK path, not the subprocess fallback."""
    with patch("simply_connect.brain._api_key", return_value="fake-test-key"):
        yield


@pytest.fixture
def project_root(tmp_path):
    (tmp_path / "AGENT.md").write_text("# AGENT.md\n\nYou are a contract assistant.\n")
    return tmp_path


@pytest.fixture
def mock_context():
    return {
        "committed": {"business": "Acme Ltd.", "contracts": "## SLA Terms\n- Test: 2026-06-01"},
        "staging": [],
    }


@pytest.fixture
def dummy_tools():
    return [
        {
            "name": "get_sla_status",
            "description": "Get SLA status.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }
    ]


def _make_text_response(text: str):
    """Mock response with stop_reason=end_turn and a text block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def _make_tool_use_response(tool_name: str, tool_args: dict, tool_use_id: str = "tu_123"):
    """Mock response with stop_reason=tool_use and a tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.input = tool_args
    block.id = tool_use_id
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


# ---------------------------------------------------------------------------
# respond_with_tools
# ---------------------------------------------------------------------------

class TestRespondWithTools:
    def test_returns_required_keys(self, project_root, mock_context, dummy_tools):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_text_response("Direct reply.")

        dispatch_fn = MagicMock(return_value='{"result": "ok"}')

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond_with_tools
            result = respond_with_tools(
                message="Hello",
                context=mock_context,
                tools=dummy_tools,
                dispatch_fn=dispatch_fn,
            )

        for key in ("reply", "capture", "confidence", "used_unconfirmed", "raw_response"):
            assert key in result

    def test_end_turn_returns_text_reply(self, project_root, mock_context, dummy_tools):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_text_response("Here is my answer.")

        dispatch_fn = MagicMock()

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond_with_tools
            result = respond_with_tools("Tell me something.", mock_context, dummy_tools, dispatch_fn)

        assert result["reply"] == "Here is my answer."
        dispatch_fn.assert_not_called()

    def test_tool_use_loop_calls_dispatch(self, project_root, mock_context, dummy_tools):
        """Claude calls a tool once, then returns end_turn."""
        tool_response = _make_tool_use_response("get_sla_status", {}, "tu_001")
        final_response = _make_text_response("SLA status looks good.")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, final_response]

        dispatch_fn = MagicMock(return_value='{"total": 2, "at_risk_count": 0}')

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond_with_tools
            result = respond_with_tools("Check SLA status.", mock_context, dummy_tools, dispatch_fn)

        dispatch_fn.assert_called_once_with("get_sla_status", {})
        assert result["reply"] == "SLA status looks good."
        assert mock_client.messages.create.call_count == 2

    def test_tool_use_loop_two_consecutive_tools(self, project_root, mock_context, dummy_tools):
        """Claude calls two tools across two turns before end_turn."""
        resp1 = _make_tool_use_response("get_sla_status", {}, "tu_001")
        resp2 = _make_tool_use_response("get_sla_status", {}, "tu_002")
        resp3 = _make_text_response("Done.")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [resp1, resp2, resp3]

        dispatch_fn = MagicMock(return_value='{"ok": true}')

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond_with_tools
            result = respond_with_tools("Run twice.", mock_context, dummy_tools, dispatch_fn)

        assert dispatch_fn.call_count == 2
        assert result["reply"] == "Done."

    def test_dispatch_error_returns_error_json_to_claude(self, project_root, mock_context, dummy_tools):
        """If dispatch raises, the error is passed back to Claude as a tool_result."""
        tool_response = _make_tool_use_response("get_sla_status", {}, "tu_err")
        final_response = _make_text_response("I encountered an error with that tool.")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, final_response]

        dispatch_fn = MagicMock(side_effect=Exception("API timeout"))

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond_with_tools
            result = respond_with_tools("Check SLA.", mock_context, dummy_tools, dispatch_fn)

        # Should not crash — error passed to Claude as tool_result content
        assert isinstance(result["reply"], str)
        # Verify the second call to Claude included a tool_result with an error
        second_call_messages = mock_client.messages.create.call_args_list[1][1]["messages"]
        tool_result_msg = second_call_messages[-1]
        assert tool_result_msg["role"] == "user"
        tool_result_content = tool_result_msg["content"]
        assert any("error" in c.get("content", "") for c in tool_result_content)

    def test_api_error_returns_fallback_message(self, project_root, mock_context, dummy_tools):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("Network failure")

        dispatch_fn = MagicMock()

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond_with_tools
            result = respond_with_tools("Hello", mock_context, dummy_tools, dispatch_fn)

        assert isinstance(result["reply"], str)
        assert len(result["reply"]) > 0  # Graceful fallback

    def test_history_is_included_in_messages(self, project_root, mock_context, dummy_tools):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_text_response("OK")

        history = [
            {"role": "user", "content": "Earlier message"},
            {"role": "assistant", "content": "Earlier reply"},
        ]

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond_with_tools
            respond_with_tools("New message", mock_context, dummy_tools, MagicMock(), history=history)

        call_kwargs = mock_client.messages.create.call_args[1]
        messages = call_kwargs["messages"]
        roles = [m["role"] for m in messages]
        assert "user" in roles
        assert "assistant" in roles

    def test_capture_tool_included_in_tools_list(self, project_root, mock_context, dummy_tools):
        """capture_to_staging should always be included in the tools passed to Claude."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_text_response("OK")

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond_with_tools
            respond_with_tools("Hello", mock_context, dummy_tools, MagicMock())

        call_kwargs = mock_client.messages.create.call_args[1]
        tool_names = [t["name"] for t in call_kwargs["tools"]]
        assert "capture_to_staging" in tool_names
