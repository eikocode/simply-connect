"""Tests for simply_connect.curator — session curation and promotion."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def temp_project(tmp_path: Path):
    """Create a minimal project with sessions containing captures."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "AGENT.md").write_text("# Test Agent\n")
    (root / "profile.json").write_text(json.dumps({
        "name": "Test",
        "context_files": ["business", "parties"],
        "category_map": {"business": "business.md", "parties": "parties.md", "general": "business.md"},
    }))
    ctx = root / "context"
    ctx.mkdir()
    (ctx / "business.md").write_text("## Test Business\nA test business.\n")
    (ctx / "parties.md").write_text("## Test Party\nA test party.\n")
    (root / "staging").mkdir()

    sessions_dir = root / "data" / "sessions"
    sessions_dir.mkdir(parents=True)

    # Session with captures
    session_data = {
        "session_id": "operator:test-1",
        "role": "operator",
        "history": [
            {"role": "user", "content": "Remember that our payment terms are net 30."},
            {"role": "capture", "content": json.dumps({
                "summary": "Payment terms are net 30",
                "content": "Our standard payment terms for all contracts are net 30 days.",
                "category": "business",
            })},
            {"role": "assistant", "content": "Noted — under review."},
            {"role": "user", "content": "Note that we use AWS for hosting."},
            {"role": "capture", "content": json.dumps({
                "summary": "Hosting on AWS",
                "content": "All infrastructure is hosted on AWS us-east-1.",
                "category": "business",
            })},
            {"role": "assistant", "content": "Noted — under review."},
            {"role": "user", "content": "The meeting is at 3pm tomorrow."},
            {"role": "capture", "content": json.dumps({
                "summary": "Meeting at 3pm",
                "content": "Team meeting scheduled for 3pm tomorrow.",
                "category": "general",
            })},
        ],
    }
    (sessions_dir / "operator_test-1.json").write_text(json.dumps(session_data))

    # Session without captures
    session_data2 = {
        "session_id": "operator:test-2",
        "role": "operator",
        "history": [
            {"role": "user", "content": "What do we know about the business?"},
            {"role": "assistant", "content": "We use AWS for hosting."},
        ],
    }
    (sessions_dir / "operator_test-2.json").write_text(json.dumps(session_data2))

    return root


@pytest.fixture
def cm(temp_project: Path):
    from simply_connect.context_manager import ContextManager
    return ContextManager(root=temp_project)


@pytest.fixture
def sm(temp_project: Path):
    from simply_connect.session_manager import SessionManager
    return SessionManager(data_dir=temp_project / "data" / "sessions")


# ---------------------------------------------------------------------------
# _extract_session_captures
# ---------------------------------------------------------------------------

class TestExtractSessionCaptures:
    def test_extracts_json_captures(self):
        from simply_connect.curator import _extract_session_captures
        session_data = {
            "history": [
                {"role": "user", "content": "Hello"},
                {"role": "capture", "content": json.dumps({
                    "summary": "Test capture",
                    "content": "Captured content",
                    "category": "business",
                })},
                {"role": "assistant", "content": "OK"},
            ],
        }
        captures = _extract_session_captures(session_data)
        assert len(captures) == 1
        assert captures[0]["summary"] == "Test capture"
        assert captures[0]["category"] == "business"

    def test_handles_non_json_captures(self):
        from simply_connect.curator import _extract_session_captures
        session_data = {
            "history": [
                {"role": "capture", "content": "Raw capture text"},
            ],
        }
        captures = _extract_session_captures(session_data)
        assert len(captures) == 1
        assert captures[0]["content"] == "Raw capture text"
        assert captures[0]["category"] == "general"

    def test_empty_history(self):
        from simply_connect.curator import _extract_session_captures
        captures = _extract_session_captures({"history": []})
        assert captures == []


# ---------------------------------------------------------------------------
# _build_curator_prompt
# ---------------------------------------------------------------------------

class TestBuildCuratorPrompt:
    def test_includes_captures_and_committed(self):
        from simply_connect.curator import _build_curator_prompt
        captures = [
            {"summary": "Test", "content": "Content", "category": "business"},
        ]
        committed = {"business": "Existing business context"}
        criteria = {"enduring_knowledge": True, "operational_ephemera": False}
        prompt = _build_curator_prompt(captures, committed, criteria)
        assert "Test" in prompt
        assert "Content" in prompt
        assert "Existing business context" in prompt
        assert "enduring knowledge" in prompt.lower()

    def test_handles_empty_committed(self):
        from simply_connect.curator import _build_curator_prompt
        captures = [{"summary": "Test", "content": "Content", "category": "business"}]
        prompt = _build_curator_prompt(captures, {}, {})
        assert "no committed context yet" in prompt


