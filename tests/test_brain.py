"""
Tests for brain.py — response structure and capture detection.

Uses a mock Anthropic client — no live API calls.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


@pytest.fixture(autouse=True)
def force_sdk_path():
    """Ensure all brain tests use the mocked SDK path, not the subprocess fallback."""
    with patch("simply_connect.brain._api_key", return_value="fake-test-key"):
        yield


@pytest.fixture
def project_root(tmp_path):
    """Create a minimal project root with AGENT.md."""
    (tmp_path / "AGENT.md").write_text(
        "# AGENT.md\n\nYou are a contract assistant.\n"
    )
    return tmp_path


@pytest.fixture
def mock_context():
    """Minimal context dict matching load_all_context() output."""
    return {
        "committed": {
            "business": "# Business\n\nAcme Consulting Ltd, Hong Kong.",
            "parties": "# Parties\n\n[empty]",
            "preferences": "# Preferences\n\nPlain English preferred.",
            "contracts": "# Contracts\n\n[empty]",
        },
        "staging": [],
    }


@pytest.fixture
def mock_context_with_staging(mock_context):
    """Context with one unconfirmed staging entry."""
    mock_context["staging"] = [
        {
            "id": "abc-123",
            "summary": "Client XYZ prefers 30-day payment terms",
            "content": "Client XYZ has requested net-30 payment terms on all future contracts.",
            "category": "preferences",
            "status": "unconfirmed",
            "source": "operator",
            "captured": "2026-03-24T14:30:00+00:00",
        }
    ]
    return mock_context


def _make_mock_claude(response_text: str):
    """Create a mock Anthropic client that returns a fixed response."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=response_text)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    return mock_client


class TestRespondStructure:
    def test_returns_all_required_keys(self, project_root, mock_context):
        response_json = json.dumps({
            "reply": "Here is my response.",
            "capture": None,
            "confidence": 0.9,
            "used_unconfirmed": False,
            "raw_response": "reasoning",
        })
        mock_client = _make_mock_claude(response_json)

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond
            result = respond("Draft a simple NDA.", mock_context)

        assert "reply" in result
        assert "capture" in result
        assert "confidence" in result
        assert "used_unconfirmed" in result
        assert "raw_response" in result

    def test_reply_is_string(self, project_root, mock_context):
        response_json = json.dumps({
            "reply": "A contract is an agreement.",
            "capture": None,
            "confidence": 0.85,
            "used_unconfirmed": False,
            "raw_response": "",
        })
        mock_client = _make_mock_claude(response_json)

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond
            result = respond("What is a contract?", mock_context)

        assert isinstance(result["reply"], str)
        assert len(result["reply"]) > 0

    def test_defaults_applied_on_missing_fields(self, project_root, mock_context):
        """Brain should fill in defaults if Claude omits fields."""
        response_json = json.dumps({"reply": "Partial response."})
        mock_client = _make_mock_claude(response_json)

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond
            result = respond("Hello", mock_context)

        assert result["capture"] is None
        assert isinstance(result["confidence"], float)
        assert isinstance(result["used_unconfirmed"], bool)

    def test_handles_api_error_gracefully(self, project_root, mock_context):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API unavailable")

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond
            result = respond("Hello", mock_context)

        assert isinstance(result["reply"], str)
        assert len(result["reply"]) > 0  # Graceful fallback message

    def test_extracts_capture_field(self, project_root, mock_context):
        response_json = json.dumps({
            "reply": "Got it.\n\nCaptured — pending admin review.",
            "capture": {
                "summary": "Client ABC prefers plain English",
                "content": "Operator noted client ABC wants plain English drafting.",
                "category": "preferences",
            },
            "confidence": 0.9,
            "used_unconfirmed": False,
            "raw_response": "",
        })
        mock_client = _make_mock_claude(response_json)

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond
            result = respond("Remember that client ABC prefers plain English", mock_context)

        assert result["capture"] is not None
        assert result["capture"]["category"] == "preferences"
        assert "plain English" in result["capture"]["content"]

    def test_repairs_malformed_json_response(self, project_root, mock_context):
        malformed = '{"reply":"Here is the debit note draft: "Unit A" owes HKD 420","capture":null,"confidence":0.9,"used_unconfirmed":false,"raw_response":"drafted debit note"}'
        repaired = json.dumps({
            "reply": 'Here is the debit note draft: "Unit A" owes HKD 420',
            "capture": None,
            "confidence": 0.9,
            "used_unconfirmed": False,
            "raw_response": "drafted debit note",
        })

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            _make_mock_claude(malformed).messages.create.return_value,
            _make_mock_claude(repaired).messages.create.return_value,
        ]

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond
            result = respond("Generate a debit note for Unit A.", mock_context)

        assert result["reply"] == 'Here is the debit note draft: "Unit A" owes HKD 420'
        assert result["confidence"] == 0.9

    def test_clears_spurious_unconfirmed_flag_when_no_staging_exists(self, project_root, mock_context):
        response_json = json.dumps({
            "reply": "Known facts only.\n\n*(note: drawing on unconfirmed context — pending admin review)*",
            "capture": None,
            "confidence": 0.8,
            "used_unconfirmed": True,
            "raw_response": "incorrectly flagged unconfirmed context",
        })
        mock_client = _make_mock_claude(response_json)

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond
            result = respond("Who is the tenant?", mock_context)

        assert result["used_unconfirmed"] is False
        assert "unconfirmed context" not in result["reply"]


