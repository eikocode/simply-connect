"""Runtime-level checks for SDKRuntime with real initialized domains."""

from __future__ import annotations

import json
import sys
from pathlib import Path


class TestSDKRuntimeDecisionPack:
    def test_sdk_runtime_uses_decision_pack_extension_tools(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        monkeypatch.chdir(target_root)

        def fake_respond_with_tools(*, message, context, tools, dispatch_fn, history, role, agent_md_path, categories):
            tool_names = {tool["name"] for tool in tools}
            assert "decision_pack_create_submission" in tool_names
            assert "decision_pack_get_latest_submission" in tool_names
            assert role == "founder"
            assert agent_md_path == target_root / "roles" / "founder" / "AGENT.md"

            created = json.loads(
                dispatch_fn(
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
                )
            )
            latest = json.loads(dispatch_fn("decision_pack_get_latest_submission", {}))
            assert latest["submission_id"] == created["submission_id"]

            overview = json.loads(dispatch_fn("decision_pack_build_operator_overview", {}))
            assert overview["latest_submission"]["submission_id"] == created["submission_id"]

            return {
                "reply": f"Created {created['submission_id']}",
                "capture": None,
                "confidence": 0.99,
                "used_unconfirmed": False,
                "raw_response": "tool path exercised",
            }

        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fake_respond_with_tools)

        from simply_connect.runtimes.sdk import SDKRuntime

        runtime = SDKRuntime(role_name="founder")
        reply = runtime.call("Create a new FluxHalo submission.", user_id=42)

        assert reply.startswith("Created ")
        assert (target_root / ".decision_pack_state" / "latest.json").exists()
        assert (target_root / "data" / "sessions" / "founder_42.json").exists()

        for module_name in list(sys.modules):
            if module_name == "domains.decision_pack.extension.tools" or module_name.startswith("domains.decision_pack.extension."):
                del sys.modules[module_name]

    def test_sdk_runtime_supports_stepwise_conversation_over_latest_state(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        monkeypatch.chdir(target_root)

        call_counter = {"count": 0}

        def fake_respond_with_tools(*, message, context, tools, dispatch_fn, history, role, agent_md_path, categories):
            tool_names = {tool["name"] for tool in tools}
            assert "decision_pack_get_working_state" in tool_names
            assert role == "founder"
            call_counter["count"] += 1

            if call_counter["count"] == 1:
                assert history == []
                created = json.loads(
                    dispatch_fn(
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
                    )
                )
                return {
                    "reply": f"Created {created['submission_id']}",
                    "capture": None,
                    "confidence": 0.99,
                    "used_unconfirmed": False,
                    "raw_response": "created submission",
                }

            assert any(turn["content"].startswith("Create") for turn in history if turn["role"] == "user")
            working = json.loads(dispatch_fn("decision_pack_get_working_state", {}))
            assert working["latest_submission"]["submission_id"]
            assert working["latest_version"] >= 1

            attached = json.loads(
                dispatch_fn(
                    "decision_pack_attach_investor_questions",
                    {
                        "questions": ["Why will this be defensible against fast followers?"],
                        "expected_version": working["latest_version"],
                    },
                )
            )
            rerun = json.loads(
                dispatch_fn(
                    "decision_pack_rerun_underwriting",
                    {
                        "expected_version": attached["version"],
                    },
                )
            )
            refreshed = json.loads(dispatch_fn("decision_pack_get_working_state", {}))
            assert refreshed["top_blocker_task"]["task_id"].startswith("TQ_")
            return {
                "reply": f"Attached investor diligence and refreshed to version {rerun['version']}",
                "capture": None,
                "confidence": 0.99,
                "used_unconfirmed": False,
                "raw_response": "stepwise follow-up",
            }

        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fake_respond_with_tools)

        from simply_connect.runtimes.sdk import SDKRuntime

        runtime = SDKRuntime(role_name="founder")
        first = runtime.call("Create a new FluxHalo submission.", user_id=42)
        second = runtime.call("Now add an investor diligence question and tell me the top blocker.", user_id=42)

        assert first.startswith("Created ")
        assert "version" in second.lower()
        assert (target_root / ".decision_pack_state" / "latest.json").exists()

        for module_name in list(sys.modules):
            if module_name == "domains.decision_pack.extension.tools" or module_name.startswith("domains.decision_pack.extension."):
                del sys.modules[module_name]

    def test_sdk_runtime_can_use_compound_tools_for_bigger_intents(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        monkeypatch.chdir(target_root)
        call_counter = {"count": 0}

        def fake_respond_with_tools(*, message, context, tools, dispatch_fn, history, role, agent_md_path, categories):
            tool_names = {tool["name"] for tool in tools}
            assert "decision_pack_create_and_assess_submission" in tool_names
            assert "decision_pack_process_pricing_change" in tool_names
            assert "decision_pack_review_material_change_hold" in tool_names
            call_counter["count"] += 1

            if call_counter["count"] == 1:
                assert role == "founder"
                result = json.loads(
                    dispatch_fn(
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
                    )
                )
                return {
                    "reply": f"Created and assessed {result['submission']['submission_id']}",
                    "capture": None,
                    "confidence": 0.99,
                    "used_unconfirmed": False,
                    "raw_response": "compound create",
                }

            first_state = json.loads(dispatch_fn("decision_pack_get_working_state", {}))
            assert first_state["active_submission_id"]

            if call_counter["count"] == 2:
                assert role == "founder"
                changed = json.loads(
                    dispatch_fn(
                        "decision_pack_process_pricing_change",
                        {
                            "summary": "FluxHalo moved from usage-based pricing to annual platform contracts with a one-time implementation fee.",
                            "expected_version": first_state["latest_version"],
                        },
                    )
                )
                return {
                    "reply": f"Processed pricing change at version {changed['processed_change']['version']}",
                    "capture": None,
                    "confidence": 0.99,
                    "used_unconfirmed": False,
                    "raw_response": "compound change",
                }

            assert role == "reviewer"
            reviewed = json.loads(
                dispatch_fn(
                    "decision_pack_review_material_change_hold",
                    {
                        "expected_version": first_state["latest_version"],
                    },
                )
            )
            return {
                "reply": f"Submission is now {reviewed['reviewer_disposition']['status']}",
                "capture": None,
                "confidence": 0.99,
                "used_unconfirmed": False,
                "raw_response": "compound review",
            }

        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fake_respond_with_tools)

        from simply_connect.runtimes.sdk import SDKRuntime

        founder_runtime = SDKRuntime(role_name="founder")
        reviewer_runtime = SDKRuntime(role_name="reviewer")
        first = founder_runtime.call("Create and assess a new FluxHalo submission.", user_id=77)
        second = founder_runtime.call("Process a pricing change for the active submission.", user_id=77)
        third = reviewer_runtime.call("Hold the active material change for policy review.", user_id=77)

        assert first.startswith("Created and assessed ")
        assert "Processed pricing change" in second
        assert "needs_policy_review" in third

        for module_name in list(sys.modules):
            if module_name == "domains.decision_pack.extension.tools" or module_name.startswith("domains.decision_pack.extension."):
                del sys.modules[module_name]
