"""Tests for profile-driven starter prompts."""

from __future__ import annotations

import json


def test_context_manager_reads_profile_starter_prompts(tmp_path):
    (tmp_path / "AGENT.md").write_text("# Test\n", encoding="utf-8")
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    (context_dir / "business.md").write_text("# Business\n", encoding="utf-8")

    profile = {
        "name": "Test",
        "context_files": ["business"],
        "category_map": {"business": "business.md", "general": "business.md"},
        "intake_sources": {},
        "extensions": [],
        "starter_prompts": {
            "reviewer": ["Review the active change."],
            "operator": ["Show me the current overview."],
        },
    }
    (tmp_path / "profile.json").write_text(json.dumps(profile), encoding="utf-8")

    from simply_connect.context_manager import ContextManager

    cm = ContextManager(root=tmp_path)
    assert cm.starter_prompts_for_role("reviewer") == ["Review the active change."]
    assert cm.starter_prompts_for_role("founder") == ["Show me the current overview."]
