"""
Tests for multi-role profile support.

Covers ContextManager role methods, context filtering, AGENT.md path resolution,
bot token env var lookup, and SDKRuntime role-namespaced sessions.
No API calls required.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_root(tmp_path):
    (tmp_path / "AGENT.md").write_text("# Root AGENT.md\n")
    ctx = tmp_path / "context"
    ctx.mkdir()
    for stem in ["business", "parties", "preferences", "contracts"]:
        (ctx / f"{stem}.md").write_text(f"# {stem.capitalize()}\n\nContent for {stem}.\n")
    (tmp_path / "staging").mkdir()
    return tmp_path


@pytest.fixture
def profile_with_roles(project_root):
    profile = {
        "name": "Legal Counsel + SLA",
        "context_files": ["business", "parties", "preferences", "contracts"],
        "category_map": {
            "business": "business.md",
            "parties": "parties.md",
            "preferences": "preferences.md",
            "contracts": "contracts.md",
            "general": "business.md",
        },
        "intake_sources": {},
        "extensions": [],
        "roles": {
            "lawyer": {
                "agent_md": "roles/lawyer/AGENT.md",
                "context_filter": ["business", "parties", "preferences", "contracts"],
                "telegram_bot_env": "SC_LAWYER_BOT_TOKEN",
            },
            "client": {
                "agent_md": "roles/client/AGENT.md",
                "context_filter": ["parties"],
                "telegram_bot_env": "SC_CLIENT_BOT_TOKEN",
            },
        },
    }
    (project_root / "profile.json").write_text(json.dumps(profile))

    # Create role AGENT.md files
    roles_dir = project_root / "roles"
    (roles_dir / "lawyer").mkdir(parents=True)
    (roles_dir / "client").mkdir(parents=True)
    (roles_dir / "lawyer" / "AGENT.md").write_text("# Lawyer AGENT.md\n")
    (roles_dir / "client" / "AGENT.md").write_text("# Client AGENT.md\n")

    return project_root


@pytest.fixture
def cm_with_roles(profile_with_roles):
    from simply_connect.context_manager import ContextManager
    return ContextManager(root=profile_with_roles)


@pytest.fixture
def cm_no_roles(project_root):
    from simply_connect.context_manager import ContextManager
    return ContextManager(root=project_root)


# ---------------------------------------------------------------------------
# ContextManager.roles
# ---------------------------------------------------------------------------

class TestRolesProperty:
    def test_no_roles_returns_empty_dict(self, cm_no_roles):
        assert cm_no_roles.roles == {}

    def test_roles_returns_dict(self, cm_with_roles):
        roles = cm_with_roles.roles
        assert isinstance(roles, dict)
        assert "lawyer" in roles
        assert "client" in roles

    def test_role_keys_present(self, cm_with_roles):
        lawyer = cm_with_roles.roles["lawyer"]
        assert "agent_md" in lawyer
        assert "context_filter" in lawyer
        assert "telegram_bot_env" in lawyer


# ---------------------------------------------------------------------------
# ContextManager.load_context_for_role
# ---------------------------------------------------------------------------

class TestLoadContextForRole:
    def test_lawyer_sees_all_context_files(self, cm_with_roles):
        ctx = cm_with_roles.load_context_for_role("lawyer")
        assert set(ctx["committed"].keys()) == {"business", "parties", "preferences", "contracts"}

    def test_client_sees_only_parties(self, cm_with_roles):
        ctx = cm_with_roles.load_context_for_role("client")
        assert set(ctx["committed"].keys()) == {"parties"}

    def test_filtered_context_excludes_other_files(self, cm_with_roles):
        ctx = cm_with_roles.load_context_for_role("client")
        assert "business" not in ctx["committed"]
        assert "contracts" not in ctx["committed"]

    def test_staging_always_included(self, cm_with_roles):
        cm_with_roles.create_staging_entry("Test", "Content", "business")
        ctx = cm_with_roles.load_context_for_role("client")
        assert "staging" in ctx
        assert isinstance(ctx["staging"], list)

    def test_unknown_role_returns_all_context(self, cm_with_roles):
        """Unknown role has no filter — returns full context."""
        ctx = cm_with_roles.load_context_for_role("unknown_role")
        assert set(ctx["committed"].keys()) == {"business", "parties", "preferences", "contracts"}

    def test_returns_expected_structure(self, cm_with_roles):
        ctx = cm_with_roles.load_context_for_role("lawyer")
        assert "committed" in ctx
        assert "staging" in ctx
        assert isinstance(ctx["committed"], dict)
        assert isinstance(ctx["staging"], list)


# ---------------------------------------------------------------------------
# ContextManager.agent_md_path_for_role
# ---------------------------------------------------------------------------

class TestAgentMdPathForRole:
    def test_lawyer_returns_path(self, cm_with_roles, profile_with_roles):
        path = cm_with_roles.agent_md_path_for_role("lawyer")
        assert path is not None
        assert path == profile_with_roles / "roles" / "lawyer" / "AGENT.md"

    def test_client_returns_path(self, cm_with_roles, profile_with_roles):
        path = cm_with_roles.agent_md_path_for_role("client")
        assert path is not None
        assert path == profile_with_roles / "roles" / "client" / "AGENT.md"

    def test_unknown_role_returns_none(self, cm_with_roles):
        path = cm_with_roles.agent_md_path_for_role("unknown_role")
        assert path is None

    def test_no_roles_profile_returns_none(self, cm_no_roles):
        path = cm_no_roles.agent_md_path_for_role("operator")
        assert path is None

    def test_path_exists(self, cm_with_roles):
        path = cm_with_roles.agent_md_path_for_role("lawyer")
        assert path.exists()


# ---------------------------------------------------------------------------
# ContextManager.bot_token_env_for_role
# ---------------------------------------------------------------------------

class TestBotTokenEnvForRole:
    def test_lawyer_returns_correct_env_var(self, cm_with_roles):
        assert cm_with_roles.bot_token_env_for_role("lawyer") == "SC_LAWYER_BOT_TOKEN"

    def test_client_returns_correct_env_var(self, cm_with_roles):
        assert cm_with_roles.bot_token_env_for_role("client") == "SC_CLIENT_BOT_TOKEN"

    def test_unknown_role_falls_back_to_default(self, cm_with_roles):
        assert cm_with_roles.bot_token_env_for_role("unknown") == "SC_TELEGRAM_BOT_TOKEN"

    def test_no_roles_profile_falls_back_to_default(self, cm_no_roles):
        assert cm_no_roles.bot_token_env_for_role("operator") == "SC_TELEGRAM_BOT_TOKEN"


# ---------------------------------------------------------------------------
# brain._load_agent_md with explicit path
# ---------------------------------------------------------------------------

class TestLoadAgentMdPath:
    def test_explicit_path_overrides_root(self, profile_with_roles):
        from simply_connect.brain import _load_agent_md
        path = profile_with_roles / "roles" / "lawyer" / "AGENT.md"
        content = _load_agent_md(path=path)
        assert "Lawyer AGENT.md" in content

    def test_none_path_reads_root(self, profile_with_roles):
        from simply_connect.brain import _load_agent_md
        with patch("simply_connect.brain._resolve_project_root", return_value=profile_with_roles):
            content = _load_agent_md(path=None)
        assert "Root AGENT.md" in content

    def test_missing_path_returns_empty_string(self, tmp_path):
        from simply_connect.brain import _load_agent_md
        missing = tmp_path / "nonexistent" / "AGENT.md"
        content = _load_agent_md(path=missing)
        assert content == ""


# ---------------------------------------------------------------------------
# SDKRuntime role-namespaced sessions
# ---------------------------------------------------------------------------

class TestSDKRuntimeRoleNamespacing:
    def test_session_namespaced_by_role(self, cm_with_roles, profile_with_roles):
        """SDKRuntime with role_name should use role-prefixed session IDs."""
        from simply_connect.runtimes.sdk import SDKRuntime

        mock_result = {
            "reply": "Hello from lawyer.",
            "capture": None,
            "confidence": 0.9,
            "used_unconfirmed": False,
            "raw_response": "",
        }

        sm = MagicMock()
        sm.get_history.return_value = []

        # ContextManager is imported inside __init__ — patch at source module
        with patch("simply_connect.context_manager.ContextManager.__init__", return_value=None), \
             patch("simply_connect.brain.respond", return_value=mock_result), \
             patch("simply_connect.brain._get_claude"), \
             patch("simply_connect.brain._resolve_project_root", return_value=profile_with_roles):

            runtime = SDKRuntime(role_name="lawyer")
            runtime._cm = cm_with_roles  # inject directly after init
            runtime._sm = sm

            runtime.call("Draft an NDA.", user_id=42)

        sm.init_session.assert_called_once_with("lawyer:42", role="lawyer")
        sm.get_history.assert_called_once_with("lawyer:42")

    def test_no_roles_session_not_namespaced(self, cm_no_roles, project_root):
        """Without roles, session key is still role-prefixed (operator:user_id)."""
        from simply_connect.runtimes.sdk import SDKRuntime

        mock_result = {
            "reply": "Hello.",
            "capture": None,
            "confidence": 0.9,
            "used_unconfirmed": False,
            "raw_response": "",
        }

        sm = MagicMock()
        sm.get_history.return_value = []

        with patch("simply_connect.context_manager.ContextManager.__init__", return_value=None), \
             patch("simply_connect.brain.respond", return_value=mock_result), \
             patch("simply_connect.brain._get_claude"), \
             patch("simply_connect.brain._resolve_project_root", return_value=project_root):

            runtime = SDKRuntime(role_name="operator")
            runtime._cm = cm_no_roles
            runtime._sm = sm

            runtime.call("Hello.", user_id=99)

        sm.init_session.assert_called_once_with("operator:99", role="operator")


# ---------------------------------------------------------------------------
# Backward compatibility — no roles in profile
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_load_all_context_unchanged(self, cm_no_roles):
        ctx = cm_no_roles.load_all_context()
        assert "committed" in ctx
        assert "staging" in ctx
        assert set(ctx["committed"].keys()) == {"business", "parties", "preferences", "contracts"}

    def test_roles_is_empty_dict(self, cm_no_roles):
        assert cm_no_roles.roles == {}

    def test_agent_md_path_none_for_no_roles(self, cm_no_roles):
        assert cm_no_roles.agent_md_path_for_role("operator") is None