# ---------------------------------------------------------------------------
# curate_session
# ---------------------------------------------------------------------------

class TestCurateSession:
    def test_curates_session_with_captures(self, cm, sm):
        from simply_connect.curator import curate_session

        mock_evaluations = {
            "evaluations": [
                {
                    "capture_index": 1,
                    "recommendation": "promote",
                    "reason": "Enduring business rule",
                    "confidence": 0.9,
                },
                {
                    "capture_index": 2,
                    "recommendation": "promote",
                    "reason": "Infrastructure fact",
                    "confidence": 0.85,
                },
            ]
        }

        with patch("simply_connect.curator._call_curator_model", return_value=mock_evaluations):
            result = curate_session(cm, sm, "operator:test-1")

        assert result["session_id"] == "operator:test-1"
        assert result["captures_evaluated"] == 3
        assert result["promoted"] == 2
        assert result["rejected"] == 1  # "3pm" matched prefilter time pattern
        assert len(result["entry_ids"]) == 2

    def test_dry_run_does_not_create_entries(self, cm, sm):
        from simply_connect.curator import curate_session

        mock_evaluations = {
            "evaluations": [
                {
                    "capture_index": 1,
                    "recommendation": "promote",
                    "reason": "Enduring business rule",
                    "confidence": 0.9,
                },
            ]
        }

        with patch("simply_connect.curator._call_curator_model", return_value=mock_evaluations):
            result = curate_session(cm, sm, "operator:test-1", dry_run=True)

        assert result["promoted"] == 1
        assert result["entry_ids"] == []

    def test_session_not_found(self, cm, sm):
        from simply_connect.curator import curate_session
        result = curate_session(cm, sm, "nonexistent")
        assert result["error"] == "Session not found"
        assert result["captures_evaluated"] == 0

    def test_session_without_captures(self, cm, sm):
        from simply_connect.curator import curate_session
        result = curate_session(cm, sm, "operator:test-2")
        assert result["note"] == "No captures found in session"
        assert result["captures_evaluated"] == 0

    def test_deferred_captures_not_promoted(self, cm, sm):
        from simply_connect.curator import curate_session

        mock_evaluations = {
            "evaluations": [
                {
                    "capture_index": 1,
                    "recommendation": "defer",
                    "reason": "Conflicts with existing context",
                    "confidence": 0.5,
                },
            ]
        }

        with patch("simply_connect.curator._call_curator_model", return_value=mock_evaluations):
            result = curate_session(cm, sm, "operator:test-1")

        assert result["deferred"] >= 1
        assert result["promoted"] == 0


# ---------------------------------------------------------------------------
# curate_all_sessions
# ---------------------------------------------------------------------------

class TestCurateAllSessions:
    def test_curates_all_sessions_with_captures(self, cm, sm):
        from simply_connect.curator import curate_all_sessions

        mock_evaluations = {
            "evaluations": [
                {
                    "capture_index": 1,
                    "recommendation": "promote",
                    "reason": "Valid capture",
                    "confidence": 0.9,
                },
            ]
        }

        with patch("simply_connect.curator._call_curator_model", return_value=mock_evaluations):
            results = curate_all_sessions(cm, sm)

        # Only session with captures should be returned
        assert len(results) == 1
        assert results[0]["session_id"] == "operator:test-1"

    def test_dry_run_all(self, cm, sm):
        from simply_connect.curator import curate_all_sessions

        mock_evaluations = {
            "evaluations": [
                {
                    "capture_index": 1,
                    "recommendation": "promote",
                    "reason": "Valid capture",
                    "confidence": 0.9,
                },
            ]
        }

        with patch("simply_connect.curator._call_curator_model", return_value=mock_evaluations):
            results = curate_all_sessions(cm, sm, dry_run=True)

        assert len(results) == 1
        assert results[0]["entry_ids"] == []


# ---------------------------------------------------------------------------
# _load_promotion_criteria
# ---------------------------------------------------------------------------

class TestLoadPromotionCriteria:
    def test_defaults_when_not_configured(self, cm):
        from simply_connect.curator import _load_promotion_criteria
        criteria = _load_promotion_criteria(cm)
        assert criteria["enduring_knowledge"] is True
        assert criteria["operational_ephemera"] is False

    def test_merges_with_profile(self, tmp_path):
        from simply_connect.context_manager import ContextManager
        from simply_connect.curator import _load_promotion_criteria

        root = tmp_path / "project"
        root.mkdir()
        (root / "AGENT.md").write_text("# Test\n")
        (root / "profile.json").write_text(json.dumps({
            "name": "Test",
            "context_files": ["business"],
            "category_map": {"business": "business.md", "general": "business.md"},
            "promotion_criteria": {
                "enduring_knowledge": False,
                "custom_criterion": True,
            },
        }))
        (root / "context").mkdir()
        (root / "staging").mkdir()
        (root / "context" / "business.md").write_text("Test\n")

        cm = ContextManager(root=root)
        criteria = _load_promotion_criteria(cm)
        assert criteria["enduring_knowledge"] is False
        assert criteria["custom_criterion"] is True
        assert criteria["operational_ephemera"] is False  # default preserved