class TestRespondWithStaging:
    def test_used_unconfirmed_flag_set(self, project_root, mock_context_with_staging):
        response_json = json.dumps({
            "reply": "Based on context, Client XYZ uses net-30 payment terms.",
            "capture": None,
            "confidence": 0.8,
            "used_unconfirmed": True,
            "raw_response": "used staging entry about payment terms",
        })
        mock_client = _make_mock_claude(response_json)

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import respond
            result = respond("What payment terms does XYZ use?", mock_context_with_staging)

        assert result["used_unconfirmed"] is True


class TestReviewStagingEntry:
    def test_returns_all_required_keys(self, project_root):
        response_json = json.dumps({
            "recommendation": "approve",
            "reason": "Factual, non-conflicting, adds value.",
            "conflicts": [],
            "suggested_category": "preferences",
            "confidence": 0.9,
        })
        mock_client = _make_mock_claude(response_json)

        entry = {
            "id": "test-id",
            "summary": "Client prefers plain English",
            "content": "Plain English drafting preferred.",
            "category": "preferences",
            "source": "operator",
        }
        committed = {"preferences": "# Preferences\n\n[empty]"}

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import review_staging_entry
            result = review_staging_entry(entry, committed)

        assert "recommendation" in result
        assert "reason" in result
        assert "conflicts" in result
        assert "suggested_category" in result
        assert "confidence" in result

    def test_recommendation_is_valid_value(self, project_root):
        response_json = json.dumps({
            "recommendation": "defer",
            "reason": "Ambiguous.",
            "conflicts": ["May conflict with existing preference"],
            "suggested_category": "preferences",
            "confidence": 0.6,
        })
        mock_client = _make_mock_claude(response_json)

        entry = {"id": "x", "summary": "test", "content": "test", "category": "preferences"}

        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import review_staging_entry
            result = review_staging_entry(entry, {})

        assert result["recommendation"] in ("approve", "reject", "defer")

    def test_handles_api_error_gracefully(self, project_root):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")

        entry = {"id": "x", "summary": "test", "content": "test", "category": "business"}

        # Patch _api_key to return a fake key so the SDK path is taken,
        # then mock the client to raise — verifying graceful fallback to defer.
        with patch("simply_connect.brain._get_claude", return_value=mock_client), \
             patch("simply_connect.brain._api_key", return_value="sk-fake"), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):
            from simply_connect.brain import review_staging_entry
            result = review_staging_entry(entry, {})

        # Should return a safe default — defer, not crash
        assert result["recommendation"] == "defer"
        assert isinstance(result["reason"], str)
