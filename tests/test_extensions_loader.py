"""
Tests for simply-connect/ext_loader.py and ContextManager.active_extensions.

Uses a fake in-memory extension module — no real extension files required.
"""

import json
import sys
import types
import pytest
from pathlib import Path
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_root(tmp_path):
    """Minimal project root with AGENT.md, context/, and staging/."""
    (tmp_path / "AGENT.md").write_text("# Test\n")
    ctx = tmp_path / "context"
    ctx.mkdir()
    for stem in ["business", "parties", "preferences", "contracts"]:
        (ctx / f"{stem}.md").write_text(f"# {stem.capitalize()}\n")
    (tmp_path / "staging").mkdir()
    return tmp_path


@pytest.fixture
def cm_no_extensions(project_root):
    """ContextManager with no active extensions."""
    from simply_connect.context_manager import ContextManager
    return ContextManager(root=project_root)


@pytest.fixture
def cm_with_fake_ext(project_root):
    """ContextManager with 'fakeext' declared in profile.json."""
    profile = {
        "name": "Test",
        "context_files": ["business", "parties", "preferences", "contracts"],
        "category_map": {
            "business": "business.md",
            "parties": "parties.md",
            "preferences": "preferences.md",
            "contracts": "contracts.md",
            "general": "business.md",
        },
        "intake_sources": {},
        "extensions": ["fakeext"],
    }
    (project_root / "profile.json").write_text(json.dumps(profile))
    from simply_connect.context_manager import ContextManager
    return ContextManager(root=project_root)