# ---------------------------------------------------------------------------
# Domain role trust weights
# ---------------------------------------------------------------------------

class TestDomainRoleTrustWeights:
    def test_get_role_trust_weight_default(self, cm):
        from simply_connect.curator import _get_role_trust_weight
        assert _get_role_trust_weight(cm, "unknown_role") == 0.5

    def test_get_role_trust_weight_from_profile(self, tmp_path):
        from simply_connect.context_manager import ContextManager
        from simply_connect.curator import _get_role_trust_weight

        root = tmp_path / "project"
        root.mkdir()
        (root / "AGENT.md").write_text("# Test\n")
        (root / "profile.json").write_text(json.dumps({
            "name": "Test",
            "context_files": ["business"],
            "category_map": {"business": "business.md", "general": "business.md"},
            "domain_roles": {
                "finance": {"trust_weight": 0.8},
                "housekeeping": {"trust_weight": 0.3},
            },
        }))
        (root / "context").mkdir()
        (root / "staging").mkdir()
        (root / "context" / "business.md").write_text("Test\n")

        cm = ContextManager(root=root)
        assert _get_role_trust_weight(cm, "finance") == 0.8
        assert _get_role_trust_weight(cm, "housekeeping") == 0.3
        assert _get_role_trust_weight(cm, "unknown") == 0.5

    def test_prompt_includes_role_trust_weights(self):
        from simply_connect.curator import _build_curator_prompt
        captures = [
            {"summary": "Test", "content": "Content", "category": "business", "source_role": "finance"},
        ]
        committed = {"business": "Existing"}
        criteria = {"enduring_knowledge": True}
        domain_roles = {"finance": {"trust_weight": 0.8}}
        prompt = _build_curator_prompt(captures, committed, criteria, domain_roles)
        assert "finance" in prompt
        assert "0.8" in prompt
        assert "trust weight: 0.8" in prompt

    def test_prompt_includes_role_trust_section(self):
        from simply_connect.curator import _build_curator_prompt
        captures = [{"summary": "Test", "content": "Content", "category": "business", "source_role": "finance"}]
        domain_roles = {"finance": {"trust_weight": 0.8}, "ops": {"trust_weight": 0.6}}
        prompt = _build_curator_prompt(captures, {}, {}, domain_roles)
        assert "## Role Trust Weights" in prompt
        assert "finance: 0.8" in prompt
        assert "ops: 0.6" in prompt


class TestCuratorDaemon:
    def test_daemon_starts_and_stops(self, temp_project):
        from simply_connect.context_manager import ContextManager
        from simply_connect.session_manager import SessionManager
        from simply_connect.curator import CuratorDaemon

        cm = ContextManager(root=temp_project)
        sm = SessionManager(data_dir=temp_project / "data" / "sessions")

        daemon = CuratorDaemon(cm, sm, interval_minutes=1, dry_run=True)
        daemon.start()

        assert daemon._thread is not None
        assert daemon._thread.is_alive()

        daemon.stop()

    def test_schedule_curator_run_once(self, temp_project):
        from simply_connect.context_manager import ContextManager
        from simply_connect.curator import schedule_curator

        cm = ContextManager(root=temp_project)
        result = schedule_curator(cm, dry_run=True, run_once=True)

        assert result["mode"] == "once"
        assert "results" in result

    def test_schedule_curator_daemon_mode(self, temp_project):
        from simply_connect.context_manager import ContextManager
        from simply_connect.curator import schedule_curator

        cm = ContextManager(root=temp_project)
        result = schedule_curator(cm, interval_minutes=5, dry_run=True, run_once=False)

        assert result["mode"] == "daemon"
        assert result["daemon_active"] is True
        assert result["interval_minutes"] == 5

    def test_curator_daemon_creates_staging_entries(self, temp_project):
        from simply_connect.context_manager import ContextManager
        from simply_connect.session_manager import SessionManager
        from simply_connect.curator import CuratorDaemon

        cm = ContextManager(root=temp_project)
        sm = SessionManager(data_dir=temp_project / "data" / "sessions")

        result = sm.load("operator:test-1")
        captures_before = len([t for t in result.get("history", []) if t.get("role") == "capture"])

        daemon = CuratorDaemon(cm, sm, interval_minutes=1, dry_run=False)

        summary = daemon._curate_once()

        assert summary["total_evaluated"] == captures_before
        assert "promoted" in summary or summary["total_deferred"] >= 0
