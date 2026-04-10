"""End-to-end integration tests for the next design pipeline.

Tests the full flow:
  Domain role session → capture to session → curator evaluates → staging → admin review → committed
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def deployment(tmp_path: Path):
    """Create a full deployment with domain roles and promotion criteria."""
    root = tmp_path / "deploy"
    root.mkdir()
    (root / "AGENT.md").write_text("# Test Domain\n")
    (root / "profile.json").write_text(json.dumps({
        "name": "Test Domain",
        "context_files": ["business", "operations"],
        "category_map": {"business": "business.md", "operations": "operations.md", "general": "business.md"},
        "extensions": [],
        "roles": {
            "operator": {
                "agent_md": "AGENT.md",
                "context_filter": ["business", "operations"],
            },
            "finance": {
                "agent_md": "AGENT.md",
                "context_filter": ["business"],
            },
        },
        "domain_roles": {
            "finance": {"trust_weight": 0.8},
        },
        "promotion_criteria": {
            "enduring_knowledge": True,
            "operational_ephemera": False,
        },
    }))
    ctx = root / "context"
    ctx.mkdir()
    (ctx / "business.md").write_text("# Business\nExisting business context.\n")
    (ctx / "operations.md").write_text("# Operations\n")
    (root / "staging").mkdir()
    (root / "data" / "sessions").mkdir(parents=True)
    return root


@pytest.fixture
def cm(deployment: Path):
    from simply_connect.context_manager import ContextManager
    return ContextManager(root=deployment)


@pytest.fixture
def sm(deployment: Path):
    from simply_connect.session_manager import SessionManager
    return SessionManager(data_dir=deployment / "data" / "sessions")


# ---------------------------------------------------------------------------
# E2E: Domain role capture → session → curator → staging
# ---------------------------------------------------------------------------

class TestEndToEndCaptureToCommitted:
    def test_domain_role_capture_goes_to_session_not_staging(self, cm, sm):
        """Domain roles write captures to session, not staging."""
        session_id = "finance:user-1"
        sm.init_session(session_id, role="finance")

        # Simulate what SDK runtime does for domain role capture
        capture_data = {
            "summary": "Payment terms are net 45",
            "content": "All enterprise contracts use net 45 payment terms.",
            "category": "business",
        }
        sm.add_turn(session_id, "capture", json.dumps(capture_data))

        # Verify capture is in session
        history = sm.get_history(session_id)
        capture_turns = [t for t in history if t.get("role") == "capture"]
        assert len(capture_turns) == 1
        stored = json.loads(capture_turns[0]["content"])
        assert stored["summary"] == "Payment terms are net 45"

        # Verify nothing in staging
        staging = cm.list_staging(status="unconfirmed")
        assert len(staging) == 0

    def test_curator_promotes_session_capture_to_staging(self, cm, sm):
        """Curator evaluates session captures and promotes worthy ones."""
        from simply_connect.curator import curate_session

        session_id = "finance:user-1"
        sm.init_session(session_id, role="finance")

        # Add captures
        sm.add_turn(session_id, "capture", json.dumps({
            "summary": "Payment terms are net 45",
            "content": "All enterprise contracts use net 45 payment terms.",
            "category": "business",
        }))
        sm.add_turn(session_id, "capture", json.dumps({
            "summary": "Meeting at 3pm",
            "content": "Team meeting scheduled for 3pm tomorrow.",
            "category": "general",
        }))

        # Mock curator model to promote the first, reject the second
        mock_result = {
            "evaluations": [
                {
                    "capture_index": 1,
                    "recommendation": "promote",
                    "reason": "Enduring business rule about payment terms",
                    "confidence": 0.9,
                },
                {
                    "capture_index": 2,
                    "recommendation": "reject",
                    "reason": "Operational ephemera — meeting scheduling",
                    "confidence": 0.95,
                },
            ]
        }

        with patch("simply_connect.curator._call_curator_model", return_value=mock_result):
            result = curate_session(cm, sm, session_id)

        # Verify promotion results
        assert result["captures_evaluated"] == 2
        assert result["promoted"] == 1
        assert result["rejected"] == 1
        assert len(result["entry_ids"]) == 1

        # Verify staging entry was created
        staging = cm.list_staging(status="unconfirmed")
        assert len(staging) == 1
        assert "Payment terms" in staging[0]["summary"]
        assert staging[0]["source"] == f"curator:finance:{session_id}"
        assert staging[0]["curated"] in ("True", True)  # frontmatter stores as string
        assert staging[0]["source_role"] == "curator"  # extracted from source prefix

    def test_admin_review_promotes_to_committed(self, cm, sm):
        """Full pipeline: session → curator → staging → admin review → committed."""
        from simply_connect.curator import curate_session

        session_id = "finance:user-1"
        sm.init_session(session_id, role="finance")

        # Domain role captures to session
        sm.add_turn(session_id, "capture", json.dumps({
            "summary": "Payment terms are net 45",
            "content": "All enterprise contracts use net 45 payment terms.",
            "category": "business",
        }))

        # Curator promotes to staging
        mock_result = {
            "evaluations": [
                {
                    "capture_index": 1,
                    "recommendation": "promote",
                    "reason": "Enduring business rule",
                    "confidence": 0.9,
                },
            ]
        }
        with patch("simply_connect.curator._call_curator_model", return_value=mock_result):
            curate_session(cm, sm, session_id)

        # Verify staging entry exists
        staging = cm.list_staging(status="unconfirmed")
        assert len(staging) == 1
        entry_id = staging[0]["id"]

        # Admin reviews and approves
        success = cm.promote_to_committed(entry_id, reviewed_by="human")
        assert success is True

        # Verify committed context was updated
        committed = cm.load_committed()
        assert "net 45" in committed["business"]

        # Verify staging entry status updated
        entry = cm.get_staging_entry(entry_id)
        assert entry["status"] == "approved"
        assert entry["reviewed_by"] == "human"

    def test_framework_role_bypasses_curator(self, cm, sm):
        """Framework roles (operator) write directly to staging, not session."""
        session_id = "operator:user-1"
        sm.init_session(session_id, role="operator")

        # Framework role capture goes directly to staging (simulating SDK runtime behavior)
        entry_id = cm.create_staging_entry(
            summary="Client prefers plain English",
            content="Client ABC wants all contracts in plain English.",
            category="business",
            source="operator",
        )

        # Verify staging entry exists
        staging = cm.list_staging(status="unconfirmed")
        assert len(staging) == 1
        assert staging[0]["id"] == entry_id
        assert staging[0]["source"] == "operator"
        assert staging[0]["curated"] in ("False", False)  # frontmatter stores as string

        # No captures in session
        history = sm.get_history(session_id)
        capture_turns = [t for t in history if t.get("role") == "capture"]
        assert len(capture_turns) == 0


# ---------------------------------------------------------------------------
# E2E: Multiple sessions curation
# ---------------------------------------------------------------------------

class TestEndToEndMultipleSessions:
    def test_curate_all_sessions(self, cm, sm):
        """Curator processes multiple sessions with captures."""
        from simply_connect.curator import curate_all_sessions

        # Session 1: finance role
        sm.init_session("finance:user-1", role="finance")
        sm.add_turn("finance:user-1", "capture", json.dumps({
            "summary": "Net 45 terms",
            "content": "Enterprise contracts use net 45.",
            "category": "business",
        }))

        # Session 2: different finance user
        sm.init_session("finance:user-2", role="finance")
        sm.add_turn("finance:user-2", "capture", json.dumps({
            "summary": "Audit deadline",
            "content": "Annual audit due by March 31.",
            "category": "business",
        }))

        # Session 3: no captures
        sm.init_session("operator:user-3", role="operator")

        mock_result = {
            "evaluations": [
                {
                    "capture_index": 1,
                    "recommendation": "promote",
                    "reason": "Valid capture",
                    "confidence": 0.85,
                },
            ]
        }

        with patch("simply_connect.curator._call_curator_model", return_value=mock_result):
            results = curate_all_sessions(cm, sm)

        # Both sessions with captures should be processed
        assert len(results) == 2
        total_promoted = sum(r["promoted"] for r in results)
        assert total_promoted == 2

        # Verify both created staging entries
        staging = cm.list_staging(status="unconfirmed")
        assert len(staging) == 2


# ---------------------------------------------------------------------------
# E2E: Dry run mode
# ---------------------------------------------------------------------------

class TestEndToEndDryRun:
    def test_dry_run_does_not_modify_staging(self, cm, sm):
        """Dry run evaluates captures but creates no staging entries."""
        from simply_connect.curator import curate_session

        session_id = "finance:user-1"
        sm.init_session(session_id, role="finance")
        sm.add_turn(session_id, "capture", json.dumps({
            "summary": "Test capture",
            "content": "Test content",
            "category": "business",
        }))

        mock_result = {
            "evaluations": [
                {
                    "capture_index": 1,
                    "recommendation": "promote",
                    "reason": "Valid",
                    "confidence": 0.9,
                },
            ]
        }

        with patch("simply_connect.curator._call_curator_model", return_value=mock_result):
            result = curate_session(cm, sm, session_id, dry_run=True)

        assert result["promoted"] == 1
        assert result["entry_ids"] == []
        staging = cm.list_staging(status="unconfirmed")
        assert len(staging) == 0