@pytest.fixture
def fake_extension_module():
    """Register a fake extension module in sys.modules at the domain path."""
    FAKE_TOOLS = [
        {
            "name": "fake_tool",
            "description": "A fake tool for testing.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        }
    ]

    def dispatch(name, args, cm):
        if name == "fake_tool":
            return '{"result": "fake_tool_result"}'
        raise ValueError(f"Unknown tool: {name}")

    mod = types.ModuleType("domains.fakeext.extension.tools")
    mod.TOOLS = FAKE_TOOLS
    mod.dispatch = dispatch

    sys.modules["domains.fakeext.extension.tools"] = mod
    yield mod
    del sys.modules["domains.fakeext.extension.tools"]


# ---------------------------------------------------------------------------
# ContextManager.active_extensions
# ---------------------------------------------------------------------------

class TestActiveExtensions:
    def test_no_extensions_returns_empty_list(self, cm_no_extensions):
        assert cm_no_extensions.active_extensions == []

    def test_extensions_from_profile_json(self, cm_with_fake_ext):
        assert cm_with_fake_ext.active_extensions == ["fakeext"]

    def test_active_extensions_is_list(self, cm_no_extensions):
        assert isinstance(cm_no_extensions.active_extensions, list)

    def test_multiple_extensions(self, project_root):
        profile = {
            "name": "Multi",
            "context_files": ["business"],
            "category_map": {"business": "business.md", "general": "business.md"},
            "intake_sources": {},
            "extensions": ["alpha", "beta"],
        }
        (project_root / "profile.json").write_text(json.dumps(profile))
        from simply_connect.context_manager import ContextManager
        cm = ContextManager(root=project_root)
        assert cm.active_extensions == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# get_all_tools
# ---------------------------------------------------------------------------

class TestGetAllTools:
    def test_no_extensions_returns_empty(self, cm_no_extensions):
        from simply_connect.ext_loader import get_all_tools
        tools = get_all_tools(cm_no_extensions)
        assert tools == []

    def test_returns_tools_from_fake_extension(self, cm_with_fake_ext, fake_extension_module):
        from simply_connect.ext_loader import get_all_tools
        tools = get_all_tools(cm_with_fake_ext)
        assert len(tools) == 1
        assert tools[0]["name"] == "fake_tool"

    def test_unknown_extension_skipped_no_crash(self, project_root):
        """An extension that doesn't exist should be skipped with a warning, not crash."""
        profile = {
            "name": "Test",
            "context_files": ["business"],
            "category_map": {"business": "business.md", "general": "business.md"},
            "intake_sources": {},
            "extensions": ["nonexistent_extension_xyz"],
        }
        (project_root / "profile.json").write_text(json.dumps(profile))
        from simply_connect.context_manager import ContextManager
        cm = ContextManager(root=project_root)
        from simply_connect.ext_loader import get_all_tools
        tools = get_all_tools(cm)  # Should not raise
        assert tools == []

    def test_tools_is_a_list(self, cm_no_extensions):
        from simply_connect.ext_loader import get_all_tools
        result = get_all_tools(cm_no_extensions)
        assert isinstance(result, list)

    def test_legacy_root_extension_layout_is_supported(self, project_root):
        profile = {
            "name": "Legacy",
            "context_files": ["business"],
            "category_map": {"business": "business.md", "general": "business.md"},
            "intake_sources": {},
            "extensions": ["legacyext"],
        }
        (project_root / "profile.json").write_text(json.dumps(profile))
        extension_dir = project_root / "extension"
        extension_dir.mkdir()
        (extension_dir / "tools.py").write_text(
            "TOOLS = [{'name': 'legacy_tool', 'description': 'legacy', 'input_schema': {'type': 'object', 'properties': {}, 'required': []}}]\n"
            "def dispatch(name, args, cm):\n"
            "    if name == 'legacy_tool':\n"
            "        return '{\"result\": \"legacy_ok\"}'\n"
            "    raise ValueError(name)\n",
            encoding="utf-8",
        )
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import get_all_tools

        cm = ContextManager(root=project_root)
        tools = get_all_tools(cm)
        assert [tool["name"] for tool in tools] == ["legacy_tool"]

    def test_legacy_root_extension_layout_supports_relative_imports(self, project_root):
        profile = {
            "name": "Legacy",
            "context_files": ["business"],
            "category_map": {"business": "business.md", "general": "business.md"},
            "intake_sources": {},
            "extensions": ["legacyext"],
        }
        (project_root / "profile.json").write_text(json.dumps(profile))
        extension_dir = project_root / "extension"
        extension_dir.mkdir()
        (extension_dir / "__init__.py").write_text("# legacy package\n", encoding="utf-8")
        (extension_dir / "client.py").write_text(
            "VALUE = 'relative_ok'\n",
            encoding="utf-8",
        )
        (extension_dir / "tools.py").write_text(
            "from .client import VALUE\n"
            "TOOLS = [{'name': 'legacy_tool', 'description': VALUE, 'input_schema': {'type': 'object', 'properties': {}, 'required': []}}]\n"
            "def dispatch(name, args, cm):\n"
            "    if name == 'legacy_tool':\n"
            "        return '{\"result\": \"%s\"}' % VALUE\n"
            "    raise ValueError(name)\n",
            encoding="utf-8",
        )
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import get_all_tools, dispatch_extension_tool

        cm = ContextManager(root=project_root)
        tools = get_all_tools(cm)
        assert tools[0]["description"] == "relative_ok"
        result = dispatch_extension_tool("legacy_tool", {}, cm)
        assert "relative_ok" in result


# ---------------------------------------------------------------------------
# dispatch_extension_tool
# ---------------------------------------------------------------------------

class TestDispatchExtensionTool:
    def test_dispatches_to_fake_extension(self, cm_with_fake_ext, fake_extension_module):
        from simply_connect.ext_loader import dispatch_extension_tool
        result = dispatch_extension_tool("fake_tool", {}, cm_with_fake_ext)
        assert "fake_tool_result" in result

    def test_raises_value_error_for_unknown_tool(self, cm_with_fake_ext, fake_extension_module):
        from simply_connect.ext_loader import dispatch_extension_tool
        with pytest.raises(ValueError, match="No extension handles tool"):
            dispatch_extension_tool("unknown_tool_xyz", {}, cm_with_fake_ext)

    def test_raises_value_error_when_no_extensions(self, cm_no_extensions):
        from simply_connect.ext_loader import dispatch_extension_tool
        with pytest.raises(ValueError):
            dispatch_extension_tool("any_tool", {}, cm_no_extensions)

    def test_dispatch_result_is_string(self, cm_with_fake_ext, fake_extension_module):
        from simply_connect.ext_loader import dispatch_extension_tool
        result = dispatch_extension_tool("fake_tool", {}, cm_with_fake_ext)
        assert isinstance(result, str)

    def test_dispatches_legacy_root_extension_layout(self, project_root):
        profile = {
            "name": "Legacy",
            "context_files": ["business"],
            "category_map": {"business": "business.md", "general": "business.md"},
            "intake_sources": {},
            "extensions": ["legacyext"],
        }
        (project_root / "profile.json").write_text(json.dumps(profile))
        extension_dir = project_root / "extension"
        extension_dir.mkdir()
        (extension_dir / "tools.py").write_text(
            "TOOLS = [{'name': 'legacy_tool', 'description': 'legacy', 'input_schema': {'type': 'object', 'properties': {}, 'required': []}}]\n"
            "def dispatch(name, args, cm):\n"
            "    if name == 'legacy_tool':\n"
            "        return '{\"result\": \"legacy_ok\"}'\n"
            "    raise ValueError(name)\n",
            encoding="utf-8",
        )
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        cm = ContextManager(root=project_root)
        result = dispatch_extension_tool("legacy_tool", {}, cm)
        assert "legacy_ok" in result


class TestMaybeHandleMessage:
    def test_returns_none_when_no_extension_claims_message(self, cm_no_extensions):
        from simply_connect.ext_loader import maybe_handle_message

        assert maybe_handle_message("hello", cm_no_extensions) is None

    def test_dispatches_to_extension_message_handler(self, project_root):
        profile = {
            "name": "Legacy",
            "context_files": ["business"],
            "category_map": {"business": "business.md", "general": "business.md"},
            "intake_sources": {},
            "extensions": ["legacyext"],
        }
        (project_root / "profile.json").write_text(json.dumps(profile))
        extension_dir = project_root / "extension"
        extension_dir.mkdir()
        (extension_dir / "tools.py").write_text(
            "TOOLS = []\n"
            "def dispatch(name, args, cm):\n"
            "    raise ValueError(name)\n"
            "def maybe_handle_message(message, cm, role_name='operator'):\n"
            "    if 'claim' in message:\n"
            "        return 'claimed by extension'\n"
            "    return None\n",
            encoding="utf-8",
        )

        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import maybe_handle_message

        cm = ContextManager(root=project_root)
        assert maybe_handle_message("please claim this", cm) == "claimed by extension"

    def test_passes_history_to_extension_message_handler_when_supported(self, project_root):
        profile = {
            "name": "Legacy",
            "context_files": ["business"],
            "category_map": {"business": "business.md", "general": "business.md"},
            "intake_sources": {},
            "extensions": ["legacyext"],
        }
        (project_root / "profile.json").write_text(json.dumps(profile))
        extension_dir = project_root / "extension"
        extension_dir.mkdir()
        (extension_dir / "tools.py").write_text(
            "TOOLS = []\n"
            "def dispatch(name, args, cm):\n"
            "    raise ValueError(name)\n"
            "def maybe_handle_message(message, cm, role_name='operator', history=None):\n"
            "    if 'claim' in message:\n"
            "        return history[-1]['content'] if history else 'no-history'\n"
            "    return None\n",
            encoding="utf-8",
        )

        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import maybe_handle_message

        cm = ContextManager(root=project_root)
        assert maybe_handle_message(
            "please claim this",
            cm,
            history=[{"role": "assistant", "content": "draft"}],
        ) == "draft"


class TestDecisionPackExtension:
    def test_decision_pack_extension_loads_from_initialized_project(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import get_all_tools

        source_domains = Path("/Users/andrew/backup/work/simply-connect-workspace/simply-connect-domains/domains")
        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()

        admin_cli.cmd_init("decision-pack", target_root, force=False)

        cm = ContextManager(root=target_root)
        tools = get_all_tools(cm)
        tool_names = {tool["name"] for tool in tools}

        assert "decision_pack_create_submission" in tool_names
        assert "decision_pack_build_operator_overview" in tool_names
        assert "decision_pack_set_active_submission" in tool_names
        assert "decision_pack_work_top_blocker" in tool_names
        assert "decision_pack_create_and_assess_submission" in tool_names
        assert "decision_pack_answer_top_diligence_question" in tool_names
        assert "decision_pack_process_pricing_change" in tool_names
        assert "decision_pack_review_material_change_hold" in tool_names

        for module_name in list(sys.modules):
            if module_name == "domains.decision_pack.extension.tools" or module_name.startswith("domains.decision_pack.extension."):
                del sys.modules[module_name]

    def test_decision_pack_extension_dispatches_real_submission_loop(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        cm = ContextManager(root=target_root)
        create_result = json.loads(
            dispatch_extension_tool(
                "decision_pack_create_submission",
                {
                    "source_bundle": {
                        "one_liner": "FluxHalo is an AI copilot for warehouse exception handling for 3PL operators.",
                        "deck_bullets": ["FluxHalo helps 3PL teams resolve warehouse exceptions faster."],
                        "notes": ["Company: FluxHalo"],
                        "metrics": ["3 pilot customers renewed"],
                        "diligence_questions": [],
                    }
                },
                cm,
            )
        )

        assert create_result["submission_id"]

        latest_result = json.loads(dispatch_extension_tool("decision_pack_get_latest_submission", {}, cm))
        assert latest_result["submission_id"] == create_result["submission_id"]

        overview_result = json.loads(dispatch_extension_tool("decision_pack_build_operator_overview", {}, cm))
        assert overview_result["latest_submission"]["submission_id"] == create_result["submission_id"]

        for module_name in list(sys.modules):
            if module_name == "domains.decision_pack.extension.tools" or module_name.startswith("domains.decision_pack.extension."):
                del sys.modules[module_name]

    def test_decision_pack_working_state_tracks_latest_submission_and_next_step(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        cm = ContextManager(root=target_root)
        initial = json.loads(dispatch_extension_tool("decision_pack_get_working_state", {}, cm))
        assert initial["latest_submission"] is None
        assert initial["next_step"]["tool_name"] == "decision_pack_create_submission"

        created = json.loads(
            dispatch_extension_tool(
                "decision_pack_create_submission",
                {
                    "source_bundle": {
                        "one_liner": "FluxHalo is an AI copilot for warehouse exception handling for 3PL operators.",
                        "deck_bullets": ["FluxHalo helps 3PL teams resolve warehouse exceptions faster."],
                        "notes": ["Company: FluxHalo"],
                        "metrics": ["3 pilot customers renewed"],
                        "diligence_questions": [],
                    }
                },
                cm,
            )
        )
        working = json.loads(dispatch_extension_tool("decision_pack_get_working_state", {}, cm))
        assert working["latest_submission"]["submission_id"] == created["submission_id"]
        assert working["latest_version"] == created["version"]
        assert working["top_blocker_task"]["task_id"]
        assert working["next_step"]["surface"] == "founder"
        assert working["active_submission_id"] == created["submission_id"]

        for module_name in list(sys.modules):
            if module_name == "domains.decision_pack.extension.tools" or module_name.startswith("domains.decision_pack.extension."):
                del sys.modules[module_name]

    def test_decision_pack_can_focus_top_blocker_and_ingest_without_ids(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        cm = ContextManager(root=target_root)
        created = json.loads(
            dispatch_extension_tool(
                "decision_pack_create_submission",
                {
                    "source_bundle": {
                        "one_liner": "FluxHalo is an AI copilot for warehouse exception handling for 3PL operators.",
                        "deck_bullets": ["FluxHalo helps 3PL teams resolve warehouse exceptions faster."],
                        "notes": ["Company: FluxHalo"],
                        "metrics": ["3 pilot customers renewed"],
                        "diligence_questions": [],
                    }
                },
                cm,
            )
        )

        attached = json.loads(
            dispatch_extension_tool(
                "decision_pack_attach_investor_questions",
                {
                    "questions": ["Why will this be defensible against fast followers?"],
                    "expected_version": created["version"],
                },
                cm,
            )
        )
        rerun = json.loads(
            dispatch_extension_tool(
                "decision_pack_rerun_underwriting",
                {
                    "expected_version": attached["version"],
                },
                cm,
            )
        )

        focused = json.loads(dispatch_extension_tool("decision_pack_work_top_blocker", {}, cm))
        assert focused["focused_task_id"].startswith("TQ_")

        receipt = json.loads(
            dispatch_extension_tool(
                "decision_pack_ingest_receipt",
                {
                    "summary": "Answer diligence question with receipts: Why will this be defensible against fast followers? evidence summary",
                    "excerpt_texts": [
                        "Three pilot customers renewed after 60 days.",
                        "Average exception resolution time improved by 31%.",
                    ],
                    "expected_version": rerun["version"],
                },
                cm,
            )
        )
        completed = next(task for task in receipt["canonical_pack"]["evidence_plan"]["tasks"] if task["task_id"] == "TQ_1")
        assert completed["status"] == "done"
        assert receipt["working_state"]["focused_task_id"] != "TQ_1" or receipt["working_state"]["focused_task"]["status"] == "done"

        for module_name in list(sys.modules):
            if module_name == "domains.decision_pack.extension.tools" or module_name.startswith("domains.decision_pack.extension."):
                del sys.modules[module_name]

    def test_decision_pack_compound_tools_cover_create_answer_change_and_review(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        cm = ContextManager(root=target_root)

        created = json.loads(
            dispatch_extension_tool(
                "decision_pack_create_and_assess_submission",
                {
                    "source_bundle": {
                        "one_liner": "FluxHalo is an AI copilot for warehouse exception handling for 3PL operators.",
                        "deck_bullets": ["FluxHalo helps 3PL teams resolve warehouse exceptions faster."],
                        "notes": ["Company: FluxHalo"],
                        "metrics": ["3 pilot customers renewed"],
                        "diligence_questions": [],
                    }
                },
                cm,
            )
        )
        submission = created["submission"]
        assert created["working_state"]["active_submission_id"] == submission["submission_id"]

        attached = json.loads(
            dispatch_extension_tool(
                "decision_pack_attach_investor_questions",
                {
                    "questions": ["Why will this be defensible against fast followers?"],
                    "expected_version": submission["version"],
                },
                cm,
            )
        )
        rerun = json.loads(
            dispatch_extension_tool(
                "decision_pack_rerun_underwriting",
                {
                    "expected_version": attached["version"],
                },
                cm,
            )
        )

        answered = json.loads(
            dispatch_extension_tool(
                "decision_pack_answer_top_diligence_question",
                {
                    "summary": "Answer diligence question with receipts: Why will this be defensible against fast followers? evidence summary",
                    "excerpt_texts": [
                        "Three pilot customers renewed after 60 days.",
                        "Average exception resolution time improved by 31%.",
                    ],
                    "expected_version": rerun["version"],
                },
                cm,
            )
        )
        assert answered["focused_task"]["task_id"] == "TQ_1"
        receipt_result = answered["receipt_result"]
        tq_task = next(task for task in receipt_result["canonical_pack"]["evidence_plan"]["tasks"] if task["task_id"] == "TQ_1")
        assert tq_task["status"] == "done"

        processed = json.loads(
            dispatch_extension_tool(
                "decision_pack_process_pricing_change",
                {
                    "summary": "FluxHalo moved from usage-based pricing to annual platform contracts with a one-time implementation fee.",
                    "expected_version": receipt_result["version"],
                },
                cm,
            )
        )
        processed_change = processed["processed_change"]
        assert processed_change["latest_safe_material_change_disclosure"]["headline"].startswith("Material change review active")

        reviewed = json.loads(
            dispatch_extension_tool(
                "decision_pack_review_material_change_hold",
                {
                    "expected_version": processed_change["version"],
                },
                cm,
            )
        )
        assert reviewed["reviewer_disposition"]["status"] == "needs_policy_review"

        for module_name in list(sys.modules):
            if module_name == "domains.decision_pack.extension.tools" or module_name.startswith("domains.decision_pack.extension."):
                del sys.modules[module_name]

    def test_decision_pack_role_guardrail_blocks_reviewer_from_founder_mutation(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        cm = ContextManager(root=target_root)
        response = json.loads(
            dispatch_extension_tool(
                "decision_pack_create_submission",
                {
                    "__session_role": "reviewer",
                    "source_bundle": {
                        "one_liner": "FluxHalo is an AI copilot for warehouse exception handling for 3PL operators.",
                    },
                },
                cm,
            )
        )
        assert response["error"] == "ROLE_ACTION_NOT_ALLOWED:reviewer:decision_pack_create_submission"

        for module_name in list(sys.modules):
            if module_name == "domains.decision_pack.extension.tools" or module_name.startswith("domains.decision_pack.extension."):
                del sys.modules[module_name]

    def test_super_landlord_can_stage_minpaku_handoff(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        result = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_handoff",
                {
                    "source_property_ref": "Harbour Centre, Hung Hom",
                    "availability": "available",
                    "landlord_note": "Only make this available after a fresh cleaning check.",
                },
                cm,
            )
        )

        assert result["ok"] is True
        entry = cm.get_staging_entry(result["entry_id"])
        assert entry is not None
        assert entry["category"] == "minpaku_handoffs"
        assert "Availability: available" in entry["content"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_missing_handoff_fields_return_guided_follow_up(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        result = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_handoff",
                {
                    "source_property_ref": "Harbour Centre, Hung Hom",
                },
                cm,
            )
        )

        assert result["ok"] is False
        assert "availability state" in result["next_prompt"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_review_approval_publishes_handoff_to_minpaku(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli, brain
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool, load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        staged = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_handoff",
                {
                    "source_property_ref": "12 Harbour View Road, Unit A & B",
                    "availability": "available",
                },
                cm,
            )
        )
        entry = cm.get_staging_entry(staged["entry_id"])
        assert entry is not None

        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def create_property(self, payload):
                assert payload["title"] == "12 Harbour View Road, Unit A & B"
                assert payload["hostId"] == "host-sla-1"
                return {"success": True, "property": {"id": "prop-sla-1"}}

        monkeypatch.setattr(ext_module, "SuperLandlordMinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_DEFAULT_HOST_ID", "host-sla-1")
        monkeypatch.setattr(
            brain,
            "review_staging_entry",
            lambda _entry, _committed: {
                "recommendation": "approve",
                "reason": "Looks good",
                "conflicts": [],
                "confidence": 0.99,
            },
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": "a")

        admin_cli.cmd_review(cm, auto=False)

        approved_entry = cm.get_staging_entry(staged["entry_id"])
        assert approved_entry["status"] == "approved"
        committed = cm.load_committed()["minpaku_handoffs"]
        assert "Remote property ID: prop-sla-1" in committed
        assert "Remote host ID: host-sla-1" in committed
        assert "Sync status: published to Minpaku" in committed

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_operator_message_syncs_available_handoff_immediately_before_framework_review(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def create_property(self, payload):
                assert payload["title"] == "12 Harbour View Road, Unit A & B"
                assert payload["hostId"] == "host-sla-1"
                return {"success": True, "property": {"id": "prop-sla-1"}}

        monkeypatch.setattr(ext_module, "SuperLandlordMinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_DEFAULT_HOST_ID", "host-sla-1")

        reply = ext_module.maybe_handle_message(
            "Mark 12 Harbour View Road, Unit A & B available for Minpaku.",
            cm,
            role_name="operator",
        )

        entries = cm.list_staging(status="unconfirmed")
        assert len(entries) == 1
        assert reply is not None
        assert "Made 12 Harbour View Road, Unit A & B available in Minpaku immediately as prop-sla-1" in reply
        assert "Staged and synced — run `sc-admin review` to commit." in reply
        assert "Remote property ID: prop-sla-1" in entries[0]["content"]
        assert "Remote host ID: host-sla-1" in entries[0]["content"]
        assert "Sync status: published to Minpaku (pending framework review)" in entries[0]["content"]

        review = ext_module.review_staging_entry(cm, entries[0])
        assert review["recommendation"] == "approve"
        assert "already been synced to Minpaku" in review["reason"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_operator_message_accepts_as_available_in_minpaku_variant(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def create_property(self, payload):
                assert payload["title"] == "12 Harbour View Road, Unit A & B"
                return {"success": True, "property": {"id": "prop-sla-variant"}}

        monkeypatch.setattr(ext_module, "SuperLandlordMinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_DEFAULT_HOST_ID", "host-sla-1")

        reply = ext_module.maybe_handle_message(
            "mark Unit A as available in minpaku",
            cm,
            role_name="operator",
        )

        entries = cm.list_staging(status="unconfirmed")
        assert len(entries) == 1
        assert reply is not None
        assert "12 Harbour View Road, Unit A & B" in reply
        assert "prop-sla-variant" in reply
        assert "Staged and synced — run `sc-admin review` to commit." in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_handoff_returns_explicit_error_when_immediate_sync_fails(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        monkeypatch.setattr(
            ext_module,
            "_stage_immediate_handoff",
            lambda cm, source_property_ref, availability, landlord_note=None: (_ for _ in ()).throw(RuntimeError("Minpaku 422")),
        )

        reply = ext_module.maybe_handle_message("mark 12 Harbour View Road available for Minpaku", cm, role_name="operator")

        assert "immediate Minpaku handoff failed" in reply
        assert "Minpaku 422" in reply
        assert cm.list_staging(status="unconfirmed") == []

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_outstanding_debit_notes_reports_pending_staged_entry(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "debit_notes.md").write_text(
            "# Debit Notes\n\n## Issued\n\n_None yet._\n\n## Next reference number: DN-2026-001\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        entry_id = cm.create_staging_entry(
            summary="Issued debit note DN-2026-001 for Sarah Wong (Unit A), HKD 744.00",
            content=(
                "Issued debit note `DN-2026-001` for Sarah Wong (Unit A), HKD 744.00, "
                "Jan-Feb 2026 water charge.\n"
                "Next reference number advanced to `DN-2026-002`.\n"
            ),
            category="debit_notes",
            source="operator",
        )

        ext_module = load_active_extensions(cm)[0]["module"]
        reply = ext_module.maybe_handle_message(
            "Show outstanding debit notes for Unit A",
            cm,
            role_name="operator",
        )

        assert reply is not None
        assert "There are no committed outstanding debit notes for Unit A yet." in reply
        assert "Pending staged debit-note updates for Unit A:" in reply
        assert f"staging entry `{entry_id}`" in reply
        assert "sc-admin review" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_outstanding_debit_notes_returns_none_when_no_match_exists(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "debit_notes.md").write_text(
            "# Debit Notes\n\n## Issued\n\n_None yet._\n\n## Next reference number: DN-2026-001\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]
        reply = ext_module.maybe_handle_message(
            "Show outstanding debit notes for Unit Z",
            cm,
            role_name="operator",
        )

        assert reply is None

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_outstanding_debit_notes_finds_generic_staged_debit_note_update(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "debit_notes.md").write_text(
            "# Debit Notes\n\n## Issued\n\n_None yet._\n\n## Next reference number: DN-2026-001\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        entry_id = cm.create_staging_entry(
            summary="Recorded debit note DN-2026-001 for Sarah Wong (Unit A)",
            content=(
                "Issued debit note `DN-2026-001` for Sarah Wong (Unit A), amount `HKD 744.00`.\n"
                "Next debit note reference advanced to `DN-2026-002`.\n"
            ),
            category="general",
            source="operator",
        )

        ext_module = load_active_extensions(cm)[0]["module"]
        reply = ext_module.maybe_handle_message(
            "Show outstanding debit notes for Unit A",
            cm,
            role_name="operator",
        )

        assert reply is not None
        assert "Pending staged debit-note updates for Unit A:" in reply
        assert f"staging entry `{entry_id}`" in reply
        assert "category `debit_notes`" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_can_record_latest_debit_note_draft_into_debit_notes_staging(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]
        history = [
            {
                "role": "assistant",
                "content": (
                    "**Debit Note Draft**\n"
                    "- `Reference`: DN-2026-001\n"
                    "- `Issue date`: 2026-03-30\n"
                    "- `Property`: 12 Harbour View Road, Unit A & B\n"
                    "- `Billed to`: Sarah Wong (Unit A)\n"
                    "- `Utility`: Water (Water Supplies Department, Account WSB-2024-8821)\n"
                    "- `Billing period`: Jan-Feb 2026\n"
                    "- `Amount due from Unit A`: **HKD 744.00**\n"
                ),
            }
        ]

        reply = ext_module.maybe_handle_message(
            "record the debit note as issued and stage only the context update for framework approval",
            cm,
            role_name="operator",
            history=history,
        )

        entries = cm.list_staging(status="unconfirmed")
        assert reply is not None
        assert len(entries) == 1
        assert entries[0]["category"] == "debit_notes"
        assert "## DN-2026-001" in entries[0]["content"]
        assert "- Amount: HKD 744.00" in entries[0]["content"]
        assert "## Next reference number: DN-2026-002" in entries[0]["content"]
        assert "Mark `DN-2026-001` as issued" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_can_record_generated_fenced_debit_note_into_debit_notes_staging(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]
        history = [
            {
                "role": "assistant",
                "content": (
                    "Generated — here is the debit note draft for Unit A based on the latest committed water bill.\n\n"
                    "```text\n"
                    "DEBIT NOTE\n\n"
                    "Reference No.: DN-2026-001\n"
                    "Date: 2026-03-30\n\n"
                    "To: Sarah Wong\n"
                    "Unit: Unit A\n\n"
                    "Property / Service Account:\n"
                    "Harbour View Road\n"
                    "Water Supplies Department\n"
                    "Account No.: WSB-2024-8821\n\n"
                    "Billing Period: Jan-Feb 2026\n"
                    "Utility: Water\n\n"
                    "Amount Due: HKD 744.00\n"
                    "```\n"
                ),
            }
        ]

        reply = ext_module.maybe_handle_message(
            "record the debit note as issued and stage only the context update for framework approval",
            cm,
            role_name="operator",
            history=history,
        )

        entries = cm.list_staging(status="unconfirmed")
        assert reply is not None
        assert len(entries) == 1
        assert entries[0]["category"] == "debit_notes"
        assert "## DN-2026-001" in entries[0]["content"]
        assert "- Tenant: Sarah Wong" in entries[0]["content"]
        assert "- Amount: HKD 744.00" in entries[0]["content"]
        assert "Mark `DN-2026-001` as issued" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_record_debit_note_returns_none_when_no_recent_draft_matches(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        reply = ext_module.maybe_handle_message(
            "record the debit note as issued and stage only the context update for framework approval",
            cm,
            role_name="operator",
            history=[{"role": "assistant", "content": "No debit note draft was created here."}],
        )

        assert reply is None

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_record_debit_note_returns_explicit_error_when_staging_write_fails(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        monkeypatch.setattr(ext_module, "_stage_issued_debit_note_from_history", lambda cm, history: (_ for _ in ()).throw(RuntimeError("disk full")))

        reply = ext_module.maybe_handle_message(
            "record the debit note as issued and stage only the context update for framework approval",
            cm,
            role_name="operator",
            history=[
                {
                    "role": "assistant",
                    "content": (
                        "```text\n"
                        "DEBIT NOTE\n\n"
                        "Reference No.: DN-2026-001\n"
                        "Date: 2026-03-30\n\n"
                        "To: Sarah Wong\n"
                        "Property / Service Account:\n"
                        "Harbour View Road\n"
                        "Billing Period: Jan-Feb 2026\n"
                        "Utility: Water\n"
                        "Amount Due: HKD 744.00\n"
                        "```\n"
                    ),
                }
            ],
        )

        assert "staging the issued-note update failed" in reply
        assert "disk full" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_can_extract_property_from_latest_utility_bill_and_stage_it(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        cm = ContextManager(root=target_root)
        cm.create_staging_entry(
            summary="CLP electricity bill for Flat 6, 7/F Tower 2 Harbour Centre - Mar 2026",
            content=(
                "- Service address: Flat 6, 7/F Tower 2, Harbour Centre, 8 Hok Cheung Street, Hung Hom, Kowloon\n"
                "- Total due: HKD 2,150.00\n"
            ),
            category="utilities",
            source="ingest:electricbill.jpeg",
        )

        ext_module = load_active_extensions(cm)[0]["module"]
        extract_reply = ext_module.maybe_handle_message(
            "extract the property from the utility bill",
            cm,
            role_name="operator",
            history=None,
        )

        assert extract_reply is not None
        assert "Flat 6, 7/F Tower 2" in extract_reply
        assert "Harbour Centre" in extract_reply

        history = [{"role": "assistant", "content": extract_reply}]
        capture_reply = ext_module.maybe_handle_message(
            "capture that extracted property record",
            cm,
            role_name="operator",
            history=history,
        )

        entries = [entry for entry in cm.list_staging(status="unconfirmed") if entry["category"] == "properties"]
        assert capture_reply is not None
        assert len(entries) == 1
        assert "Flat 6, 7/F Tower 2, Harbour Centre" in entries[0]["content"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_extract_property_returns_none_when_no_bill_candidate_exists(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        reply = ext_module.maybe_handle_message(
            "extract property from the bill to add it",
            cm,
            role_name="operator",
            history=[{"role": "assistant", "content": "No bill was discussed here."}],
        )

        assert reply is None

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_capture_property_returns_explicit_error_when_staging_fails(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        monkeypatch.setattr(
            ext_module,
            "_extract_property_candidate_from_history_or_staging",
            lambda cm, history: {
                "property_ref": "Flat 6, 7/F Tower 2, Harbour Centre",
                "unit": "Flat 6, 7/F Tower 2",
                "building": "Harbour Centre",
                "full_address": "Flat 6, 7/F Tower 2, Harbour Centre, 8 Hok Cheung Street, Hung Hom, Kowloon",
            },
        )
        monkeypatch.setattr(ext_module, "_stage_property_candidate", lambda cm, candidate: (_ for _ in ()).throw(RuntimeError("staging unavailable")))

        reply = ext_module.maybe_handle_message(
            "capture that extracted property record",
            cm,
            role_name="operator",
            history=[{"role": "assistant", "content": "Extracted property from the utility bill"}],
        )

        assert "property candidate from the utility bill" in reply
        assert "staging it failed" in reply
        assert "staging unavailable" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_capture_property_from_bill_returns_none_when_no_candidate_exists(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        reply = ext_module.maybe_handle_message(
            "capture that property from the bill",
            cm,
            role_name="operator",
            history=[{"role": "assistant", "content": "No property candidate here."}],
        )

        assert reply is None

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_show_all_properties_includes_pending_staged_properties(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "properties.md").write_text("# Properties\n\n", encoding="utf-8")

        cm = ContextManager(root=target_root)
        cm.create_staging_entry(
            summary="Property record for Flat 6, 7/F Tower 2, Harbour Centre",
            content=(
                "## Flat 6, 7/F Tower 2, Harbour Centre\n"
                "- Source: utility bill extraction\n"
                "- Unit: Flat 6, 7/F Tower 2\n"
                "- Building: Harbour Centre\n"
                "- Full service address: Flat 6, 7/F Tower 2, Harbour Centre, 8 Hok Cheung Street, Hung Hom, Kowloon\n"
            ),
            category="properties",
            source="operator",
        )

        ext_module = load_active_extensions(cm)[0]["module"]
        reply = ext_module.maybe_handle_message(
            "show all properties",
            cm,
            role_name="operator",
        )

        assert reply is not None
        assert "Active properties in the operator working set:" in reply
        assert "`Flat 6, 7/F Tower 2, Harbour Centre` *(pending framework approval)*" in reply
        assert "Pending staged properties:" in reply
        assert "Flat 6, 7/F Tower 2, Harbour Centre" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_remove_property_hides_it_from_operator_working_set(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "properties.md").write_text(
            "# Properties\n\n"
            "## 12 Harbour View Road, Unit A & B\n\n"
            "- Owner: Andrew Chan\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        removal_reply = ext_module.maybe_handle_message(
            "remove 12 Harbour View Road",
            cm,
            role_name="operator",
            history=None,
        )
        assert removal_reply is not None
        assert "staged the removal" in removal_reply.lower()

        reply = ext_module.maybe_handle_message(
            "show all properties",
            cm,
            role_name="operator",
            history=None,
        )

        assert reply is not None
        assert "There are currently no active properties in the operator working set." in reply
        assert "Pending staged properties:" not in reply
        assert "Pending staged property removals:" in reply
        assert "12 Harbour View Road" in reply

        pending = cm.list_staging(status="unconfirmed")
        assert pending
        assert pending[-1]["category"] == "properties"
        assert "## Property Removal Request" in pending[-1]["content"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_blocks_debit_note_generation_for_pending_removed_property(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "properties.md").write_text(
            "# Properties\n\n"
            "## 12 Harbour View Road, Unit A & B\n\n"
            "- Owner: Andrew Chan\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        removal_reply = ext_module.maybe_handle_message(
            "remove 12 Harbour View",
            cm,
            role_name="operator",
            history=None,
        )
        assert removal_reply is not None
        assert "staged the removal" in removal_reply.lower()

        reply = ext_module.maybe_handle_message(
            "Generate a debit note for 12 Harbour View",
            cm,
            role_name="operator",
            history=None,
        )

        assert reply is not None
        assert "can't generate a debit note" in reply.lower()
        assert "pending staged removal" in reply.lower()
        assert "sc-admin review" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_blocks_incomplete_debit_note_request_when_no_active_properties_remain(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "properties.md").write_text(
            "# Properties\n\n"
            "## 12 Harbour View Road, Unit A & B\n\n"
            "- Owner: Andrew Chan\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        removal_reply = ext_module.maybe_handle_message(
            "remove 12 Harbour View",
            cm,
            role_name="operator",
            history=None,
        )
        assert removal_reply is not None

        reply = ext_module.maybe_handle_message(
            "Generate a debit note for",
            cm,
            role_name="operator",
            history=None,
        )

        assert reply is not None
        assert "no active properties in the operator working set" in reply.lower()
        assert "pending staged property removals:" in reply.lower()
        assert "sc-admin review" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_approved_property_removal_cleans_committed_properties_file(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        properties_path = target_root / "context" / "properties.md"
        properties_path.write_text(
            "# Properties\n\n"
            "## 12 Harbour View Road, Unit A & B\n\n"
            "- Owner: Andrew Chan\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        removal_reply = ext_module.maybe_handle_message(
            "remove 12 Harbour View",
            cm,
            role_name="operator",
            history=None,
        )
        assert removal_reply is not None

        pending = cm.list_staging(status="unconfirmed")
        entry = pending[-1]
        assert "## Property Removal Request" in entry["content"]

        assert cm.promote_to_committed(entry["id"], reviewed_by="human") is True
        result = ext_module.on_staging_approved(cm, entry)

        committed = properties_path.read_text(encoding="utf-8")
        assert result is not None
        assert result["ok"] is True
        assert "Committed property removal applied." in result["message"]
        assert "12 Harbour View Road, Unit A & B" not in committed
        assert "## Property Removal Request" not in committed

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_show_all_properties_ignores_committed_removal_request_section(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "properties.md").write_text(
            "# Properties\n\n"
            "## 12 Harbour View Road, Unit A & B\n\n"
            "- Owner: Andrew Chan\n\n"
            "## Property Removal Request\n\n"
            "- Property: 12 Harbour View Road, Unit A & B\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        reply = ext_module.maybe_handle_message(
            "show all properties",
            cm,
            role_name="operator",
            history=None,
        )

        assert reply is not None
        assert "12 Harbour View Road, Unit A & B" in reply
        assert "`Property Removal Request`" not in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_ingest_auto_stages_property_candidate_from_utility_bill(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        cm = ContextManager(root=target_root)
        bill_path = target_root / "electricbill.jpeg"
        bill_path.write_text("fake image payload", encoding="utf-8")

        def fake_ingest_document(filepath, committed, profile):
            assert filepath == bill_path
            return {
                "success": True,
                "extractions": [
                    {
                        "summary": "CLP electricity bill for Flat 6, 7/F Tower 2, Harbour Centre - Mar 2026",
                        "content": (
                            "- Utility provider: CLP\n"
                            "- Unit: Flat 6, 7/F Tower 2\n"
                            "- Building: Harbour Centre\n"
                            "- Full service address: Flat 6, 7/F Tower 2, Harbour Centre, 8 Hok Cheung Street, Hung Hom, Kowloon\n"
                        ),
                        "category": "utilities",
                    }
                ],
            }

        monkeypatch.setattr("simply_connect.ingestion.ingest_document", fake_ingest_document)

        result = admin_cli.ingest_to_staging(cm, bill_path)

        assert result["ok"] is True
        assert [item["category"] for item in result["entries"]] == ["utilities", "properties"]
        assert result["post_ingest"]
        assert "Also staged a property candidate" in result["post_ingest"][0]["message"]

        staged_properties = [entry for entry in cm.list_staging(status="unconfirmed") if entry["category"] == "properties"]
        assert len(staged_properties) == 1
        assert "Flat 6, 7/F Tower 2, Harbour Centre" in staged_properties[0]["content"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_remove_property_returns_none_when_reference_is_ambiguous(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "properties.md").write_text(
            "# Properties\n\n"
            "## 12 Harbour View Road, Unit A\n\n"
            "- Owner: Andrew Chan\n\n"
            "## 12 Harbour View Road, Unit B\n\n"
            "- Owner: Andrew Chan\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        reply = ext_module.maybe_handle_message(
            "remove 12 Harbour View Road",
            cm,
            role_name="operator",
            history=None,
        )

        assert reply is None
        assert cm.list_staging(status="unconfirmed") == []

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_minpaku_handoff_uses_unique_partial_property_match(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "properties.md").write_text(
            "# Properties\n\n"
            "## 12 Harbour View Road, Unit A & B\n\n"
            "- Owner: Andrew Chan\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        def fake_stage_immediate_handoff(context_manager, source_property_ref, availability, landlord_note):
            assert context_manager is cm
            assert source_property_ref == "12 Harbour View Road, Unit A & B"
            assert availability == "available"
            assert landlord_note is None
            return {
                "ok": True,
                "entry_id": "entry-123",
                "source_property_ref": source_property_ref,
                "availability": availability,
                "property_id": "prop-123",
                "host_id": "host-123",
                "message": "published immediately",
            }

        monkeypatch.setattr(ext_module, "_stage_immediate_handoff", fake_stage_immediate_handoff)

        reply = ext_module.maybe_handle_message(
            "mark 12 Harbour View Road available in minpaku",
            cm,
            role_name="operator",
            history=None,
        )

        assert reply is not None
        assert "published immediately" in reply
        assert "12 Harbour View Road, Unit A & B" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_review_allows_new_unit_scope_without_remote_sync_fields(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool, load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        (target_root / "context" / "minpaku_handoffs.md").write_text(
            "# Minpaku Handoffs\n\n"
            "## Minpaku Handoff — Unit A & B\n"
            "- Availability: available\n"
            "- Remote property ID: prop-duplex-1\n"
            "- Remote host ID: host-sla-1\n"
            "- Sync status: published to Minpaku\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        staged = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_handoff",
                {
                    "source_property_ref": "Unit A",
                    "availability": "available",
                },
                cm,
            )
        )
        entry = cm.get_staging_entry(staged["entry_id"])
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def search_properties(self, query):
                assert query == "Unit A"
                return []

        monkeypatch.setattr(ext_module, "SuperLandlordMinpakuClient", FakeClient)

        review = ext_module.review_staging_entry(cm, entry)

        assert review["recommendation"] == "approve"
        assert "expected to be absent before approval" in review["reason"]
        assert "different unit/scope" in review["reason"]
        assert review["conflicts"] == []

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_reuses_committed_minpaku_host_id_before_env_or_synthetic(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        handoffs_path = target_root / "context" / "minpaku_handoffs.md"
        handoffs_path.write_text(
            "# Minpaku Handoffs\n\n"
            "## Minpaku Handoff — Existing Property\n"
            "- Availability: available\n"
            "- Remote property ID: prop-existing-1\n"
            "- Remote host ID: host-linked-9\n"
            "- Sync status: published to Minpaku\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        monkeypatch.setenv("MINPAKU_DEFAULT_HOST_ID", "host-env-1")

        payload = ext_module._build_remote_payload(
            cm,
            {
                "source_property_ref": "12 Harbour View Road, Unit A & B",
                "availability": "available",
                "landlord_note": None,
            },
        )

        assert payload["hostId"] == "host-linked-9"

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_unavailable_handoff_reuses_committed_remote_property_linkage(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        handoffs_path = target_root / "context" / "minpaku_handoffs.md"
        handoffs_path.write_text(
            "# Minpaku Handoffs\n\n"
            "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
            "- Availability: available\n"
            "- Remote property ID: prop-sla-1\n"
            "- Remote host ID: host-sla-1\n"
            "- Sync status: published to Minpaku\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        entry_id = cm.create_staging_entry(
            summary="Minpaku handoff for 12 Harbour View Road, Unit A & B (unavailable)",
            content=(
                "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
                "- Availability: unavailable\n\n"
                "This handoff indicates landlord intent only.\n"
                "Listing title, nightly price, max guests, amenities, and guest-facing rules must be handled in the Minpaku deployment.\n"
            ),
            category="minpaku_handoffs",
            source="operator",
        )
        entry = cm.get_staging_entry(entry_id)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def delete_property(self, property_id, host_id=None):
                assert property_id == "prop-sla-1"
                assert host_id == "host-sla-1"
                return {"status": "deleted", "id": property_id}

        monkeypatch.setattr(ext_module, "SuperLandlordMinpakuClient", FakeClient)

        result = ext_module.on_staging_approved(cm, entry)

        assert result["ok"] is True
        assert "unlisted from Minpaku" in result["message"]
        committed = cm.load_committed()["minpaku_handoffs"]
        assert "Remote property ID: prop-sla-1" in committed
        assert "Remote host ID: host-sla-1" in committed
        assert "Sync status: unlisted from Minpaku" in committed

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_unavailable_handoff_treats_404_as_already_absent(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        handoffs_path = target_root / "context" / "minpaku_handoffs.md"
        handoffs_path.write_text(
            "# Minpaku Handoffs\n\n"
            "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
            "- Availability: available\n"
            "- Remote property ID: prop-sla-1\n"
            "- Remote host ID: host-sla-1\n"
            "- Sync status: published to Minpaku\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        entry_id = cm.create_staging_entry(
            summary="Minpaku handoff for 12 Harbour View Road, Unit A & B (unavailable)",
            content=(
                "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
                "- Availability: unavailable\n\n"
                "This handoff indicates landlord intent only.\n"
                "Listing title, nightly price, max guests, amenities, and guest-facing rules must be handled in the Minpaku deployment.\n"
            ),
            category="minpaku_handoffs",
            source="operator",
        )
        entry = cm.get_staging_entry(entry_id)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def delete_property(self, property_id, host_id=None):
                raise RuntimeError("Client error '404 Not Found' for url 'http://example.test/acp/properties/prop-sla-1'")

        monkeypatch.setattr(ext_module, "SuperLandlordMinpakuClient", FakeClient)

        result = ext_module.on_staging_approved(cm, entry)

        assert result["ok"] is True
        assert "already absent from Minpaku" in result["message"]
        committed = cm.load_committed()["minpaku_handoffs"]
        assert "Sync status: already absent from Minpaku" in committed

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_unavailable_handoff_prefers_latest_committed_linkage(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        handoffs_path = target_root / "context" / "minpaku_handoffs.md"
        handoffs_path.write_text(
            "# Minpaku Handoffs\n\n"
            "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
            "- Availability: available\n"
            "- Remote property ID: prop-old-1\n"
            "- Remote host ID: host-sla-1\n"
            "- Sync status: published to Minpaku\n\n"
            "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
            "- Availability: available\n"
            "- Remote property ID: prop-new-1\n"
            "- Remote host ID: host-sla-1\n"
            "- Sync status: published to Minpaku\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        entry_id = cm.create_staging_entry(
            summary="Minpaku handoff for 12 Harbour View Road, Unit A & B (unavailable)",
            content=(
                "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
                "- Availability: unavailable\n\n"
                "This handoff indicates landlord intent only.\n"
                "Listing title, nightly price, max guests, amenities, and guest-facing rules must be handled in the Minpaku deployment.\n"
            ),
            category="minpaku_handoffs",
            source="operator",
        )
        entry = cm.get_staging_entry(entry_id)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def delete_property(self, property_id, host_id=None):
                assert property_id == "prop-new-1"
                assert host_id == "host-sla-1"
                return {"status": "deleted", "id": property_id}

        monkeypatch.setattr(ext_module, "SuperLandlordMinpakuClient", FakeClient)

        result = ext_module.on_staging_approved(cm, entry)

        assert result["ok"] is True
        assert "unlisted from Minpaku" in result["message"]
        committed = cm.load_committed()["minpaku_handoffs"]
        assert "Remote property ID: prop-new-1" in committed

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_unavailable_handoff_recovers_live_property_after_stale_404(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        handoffs_path = target_root / "context" / "minpaku_handoffs.md"
        handoffs_path.write_text(
            "# Minpaku Handoffs\n\n"
            "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
            "- Availability: available\n"
            "- Remote property ID: prop-old-1\n"
            "- Remote host ID: host-sla-1\n"
            "- Sync status: published to Minpaku\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        entry_id = cm.create_staging_entry(
            summary="Minpaku handoff for 12 Harbour View Road, Unit A & B (unavailable)",
            content=(
                "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
                "- Availability: unavailable\n\n"
                "This handoff indicates landlord intent only.\n"
                "Listing title, nightly price, max guests, amenities, and guest-facing rules must be handled in the Minpaku deployment.\n"
            ),
            category="minpaku_handoffs",
            source="operator",
        )
        entry = cm.get_staging_entry(entry_id)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def __init__(self):
                self.calls = []

            def delete_property(self, property_id, host_id=None):
                self.calls.append((property_id, host_id))
                if property_id == "prop-old-1":
                    raise RuntimeError("Client error '404 Not Found' for url 'http://example.test/acp/properties/prop-old-1'")
                assert property_id == "prop-live-9"
                assert host_id == "host-sla-1"
                return {"status": "deleted", "id": property_id}

            def search_properties(self, query):
                assert "12 Harbour View Road" in query
                return [{"id": "prop-live-9", "title": "12 Harbour View Road, Unit A & B", "hostId": "host-sla-1"}]

        monkeypatch.setattr(ext_module, "SuperLandlordMinpakuClient", FakeClient)

        result = ext_module.on_staging_approved(cm, entry)

        assert result["ok"] is True
        assert "recovered remote property id and unlisted from Minpaku" in result["message"]
        committed = cm.load_committed()["minpaku_handoffs"]
        assert "Remote property ID: prop-live-9" in committed
        assert "Sync status: recovered remote property id and unlisted from Minpaku" in committed

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_super_landlord_unavailable_handoff_falls_back_to_latest_linked_record_when_latest_state_has_no_ids(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        handoffs_path = target_root / "context" / "minpaku_handoffs.md"
        handoffs_path.write_text(
            "# Minpaku Handoffs\n\n"
            "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
            "- Availability: available\n"
            "- Remote property ID: prop-live-9\n"
            "- Remote host ID: host-sla-1\n"
            "- Sync status: published to Minpaku\n\n"
            "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
            "- Availability: unavailable\n"
            "- Sync status: marked unavailable locally (no remote property id recorded)\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        entry_id = cm.create_staging_entry(
            summary="Minpaku handoff for 12 Harbour View Road, Unit A & B (unavailable)",
            content=(
                "## Minpaku Handoff — 12 Harbour View Road, Unit A & B\n"
                "- Availability: unavailable\n\n"
                "This handoff indicates landlord intent only.\n"
                "Listing title, nightly price, max guests, amenities, and guest-facing rules must be handled in the Minpaku deployment.\n"
            ),
            category="minpaku_handoffs",
            source="operator",
        )
        entry = cm.get_staging_entry(entry_id)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def delete_property(self, property_id, host_id=None):
                assert property_id == "prop-live-9"
                assert host_id == "host-sla-1"
                return {"status": "deleted", "id": property_id}

        monkeypatch.setattr(ext_module, "SuperLandlordMinpakuClient", FakeClient)

        result = ext_module.on_staging_approved(cm, entry)

        assert result["ok"] is True
        assert "unlisted from Minpaku" in result["message"]
        committed = cm.load_committed()["minpaku_handoffs"]
        assert "Remote property ID: prop-live-9" in committed
        assert "Remote host ID: host-sla-1" in committed

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_super-landlord.tools" or module_name.startswith("_sc_extension_super-landlord."):
                del sys.modules[module_name]

    def test_minpaku_can_stage_and_publish_minpaku_listing(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool, load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class DraftClient:
            def create_listing(self, payload):
                return {"id": "list-draft-1", "propertyId": payload["propertyId"], "platform": payload["platform"]}

        monkeypatch.setattr(ext_module, "MinpakuClient", DraftClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        staged = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_listing",
                {
                    "property_id": "prop-sla-1",
                    "source_property_ref": "Harbour Centre, Hung Hom",
                    "platform": "direct",
                    "title": "Harbour Centre Short Stay",
                    "nightly_price": 1200,
                    "currency": "HKD",
                    "contact": "ops@example.com",
                },
                cm,
            )
        )
        assert staged["ok"] is True
        entry = cm.get_staging_entry(staged["entry_id"])
        assert entry is not None
        assert entry["category"] == "listing_publications"
        assert cm.update_staging_status(staged["entry_id"], "approved", "human") is True

        class FakeClient:
            def create_listing(self, payload):
                assert payload["title"] == "Harbour Centre Short Stay"
                assert payload["propertyId"] == "prop-sla-1"
                assert payload["platform"] == "direct"
                return {"id": "list-sla-1", "propertyId": "prop-sla-1", "platform": "direct"}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        admin_cli.cmd_publish_minpaku(cm, entry_id=staged["entry_id"])
        out = capsys.readouterr().out
        assert "Published Minpaku listing" in out

        refreshed = cm.get_staging_entry(staged["entry_id"])
        assert refreshed["status"] == "published"
        listings_text = (target_root / "context" / "listing_publications.md").read_text(encoding="utf-8")
        assert "list-sla-1" in listings_text
        assert "prop-sla-1" in listings_text
        assert "Harbour Centre Short Stay" in listings_text

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_missing_listing_fields_return_guided_follow_up(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        cm = ContextManager(root=target_root)
        result = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_listing",
                {
                    "source_property_ref": "Harbour Centre, Hung Hom",
                },
                cm,
            )
        )

        assert result["ok"] is False
        assert "title" in result["next_prompt"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_identical_committed_listing_is_not_restaged(self, tmp_path):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        (target_root / "context" / "listing_publications.md").write_text(
            "# Listing Publications\n\n"
            "## 12 Harbour View Road - Duplex Units A & B\n"
            "- Remote listing ID: list-existing-1\n"
            "- Property ID: prop-existing-1\n"
            "- Source property ref: prop-1774765573066-y7by53f0b\n"
            "- Platform: direct\n"
            "- Published at: 2026-03-29T06:44:00+00:00\n"
            "- Nightly price override: 1800 HKD\n"
            "- Status: active\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        result = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_listing",
                {
                    "property_id": "prop-existing-1",
                    "source_property_ref": "prop-1774765573066-y7by53f0b",
                    "platform": "direct",
                    "title": "12 Harbour View Road - Duplex Units A & B",
                    "nightly_price": 1800,
                    "currency": "HKD",
                },
                cm,
            )
        )

        assert result["ok"] is True
        assert result["staged"] is False
        assert result["already_exists"] is True
        assert result["remote_listing_id"] == "list-existing-1"
        assert cm.list_staging(status="unconfirmed") == []

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_can_update_existing_listing(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool, load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        (target_root / "context" / "listing_publications.md").write_text(
            "# Listing Publications\n\n"
            "## Harbour Centre Short Stay\n"
            "- Remote listing ID: list-sla-1\n"
            "- Property ID: prop-sla-1\n"
            "- Source property ref: Harbour Centre, Hung Hom\n"
            "- Platform: direct\n"
            "- Published at: 2026-03-28T00:00:00+00:00\n"
            "- Nightly price override: 1200 JPY\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class DraftClient:
            def create_listing(self, payload):
                return {"id": "list-draft-2", "propertyId": payload["propertyId"], "platform": payload["platform"]}

        monkeypatch.setattr(ext_module, "MinpakuClient", DraftClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        staged = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_listing",
                {
                    "property_id": "prop-sla-1",
                    "source_property_ref": "Harbour Centre, Hung Hom",
                    "platform": "direct",
                    "title": "Harbour Centre Short Stay",
                    "nightly_price": 1500,
                    "currency": "JPY",
                    "contact": "ops@example.com",
                },
                cm,
            )
        )
        assert staged["ok"] is True
        assert cm.update_staging_status(staged["entry_id"], "approved", "human") is True

        class FakeClient:
            def update_listing(self, listing_id, payload):
                assert listing_id == "list-sla-1"
                assert payload["nightlyPrice"] == 1500
                return {"id": listing_id, "propertyId": "prop-sla-1", "platform": "direct"}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        admin_cli.cmd_update_minpaku(cm, entry_id=staged["entry_id"])
        out = capsys.readouterr().out
        assert "Updated Minpaku listing" in out

        refreshed = cm.get_staging_entry(staged["entry_id"])
        assert refreshed["status"] == "updated"
        listings_text = (target_root / "context" / "listing_publications.md").read_text(encoding="utf-8")
        assert "list-sla-1" in listings_text

    def test_minpaku_review_points_inactive_listing_to_update_command(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        (target_root / "context" / "listing_publications.md").write_text(
            "# Listing Publications\n\n"
            "## Unit B\n"
            "- Remote listing ID: list-unit-b\n"
            "- Property ID: prop-unit-b\n"
            "- Source property ref: Unit B\n"
            "- Platform: airbnb\n"
            "- Published at: 2026-03-29T00:00:00+00:00\n"
            "- Status: active\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        entry_id = cm.create_staging_entry(
            summary="Minpaku listing draft for Unit B",
            content=(
                "## Minpaku Listing Draft — Unit B\n\n"
                "- Property ID: prop-unit-b\n"
                "- Source property ref: Unit B\n"
                "- Platform: airbnb\n"
                "- Status: inactive\n\n"
                "```json\n"
                "{\n"
                "  \"propertyId\": \"prop-unit-b\",\n"
                "  \"title\": \"Unit B\",\n"
                "  \"description\": \"Landlord-approved Minpaku availability handoff for Unit B.\",\n"
                "  \"platform\": \"airbnb\",\n"
                "  \"externalId\": \"unit-b-airbnb\",\n"
                "  \"nightlyPrice\": 250,\n"
                "  \"currency\": \"HKD\",\n"
                "  \"status\": \"inactive\",\n"
                "  \"contact\": \"TBD\",\n"
                "  \"source_property_ref\": \"Unit B\"\n"
                "}\n"
                "```\n"
            ),
            category="listing_publications",
            source="operator",
        )
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def list_listings(self, property_id=None, platform=None, status=None):
                return []

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")
        monkeypatch.setattr("builtins.input", lambda _prompt="": "a")

        admin_cli.cmd_review(cm, auto=False)
        out = capsys.readouterr().out

        assert cm.get_staging_entry(entry_id)["status"] == "approved"
        assert "Next: return to sc --role operator and ask it to update the live listing status." in out
        assert "Next: return to sc --role operator and ask it to publish the approved listing." not in out

    def test_minpaku_host_can_stage_property_removal_request(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions, maybe_handle_message

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def search_properties(self, query):
                assert "12 Harbour View Road" in query
                return [{
                    "id": "prop-sla-1",
                    "title": "12 Harbour View Road, Unit A & B",
                    "location": "Hong Kong, Hong Kong",
                    "hostId": "host-sla-1",
                }]

            def get_bookings_by_property(self, property_id):
                assert property_id == "prop-sla-1"
                return {"bookings": []}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = maybe_handle_message("remove 12 Harbour View Road, Unit A & B, Hong Kong", cm, role_name="host")

        assert "Removal request logged to staging" in reply
        pending = cm.list_staging(status="unconfirmed")
        assert pending
        assert "## Property Removal Request" in pending[-1]["content"]
        assert pending[-1]["category"] == "properties"

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_removal_returns_none_when_search_is_ambiguous(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions, maybe_handle_message

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def search_properties(self, query):
                assert query == "12 Harbour View Road"
                return [
                    {"id": "prop-a", "title": "12 Harbour View Road, Unit A"},
                    {"id": "prop-b", "title": "12 Harbour View Road, Unit B"},
                ]

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = maybe_handle_message("remove 12 Harbour View Road", cm, role_name="host")

        assert reply is None
        assert cm.list_staging(status="unconfirmed") == []

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_removal_accepts_unique_partial_match(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions, maybe_handle_message

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def search_properties(self, query):
                assert query == "Harbour View"
                return [{
                    "id": "prop-sla-1",
                    "title": "12 Harbour View Road, Unit A & B",
                    "location": "Hong Kong, Hong Kong",
                    "hostId": "host-sla-1",
                }]

            def get_bookings_by_property(self, property_id):
                assert property_id == "prop-sla-1"
                return {"bookings": []}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = maybe_handle_message("remove Harbour View", cm, role_name="host")

        assert reply is not None
        assert "Removal request logged to staging" in reply
        pending = cm.list_staging(status="unconfirmed")
        assert pending
        assert pending[-1]["category"] == "properties"
        assert "12 Harbour View Road, Unit A & B" in pending[-1]["content"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_removal_review_hook_approves_when_no_bookings(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        entry_id = cm.create_staging_entry(
            summary="Remove property prop-sla-1 (12 Harbour View Road, Unit A & B)",
            content=(
                "## Property Removal Request\n\n"
                "**Property to remove:**\n"
                "- ID: `prop-sla-1`\n"
                "- Title: 12 Harbour View Road, Unit A & B\n"
                "- Location: Hong Kong, Hong Kong\n"
                "- Host ID: `host-sla-1`\n"
                "- Active or upcoming bookings at request time: 0\n"
            ),
            category="properties",
            source="host-operator",
        )
        entry = cm.get_staging_entry(entry_id)

        class FakeClient:
            def search_properties(self, query):
                assert query == "prop-sla-1"
                return [{"id": "prop-sla-1"}]

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        review = ext_module.review_staging_entry(cm, entry)
        assert review["recommendation"] == "approve"
        assert review["confidence"] >= 0.99 - 1e-9

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_price_update_updates_live_property_and_stages_pricing_note(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions, maybe_handle_message

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        seen: dict[str, object] = {}

        class FakeClient:
            def search_properties(self, query):
                assert query == "Unit A & B"
                return [{
                    "id": "prop-sla-1",
                    "title": "12 Harbour View Road, Unit A & B",
                    "location": {"city": "Hong Kong", "country": "Hong Kong"},
                    "currency": "HKD",
                    "nightlyPrice": 300,
                    "maxGuests": 1,
                    "hostId": "host-sla-1",
                    "amenities": [],
                    "photos": [],
                }]

            def update_property(self, property_id, payload):
                seen["property_id"] = property_id
                seen["payload"] = payload
                return {"success": True, "property": {"id": property_id, **payload}}

            def list_listings(self, property_id=None, platform=None, status=None):
                assert property_id == "prop-sla-1"
                assert status == "active"
                return []

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = maybe_handle_message("update Unit A & B to $500 /night", cm, role_name="host")

        assert "updated the live property price" in reply
        assert seen["property_id"] == "prop-sla-1"
        assert seen["payload"]["nightlyPrice"] == 500
        assert seen["payload"]["currency"] == "HKD"

        pending = cm.list_staging(status="unconfirmed")
        assert pending
        assert pending[-1]["category"] == "pricing"
        assert "New nightly price: `HKD 500/night`" in pending[-1]["content"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_max_guests_update_updates_live_property_and_stages_note(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions, maybe_handle_message

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        seen: dict[str, object] = {}

        class FakeClient:
            def search_properties(self, query):
                assert query == "Unit A & B"
                return [{
                    "id": "prop-sla-1",
                    "title": "12 Harbour View Road, Unit A & B",
                    "location": {"city": "Hong Kong", "country": "Hong Kong"},
                    "currency": "HKD",
                    "nightlyPrice": 300,
                    "maxGuests": 1,
                    "hostId": "host-sla-1",
                    "amenities": [],
                    "photos": [],
                }]

            def update_property(self, property_id, payload):
                seen["property_id"] = property_id
                seen["payload"] = payload
                return {"success": True, "property": {"id": property_id, **payload}}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = maybe_handle_message("update Unit A & B to have 4 guests", cm, role_name="host")

        assert "updated the live property" in reply
        assert seen["property_id"] == "prop-sla-1"
        assert seen["payload"]["maxGuests"] == 4

        pending = cm.list_staging(status="unconfirmed")
        assert pending
        assert pending[-1]["category"] == "properties"
        assert "- Field updated: `maxGuests`" in pending[-1]["content"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_rules_update_updates_live_property_and_stages_note(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions, maybe_handle_message

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        seen: dict[str, object] = {}

        class FakeClient:
            def search_properties(self, query):
                assert query == "Unit A & B"
                return [{
                    "id": "prop-sla-1",
                    "title": "12 Harbour View Road, Unit A & B",
                    "location": {"city": "Hong Kong", "country": "Hong Kong"},
                    "currency": "HKD",
                    "nightlyPrice": 300,
                    "maxGuests": 1,
                    "hostId": "host-sla-1",
                    "amenities": [],
                    "photos": [],
                    "rules": "No smoking",
                }]

            def update_property(self, property_id, payload):
                seen["property_id"] = property_id
                seen["payload"] = payload
                return {"success": True, "property": {"id": property_id, **payload}}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = maybe_handle_message("update Unit A & B rules to No pets", cm, role_name="host")

        assert "updated the live property" in reply
        assert seen["property_id"] == "prop-sla-1"
        assert seen["payload"]["rules"] == "No pets"

        pending = cm.list_staging(status="unconfirmed")
        assert pending
        assert pending[-1]["category"] == "properties"
        assert "- Field updated: `rules`" in pending[-1]["content"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_price_update_falls_back_to_inventory_when_search_returns_empty(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions, maybe_handle_message

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        seen: dict[str, object] = {}

        class FakeClient:
            def search_properties(self, query):
                assert query == "12 Harbour View Road"
                return []

            def list_properties(self):
                return [{
                    "id": "prop-sla-1",
                    "title": "12 Harbour View Road, Unit A & B",
                    "location": {"city": "Hong Kong", "country": "Hong Kong"},
                    "currency": "HKD",
                    "nightlyPrice": 300,
                    "maxGuests": 1,
                    "hostId": "host-sla-1",
                    "amenities": [],
                    "photos": [],
                }]

            def update_property(self, property_id, payload):
                seen["property_id"] = property_id
                seen["payload"] = payload
                return {"success": True, "property": {"id": property_id, **payload}}

            def list_listings(self, property_id=None, platform=None, status=None):
                return []

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = maybe_handle_message("update 12 Harbour View Road to $400/night", cm, role_name="host")

        assert "updated the live property price" in reply
        assert "inventory fallback" in reply
        assert seen["property_id"] == "prop-sla-1"
        assert seen["payload"]["nightlyPrice"] == 400

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_price_update_returns_explicit_error_when_live_update_fails(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions, maybe_handle_message

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def search_properties(self, query):
                return [{
                    "id": "prop-sla-1",
                    "title": "12 Harbour View Road, Unit A & B",
                    "location": {"city": "Hong Kong", "country": "Hong Kong"},
                    "currency": "HKD",
                    "nightlyPrice": 300,
                    "maxGuests": 1,
                    "hostId": "host-sla-1",
                    "amenities": [],
                    "photos": [],
                }]

            def update_property(self, property_id, payload):
                raise RuntimeError("422 Unprocessable Entity")

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = maybe_handle_message("update 12 Harbour View Road to $400/night", cm, role_name="host")

        assert "live property price update failed" in reply
        assert "422 Unprocessable Entity" in reply
        assert cm.list_staging(status="unconfirmed") == []

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_price_update_uses_full_property_record_for_live_update(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions, maybe_handle_message

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        seen: dict[str, object] = {}

        class FakeClient:
            def search_properties(self, query):
                assert query == "12 Harbour View Road"
                return []

            def list_properties(self):
                return [{
                    "id": "prop-sla-1",
                    "title": "12 Harbour View Road, Unit A & B",
                    "location": "Unit A & B, Unit A & B",
                    "currency": "HKD",
                }]

            def get_property(self, property_id):
                assert property_id == "prop-sla-1"
                return {
                    "id": "prop-sla-1",
                    "title": "12 Harbour View Road, Unit A & B",
                    "description": "Full ACP property record",
                    "location": {"city": "Hong Kong", "country": "HK", "coordinates": None},
                    "currency": "HKD",
                    "nightlyPrice": 0,
                    "maxGuests": 1,
                    "hostId": "host-sla-demo-1",
                    "amenities": [],
                    "photos": [],
                }

            def update_property(self, property_id, payload):
                seen["property_id"] = property_id
                seen["payload"] = payload
                return {"success": True, "property": {"id": property_id, **payload}}

            def list_listings(self, property_id=None, platform=None, status=None):
                return []

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = maybe_handle_message("update 12 Harbour View Road to $300/night", cm, role_name="host")

        assert "updated the live property price" in reply
        assert seen["property_id"] == "prop-sla-1"
        assert seen["payload"]["city"] == "Hong Kong"
        assert seen["payload"]["country"] == "HK"
        assert seen["payload"]["hostId"] == "host-sla-demo-1"
        assert seen["payload"]["nightlyPrice"] == 300

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_property_removal_approval_executes_backend_delete(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli, brain
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)

        entry_id = cm.create_staging_entry(
            summary="Remove property prop-sla-1 (12 Harbour View Road, Unit A & B)",
            content=(
                "## Property Removal Request\n\n"
                "**Property to remove:**\n"
                "- ID: `prop-sla-1`\n"
                "- Title: 12 Harbour View Road, Unit A & B\n"
                "- Location: Hong Kong, Hong Kong\n"
                "- Host ID: `host-sla-1`\n"
                "- Active or upcoming bookings at request time: 0\n"
            ),
            category="properties",
            source="host-operator",
        )

        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def search_properties(self, query):
                assert query == "prop-sla-1"
                return [{"id": "prop-sla-1"}]

            def delete_property(self, property_id, host_id=None):
                assert property_id == "prop-sla-1"
                assert host_id == "host-sla-1"
                return {"status": "deleted", "id": property_id}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")
        monkeypatch.setattr(
            brain,
            "review_staging_entry",
            lambda _entry, _committed: {
                "recommendation": "approve",
                "reason": "Looks good",
                "conflicts": [],
                "confidence": 0.99,
            },
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": "a")

        admin_cli.cmd_review(cm, auto=False)

        entry = cm.get_staging_entry(entry_id)
        assert entry["status"] == "approved"
        properties_text = (target_root / "context" / "properties.md").read_text(encoding="utf-8")
        assert "Removed Property — 12 Harbour View Road, Unit A & B" in properties_text
        assert "prop-sla-1" in properties_text

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_listing_review_ignores_historical_removed_property_note_when_no_live_duplicate(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool, load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        (target_root / "context" / "properties.md").write_text(
            "# Properties\n\n"
            "## Removed Property — 12 Harbour View Road, Unit A & B\n"
            "- Remote property ID: prop-old-1\n"
            "- Deleted at: 2026-03-29T05:42:37+00:00\n"
            "- Location: Hong Kong, Hong Kong\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class DraftClient:
            def create_listing(self, payload):
                return {"id": "list-draft-review-1", "propertyId": payload["propertyId"], "platform": payload["platform"]}

        monkeypatch.setattr(ext_module, "MinpakuClient", DraftClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        staged = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_listing",
                {
                    "property_id": "prop-new-1",
                    "source_property_ref": "prop-new-1",
                    "platform": "direct",
                    "title": "12 Harbour View Road, Unit A",
                    "nightly_price": 1800,
                },
                cm,
            )
        )
        entry = cm.get_staging_entry(staged["entry_id"])

        class FakeClient:
            def list_listings(self, property_id=None, platform=None, status=None):
                return []

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        review = ext_module.review_staging_entry(cm, entry)

        assert review["recommendation"] == "approve"
        assert "historical removed-property notes do not block" in review["reason"]

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_listing_review_flattens_nested_listing_rows(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool, load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class DraftClient:
            def create_listing(self, payload):
                return {"id": "list-draft-review-2", "propertyId": payload["propertyId"], "platform": payload["platform"]}

        monkeypatch.setattr(ext_module, "MinpakuClient", DraftClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        staged = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_listing",
                {
                    "property_id": "prop-new-2",
                    "source_property_ref": "prop-new-2",
                    "platform": "direct",
                    "title": "Nested Listing Test",
                    "nightly_price": 1500,
                },
                cm,
            )
        )
        entry = cm.get_staging_entry(staged["entry_id"])

        class FakeClient:
            def list_listings(self, property_id=None, platform=None, status=None):
                return [[{"id": "list-nested-1", "propertyId": "prop-other", "platform": "direct"}]]

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        review = ext_module.review_staging_entry(cm, entry)

        assert review["recommendation"] == "approve"

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_list_listings_dispatch_flattens_nested_rows(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool, load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def list_listings(self, property_id=None, platform=None, status=None):
                return [[{"id": "list-nested-1", "propertyId": "prop-1", "platform": "direct", "title": "Nested Listing"}]]

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        result = json.loads(dispatch_extension_tool("list_listings", {}, cm))

        assert result["count"] == 1
        assert result["listings"][0]["id"] == "list-nested-1"

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_show_all_listings_guides_publish_when_only_approved_drafts_exist(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        entry_id = cm.create_staging_entry(
            summary="Minpaku listing draft for Unit B",
            content=(
                "## Minpaku Listing Draft — Unit B\n\n"
                "- Property ID: prop-unit-b\n"
                "- Source property ref: prop-unit-b\n"
                "- Platform: airbnb\n"
                "- Status: active\n\n"
                "```json\n"
                "{\n"
                "  \"propertyId\": \"prop-unit-b\",\n"
                "  \"title\": \"Unit B\",\n"
                "  \"description\": \"Landlord-approved Minpaku availability handoff for Unit B.\",\n"
                "  \"platform\": \"airbnb\",\n"
                "  \"externalId\": \"unit-b-airbnb\",\n"
                "  \"nightlyPrice\": 250,\n"
                "  \"currency\": \"HKD\",\n"
                "  \"status\": \"active\",\n"
                "  \"contact\": \"TBD\",\n"
                "  \"source_property_ref\": \"prop-unit-b\"\n"
                "}\n"
                "```\n"
            ),
            category="listing_publications",
            source="operator",
        )
        assert cm.update_staging_status(entry_id, "approved", "human") is True

        class FakeClient:
            def list_listings(self):
                return []

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = ext_module.maybe_handle_message("show all listings", cm, role_name="host")

        assert "No live listings right now (`count: 0`)." in reply
        assert "staged listing draft" in reply
        assert "publish the latest listing draft" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_host_role_can_use_live_listing_view(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class FakeClient:
            def list_listings(self):
                return [{"id": "list-1", "propertyId": "prop-1", "platform": "airbnb", "title": "Unit B", "status": "active"}]

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = ext_module.maybe_handle_message("show all listings", cm, role_name="host")

        assert "Here are all live listings (1 total):" in reply
        assert "`list-1`" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_host_can_publish_latest_approved_listing(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        entry_id = cm.create_staging_entry(
            summary="Minpaku listing draft for Unit B",
            content=(
                "## Minpaku Listing Draft — Unit B\n\n"
                "- Property ID: prop-unit-b\n"
                "- Source property ref: prop-unit-b\n"
                "- Platform: airbnb\n"
                "- Status: active\n\n"
                "```json\n"
                "{\n"
                "  \"propertyId\": \"prop-unit-b\",\n"
                "  \"title\": \"Unit B\",\n"
                "  \"description\": \"Landlord-approved Minpaku availability handoff for Unit B.\",\n"
                "  \"platform\": \"airbnb\",\n"
                "  \"externalId\": \"unit-b-airbnb\",\n"
                "  \"nightlyPrice\": 250,\n"
                "  \"currency\": \"HKD\",\n"
                "  \"status\": \"active\",\n"
                "  \"contact\": \"TBD\",\n"
                "  \"source_property_ref\": \"prop-unit-b\"\n"
                "}\n"
                "```\n"
            ),
            category="listing_publications",
            source="operator",
        )
        assert cm.update_staging_status(entry_id, "approved", "human") is True
        
        class FakeClient:
            def create_listing(self, payload):
                assert payload["propertyId"] == "prop-unit-b"
                assert payload["platform"] == "airbnb"
                return {"id": "list-unit-b", "propertyId": "prop-unit-b", "platform": "airbnb"}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = ext_module.maybe_handle_message("publish listing draft for Unit B", cm, role_name="host")

        assert "Published `listing` live in Minpaku." not in reply
        assert "Published `Unit B` live in Minpaku." in reply
        assert entry_id in reply
        assert "list-unit-b" in reply
        assert cm.get_staging_entry(entry_id)["status"] == "published"

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_host_can_publish_named_approved_listing(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        older = cm.create_staging_entry(
            summary="Minpaku listing draft for Unit A",
            content=(
                "## Minpaku Listing Draft — Unit A\n\n"
                "- Property ID: prop-unit-a\n"
                "- Source property ref: Unit A\n"
                "- Platform: airbnb\n"
                "- Status: active\n\n"
                "```json\n"
                "{\n"
                "  \"propertyId\": \"prop-unit-a\",\n"
                "  \"title\": \"Unit A\",\n"
                "  \"description\": \"Draft A.\",\n"
                "  \"platform\": \"airbnb\",\n"
                "  \"externalId\": \"unit-a-airbnb\",\n"
                "  \"nightlyPrice\": 200,\n"
                "  \"currency\": \"HKD\",\n"
                "  \"status\": \"active\",\n"
                "  \"contact\": \"TBD\",\n"
                "  \"source_property_ref\": \"Unit A\"\n"
                "}\n"
                "```\n"
            ),
            category="listing_publications",
            source="operator",
        )
        newer = cm.create_staging_entry(
            summary="Minpaku listing draft for Unit B",
            content=(
                "## Minpaku Listing Draft — Unit B\n\n"
                "- Property ID: prop-unit-b\n"
                "- Source property ref: Unit B\n"
                "- Platform: airbnb\n"
                "- Status: active\n\n"
                "```json\n"
                "{\n"
                "  \"propertyId\": \"prop-unit-b\",\n"
                "  \"title\": \"Unit B\",\n"
                "  \"description\": \"Draft B.\",\n"
                "  \"platform\": \"airbnb\",\n"
                "  \"externalId\": \"unit-b-airbnb\",\n"
                "  \"nightlyPrice\": 250,\n"
                "  \"currency\": \"HKD\",\n"
                "  \"status\": \"active\",\n"
                "  \"contact\": \"TBD\",\n"
                "  \"source_property_ref\": \"Unit B\"\n"
                "}\n"
                "```\n"
            ),
            category="listing_publications",
            source="operator",
        )
        assert cm.update_staging_status(older, "approved", "human") is True
        assert cm.update_staging_status(newer, "approved", "human") is True

        class FakeClient:
            def create_listing(self, payload):
                assert payload["propertyId"] == "prop-unit-a"
                return {"id": "list-unit-a", "propertyId": "prop-unit-a", "platform": "airbnb"}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = ext_module.maybe_handle_message("publish listing draft for Unit A", cm, role_name="host")

        assert reply is not None
        assert "Published `Unit A` live in Minpaku." in reply
        assert older in reply
        assert cm.get_staging_entry(older)["status"] == "published"
        assert cm.get_staging_entry(newer)["status"] == "approved"

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_host_listing_target_returns_none_when_ambiguous(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        first = cm.create_staging_entry(
            summary="Minpaku listing draft for Harbour View A",
            content=(
                "## Minpaku Listing Draft — Harbour View A\n\n"
                "- Property ID: prop-hv-a\n"
                "- Source property ref: Harbour View\n"
                "- Platform: airbnb\n"
                "- Status: active\n\n"
                "```json\n"
                "{\"propertyId\": \"prop-hv-a\", \"title\": \"Harbour View A\", \"description\": \"A\", \"platform\": \"airbnb\", \"status\": \"active\", \"source_property_ref\": \"Harbour View\"}\n"
                "```\n"
            ),
            category="listing_publications",
            source="operator",
        )
        second = cm.create_staging_entry(
            summary="Minpaku listing draft for Harbour View B",
            content=(
                "## Minpaku Listing Draft — Harbour View B\n\n"
                "- Property ID: prop-hv-b\n"
                "- Source property ref: Harbour View\n"
                "- Platform: airbnb\n"
                "- Status: active\n\n"
                "```json\n"
                "{\"propertyId\": \"prop-hv-b\", \"title\": \"Harbour View B\", \"description\": \"B\", \"platform\": \"airbnb\", \"status\": \"active\", \"source_property_ref\": \"Harbour View\"}\n"
                "```\n"
            ),
            category="listing_publications",
            source="operator",
        )
        assert cm.update_staging_status(first, "approved", "human") is True
        assert cm.update_staging_status(second, "approved", "human") is True

        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = ext_module.maybe_handle_message("publish listing for Harbour View", cm, role_name="host")

        assert reply is None
        assert cm.get_staging_entry(first)["status"] == "approved"
        assert cm.get_staging_entry(second)["status"] == "approved"

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_host_can_unlist_latest_approved_listing(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        (target_root / "context" / "listing_publications.md").write_text(
            "# Listing Publications\n\n"
            "## Unit B\n"
            "- Remote listing ID: list-unit-b\n"
            "- Property ID: prop-unit-b\n"
            "- Source property ref: Unit B\n"
            "- Platform: airbnb\n"
            "- Published at: 2026-03-29T00:00:00+00:00\n"
            "- Status: active\n",
            encoding="utf-8",
        )
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        entry_id = cm.create_staging_entry(
            summary="Minpaku listing draft for Unit B",
            content=(
                "## Minpaku Listing Draft — Unit B\n\n"
                "- Property ID: prop-unit-b\n"
                "- Source property ref: Unit B\n"
                "- Platform: airbnb\n"
                "- Status: inactive\n\n"
                "```json\n"
                "{\n"
                "  \"propertyId\": \"prop-unit-b\",\n"
                "  \"title\": \"Unit B\",\n"
                "  \"description\": \"Landlord-approved Minpaku availability handoff for Unit B.\",\n"
                "  \"platform\": \"airbnb\",\n"
                "  \"externalId\": \"unit-b-airbnb\",\n"
                "  \"nightlyPrice\": 250,\n"
                "  \"currency\": \"HKD\",\n"
                "  \"status\": \"inactive\",\n"
                "  \"contact\": \"TBD\",\n"
                "  \"source_property_ref\": \"Unit B\"\n"
                "}\n"
                "```\n"
            ),
            category="listing_publications",
            source="operator",
        )
        assert cm.update_staging_status(entry_id, "approved", "human") is True

        class FakeClient:
            def delete_listing(self, listing_id):
                assert listing_id == "list-unit-b"
                return {"status": "deleted", "id": listing_id}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = ext_module.maybe_handle_message("unlist the latest listing draft", cm, role_name="host")

        assert "Unlisted `Unit B` from Minpaku." in reply
        assert "list-unit-b" in reply
        assert cm.get_staging_entry(entry_id)["status"] == "deleted"

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_host_booking_confirmation_requires_payment_verification(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = ext_module.maybe_handle_message("confirm booking for prop-1 after payment verified", cm, role_name="host")

        assert "Booking confirmation is a Minpaku operator approval" in reply
        assert "ready for operator confirmation" in reply
        assert "show bookings for <property>" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_host_booking_confirmation_confirms_specific_booking_id(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        class FakeClient:
            def confirm_booking(self, booking_id, payment_method_token=None):
                assert booking_id == "bk-123456"
                assert payment_method_token is None
                return {
                    "booking": {"id": booking_id, "status": "CONFIRMED"},
                    "paymentIntent": {"status": "SUCCEEDED"},
                    "confirmation": {"confirmationId": f"CONF-{booking_id}"},
                }

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)

        reply = ext_module.maybe_handle_message("confirm booking bk-123456 after payment verified", cm, role_name="host")

        assert "Confirmed booking `bk-123456` after payment verification." in reply
        assert "`CONFIRMED`" in reply
        assert "`SUCCEEDED`" in reply
        assert "`CONF-bk-123456`" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_host_lists_bookings_needing_payment_confirmation(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        class FakeClient:
            def list_properties(self):
                return [{"id": "prop-1", "title": "Modern Apartment in Zurich Center"}]

            def get_bookings_by_property(self, property_id):
                assert property_id == "prop-1"
                return {
                    "bookings": [
                        {
                            "id": "bk-need-confirm",
                            "status": "HOLD",
                            "checkIn": "2026-04-10",
                            "checkOut": "2026-04-12",
                            "guest": {"name": "Demo Guest"},
                        },
                        {
                            "id": "bk-confirmed",
                            "status": "CONFIRMED",
                            "checkIn": "2026-05-01",
                            "checkOut": "2026-05-04",
                            "guest": {"name": "Existing Guest"},
                        },
                    ]
                }

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)

        reply = ext_module.maybe_handle_message("show bookings needing payment confirmation", cm, role_name="host")

        assert "Bookings needing operator confirmation (1 total):" in reply
        assert "`bk-need-confirm`" in reply
        assert "Demo Guest" in reply
        assert "confirm booking <booking-id> after payment verified" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_host_booking_confirmation_warns_when_payment_not_verified(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        reply = ext_module.maybe_handle_message("confirm booking for prop-1", cm, role_name="host")

        assert "Booking confirmation is a Minpaku operator approval." in reply
        assert "only after payment is verified" in reply
        assert "keep the booking on hold" in reply

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]

    def test_minpaku_client_list_listings_accepts_raw_list_response(self, tmp_path):
        from simply_connect import admin_cli

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return [{"id": "list-1"}, {"id": "list-2"}]

        class FakeHttpClient:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get(self, *args, **kwargs):
                return FakeResponse()

        import sys
        import types
        sys.path.insert(0, str(target_root))
        from extension.client import MinpakuClient
        fake_httpx = types.SimpleNamespace(Client=FakeHttpClient)
        original_httpx = sys.modules.get("httpx")
        sys.modules["httpx"] = fake_httpx
        try:
            client = MinpakuClient(base_url="http://example.test", api_key="k")
            rows = client.list_listings()
            assert [row["id"] for row in rows] == ["list-1", "list-2"]
        finally:
            if original_httpx is None:
                del sys.modules["httpx"]
            else:
                sys.modules["httpx"] = original_httpx
            sys.path.remove(str(target_root))

    def test_minpaku_can_unlist_existing_listing(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager
        from simply_connect.ext_loader import dispatch_extension_tool, load_active_extensions

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        (target_root / "context" / "listing_publications.md").write_text(
            "# Listing Publications\n\n"
            "## Harbour Centre Short Stay\n"
            "- Remote listing ID: list-sla-1\n"
            "- Property ID: prop-sla-1\n"
            "- Source property ref: Harbour Centre, Hung Hom\n"
            "- Platform: direct\n"
            "- Published at: 2026-03-28T00:00:00+00:00\n"
            "- Nightly price override: 1200 JPY\n",
            encoding="utf-8",
        )

        cm = ContextManager(root=target_root)
        ext_module = load_active_extensions(cm)[0]["module"]

        class DraftClient:
            def create_listing(self, payload):
                return {"id": "list-draft-3", "propertyId": payload["propertyId"], "platform": payload["platform"]}

        monkeypatch.setattr(ext_module, "MinpakuClient", DraftClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        staged = json.loads(
            dispatch_extension_tool(
                "prepare_minpaku_listing",
                {
                    "property_id": "prop-sla-1",
                    "source_property_ref": "Harbour Centre, Hung Hom",
                    "platform": "direct",
                    "title": "Harbour Centre Short Stay",
                    "nightly_price": 1200,
                    "currency": "JPY",
                    "contact": "ops@example.com",
                },
                cm,
            )
        )
        assert staged["ok"] is True
        assert staged["staged"] is True
        assert cm.update_staging_status(staged["entry_id"], "approved", "human") is True

        class FakeClient:
            def delete_listing(self, listing_id):
                assert listing_id == "list-sla-1"
                return {"status": "deleted", "id": listing_id}

        monkeypatch.setattr(ext_module, "MinpakuClient", FakeClient)
        monkeypatch.setenv("MINPAKU_API_URL", "http://example.test")

        admin_cli.cmd_unlist_minpaku(cm, entry_id=staged["entry_id"])
        out = capsys.readouterr().out
        assert "Unlisted Minpaku listing" in out

        refreshed = cm.get_staging_entry(staged["entry_id"])
        assert refreshed["status"] == "deleted"
        listings_text = (target_root / "context" / "listing_publications.md").read_text(encoding="utf-8")
        assert "list-sla-1" in listings_text
        assert "prop-sla-1" in listings_text
        assert "- Delisted at:" in listings_text
        assert listings_text.count("## Harbour Centre Short Stay") == 1

        for module_name in list(sys.modules):
            if module_name == "_sc_extension_minpaku.tools" or module_name.startswith("_sc_extension_minpaku."):
                del sys.modules[module_name]
