"""
Tests for sc-admin new-domain (cmd_new_domain).

Covers skeleton file generation, profile.json correctness, role scaffolding,
extension scaffolding, and the domain-already-exists guard.
No interactive prompts — inputs are injected via patch("builtins.input").
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch


def _run(inputs: list[str], domains_dir: Path) -> None:
    """Run cmd_new_domain with a sequence of canned inputs."""
    from simply_connect.admin_cli import cmd_new_domain
    with patch("builtins.input", side_effect=inputs):
        cmd_new_domain(domains_dir)


# ---------------------------------------------------------------------------
# Basic skeleton — no roles, no extension
# ---------------------------------------------------------------------------

class TestBasicDomain:
    def test_creates_domain_dir(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "notes, tasks", "", "n"], domains_dir)
        assert (domains_dir / "my-domain").is_dir()

    def test_creates_profile_json(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "My Domain", "notes, tasks", "", "n"], domains_dir)
        profile_path = domains_dir / "my-domain" / "profile.json"
        assert profile_path.exists()
        profile = json.loads(profile_path.read_text())
        assert profile["name"] == "My Domain"
        assert profile["context_files"] == ["notes", "tasks"]

    def test_creates_agent_md(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "My Domain", "notes", "", "n"], domains_dir)
        agent_md = (domains_dir / "my-domain" / "AGENT.md").read_text()
        assert "My Domain" in agent_md
        assert "Capture Instruction" in agent_md

    def test_creates_context_skeleton_files(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha, beta, gamma", "", "n"], domains_dir)
        ctx = domains_dir / "my-domain" / "context"
        assert (ctx / "alpha.md").exists()
        assert (ctx / "beta.md").exists()
        assert (ctx / "gamma.md").exists()

    def test_creates_context_readme(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "n"], domains_dir)
        assert (domains_dir / "my-domain" / "context" / "README.md").exists()

    def test_creates_intake_map(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "n"], domains_dir)
        assert (domains_dir / "my-domain" / "admin" / "intake_map.md").exists()

    def test_no_roles_dir_when_skipped(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "n"], domains_dir)
        assert not (domains_dir / "my-domain" / "roles").exists()

    def test_no_extension_dir_when_skipped(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "n"], domains_dir)
        assert not (domains_dir / "my-domain" / "extension").exists()

    def test_default_context_files_on_empty_input(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "", "", "n"], domains_dir)
        profile = json.loads((domains_dir / "my-domain" / "profile.json").read_text())
        assert profile["context_files"] == ["properties", "operations", "contacts"]

    def test_default_display_name_on_empty_input(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "", "", "n"], domains_dir)
        profile = json.loads((domains_dir / "my-domain" / "profile.json").read_text())
        assert profile["name"] == "My Domain"

    def test_profile_json_extensions_empty(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "n"], domains_dir)
        profile = json.loads((domains_dir / "my-domain" / "profile.json").read_text())
        assert profile["extensions"] == []

    def test_profile_json_roles_empty(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "n"], domains_dir)
        profile = json.loads((domains_dir / "my-domain" / "profile.json").read_text())
        assert profile["roles"] == {}


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

class TestRoles:
    def test_creates_role_dirs(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "host, guest", "n"], domains_dir)
        assert (domains_dir / "my-domain" / "roles" / "host").is_dir()
        assert (domains_dir / "my-domain" / "roles" / "guest").is_dir()

    def test_creates_role_agent_md(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "host", "n"], domains_dir)
        agent_md = (domains_dir / "my-domain" / "roles" / "host" / "AGENT.md").read_text()
        assert "host" in agent_md.lower()

    def test_profile_json_has_roles(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha, beta", "admin, viewer", "n"], domains_dir)
        profile = json.loads((domains_dir / "my-domain" / "profile.json").read_text())
        assert "admin" in profile["roles"]
        assert "viewer" in profile["roles"]

    def test_role_has_context_filter(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha, beta", "host", "n"], domains_dir)
        profile = json.loads((domains_dir / "my-domain" / "profile.json").read_text())
        assert profile["roles"]["host"]["context_filter"] == ["alpha", "beta"]

    def test_role_has_telegram_bot_env(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "host", "n"], domains_dir)
        profile = json.loads((domains_dir / "my-domain" / "profile.json").read_text())
        assert profile["roles"]["host"]["telegram_bot_env"] == "MY_DOMAIN_HOST_BOT_TOKEN"

    def test_role_has_agent_md_path(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "host", "n"], domains_dir)
        profile = json.loads((domains_dir / "my-domain" / "profile.json").read_text())
        assert profile["roles"]["host"]["agent_md"] == "roles/host/AGENT.md"


# ---------------------------------------------------------------------------
# Extension
# ---------------------------------------------------------------------------

class TestExtension:
    def test_creates_extension_dir(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "y"], domains_dir)
        assert (domains_dir / "my-domain" / "extension").is_dir()

    def test_creates_tools_py(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "y"], domains_dir)
        assert (domains_dir / "my-domain" / "extension" / "tools.py").exists()

    def test_creates_client_py(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "y"], domains_dir)
        assert (domains_dir / "my-domain" / "extension" / "client.py").exists()

    def test_creates_init_py(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "y"], domains_dir)
        assert (domains_dir / "my-domain" / "extension" / "__init__.py").exists()

    def test_profile_json_extension_listed(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "y"], domains_dir)
        profile = json.loads((domains_dir / "my-domain" / "profile.json").read_text())
        assert profile["extensions"] == ["my-domain"]

    def test_tools_py_has_dispatch_function(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "y"], domains_dir)
        tools_content = (domains_dir / "my-domain" / "extension" / "tools.py").read_text()
        assert "def dispatch(" in tools_content
        assert "TOOLS" in tools_content

    def test_client_py_has_class(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        _run(["my-domain", "", "alpha", "", "y"], domains_dir)
        client_content = (domains_dir / "my-domain" / "extension" / "client.py").read_text()
        assert "class " in client_content
        assert "MY_DOMAIN_API_URL" in client_content


# ---------------------------------------------------------------------------
# Overwrite guard
# ---------------------------------------------------------------------------

class TestOverwriteGuard:
    def test_cancels_on_existing_domain_no(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        (domains_dir / "existing").mkdir()
        _run(["existing", "n"], domains_dir)
        # No files should have been created inside
        assert not (domains_dir / "existing" / "profile.json").exists()

    def test_proceeds_on_existing_domain_yes(self, tmp_path):
        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()
        (domains_dir / "existing").mkdir()
        _run(["existing", "y", "Existing", "alpha", "", "n"], domains_dir)
        assert (domains_dir / "existing" / "profile.json").exists()
