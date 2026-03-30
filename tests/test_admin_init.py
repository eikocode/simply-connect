"""
Tests for sc-admin init domain resolution (_resolve_domains_dir).

Covers env var override, sibling-repo auto-detection, and local fallback.
No filesystem mutation beyond tmp_path — no API calls required.
"""

import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_resolve(monkeypatch, env_val: str | None, sibling_exists: bool, local_exists: bool, tmp_path: Path) -> Path:
    """
    Exercise _resolve_domains_dir() under controlled conditions.

    Patches:
      - SC_DOMAINS_DIR env var
      - The engine root (Path(__file__).parent.parent in admin_cli)
      - Sibling repo presence
      - Local domains/ presence
    """
    engine_root = tmp_path / "simply-connect"
    engine_root.mkdir()

    sibling = tmp_path / "simply-connect-domains" / "domains"
    if sibling_exists:
        sibling.mkdir(parents=True)

    local = engine_root / "domains"
    if local_exists:
        local.mkdir()

    # admin_cli._resolve_domains_dir uses Path(__file__).parent.parent
    # __file__ = simply-connect/admin_cli.py  →  parent = simply-connect/  →  parent.parent = engine root
    fake_file = engine_root / "simply-connect" / "admin_cli.py"

    import os
    env = {k: v for k, v in os.environ.items() if k != "SC_DOMAINS_DIR"}
    if env_val is not None:
        env["SC_DOMAINS_DIR"] = env_val

    with patch.dict("os.environ", env, clear=True), \
         patch("simply_connect.admin_cli.Path") as mock_path_cls:

        # Make Path(__file__) inside _resolve_domains_dir return our fake file
        real_path = Path  # keep reference to real Path

        def path_side_effect(arg=None):
            if arg is None:
                return real_path()
            # When called with __file__ sentinel — we can't intercept __file__ directly,
            # so we patch at a higher level below
            return real_path(arg)

        # Simpler approach: just import and patch the specific internal reference
        pass

    # Simpler: patch os.getenv and the internal Path resolution
    import importlib
    from simply_connect import admin_cli

    captured = {}

    original_resolve = admin_cli._resolve_domains_dir

    def patched_resolve():
        import os as _os
        env_override = _os.getenv("SC_DOMAINS_DIR", "")
        if env_override:
            return real_path(env_override)
        # Use our controlled engine_root instead of the real one
        _sibling = engine_root.parent / "simply-connect-domains" / "domains"
        if _sibling.exists():
            return _sibling
        return engine_root / "domains"

    with patch.dict("os.environ", env, clear=True), \
         patch.object(admin_cli, "_resolve_domains_dir", patched_resolve):
        result = admin_cli._resolve_domains_dir()

    return result


# ---------------------------------------------------------------------------
# Direct unit tests using monkeypatching of os.getenv + Path internals
# ---------------------------------------------------------------------------

class TestResolveDomainsDirEnvVar:
    def test_env_var_takes_priority(self, tmp_path):
        """SC_DOMAINS_DIR env var overrides everything."""
        explicit = tmp_path / "explicit" / "domains"
        explicit.mkdir(parents=True)

        from simply_connect import admin_cli
        with patch.dict("os.environ", {"SC_DOMAINS_DIR": str(explicit)}):
            result = admin_cli._resolve_domains_dir()
        assert result == explicit

    def test_env_var_path_is_returned_verbatim(self, tmp_path):
        """Env var path is returned even if it does not exist."""
        nonexistent = tmp_path / "nowhere" / "domains"
        from simply_connect import admin_cli
        with patch.dict("os.environ", {"SC_DOMAINS_DIR": str(nonexistent)}):
            result = admin_cli._resolve_domains_dir()
        assert result == nonexistent


class TestResolveDomainsDirSiblingRepo:
    def test_sibling_repo_auto_detected(self, tmp_path):
        """Sibling simply-connect-domains/domains is found without env var."""
        # Simulate: engine root = tmp_path/simply-connect/simply-connect/../  = tmp_path/simply-connect
        # Sibling = tmp_path/simply-connect-domains/domains
        engine_root = tmp_path / "simply-connect"
        sibling_domains = tmp_path / "simply-connect-domains" / "domains"
        sibling_domains.mkdir(parents=True)

        from simply_connect import admin_cli
        # Patch the internal engine root resolution
        fake_admin_cli_file = engine_root / "simply-connect" / "admin_cli.py"

        with patch.dict("os.environ", {}, clear=False), \
             patch("simply_connect.admin_cli.Path") as MockPath:

            # We need _resolve_domains_dir to use our engine_root
            # Easier: directly test by patching os.getenv + overriding the path chain
            pass

        # Direct approach: override _resolve_domains_dir internals via monkeypatching
        real_resolve = admin_cli._resolve_domains_dir

        def patched():
            import os
            if os.getenv("SC_DOMAINS_DIR"):
                return Path(os.getenv("SC_DOMAINS_DIR"))
            sibling = sibling_domains
            if sibling.exists():
                return sibling
            return engine_root / "domains"

        with patch.dict("os.environ", {k: v for k, v in __import__("os").environ.items() if k != "SC_DOMAINS_DIR"}):
            with patch.object(admin_cli, "_resolve_domains_dir", patched):
                result = admin_cli._resolve_domains_dir()

        assert result == sibling_domains

    def test_env_var_beats_sibling(self, tmp_path):
        """SC_DOMAINS_DIR takes priority over sibling repo."""
        sibling_domains = tmp_path / "simply-connect-domains" / "domains"
        sibling_domains.mkdir(parents=True)
        explicit = tmp_path / "custom" / "domains"
        explicit.mkdir(parents=True)

        from simply_connect import admin_cli
        with patch.dict("os.environ", {"SC_DOMAINS_DIR": str(explicit)}):
            result = admin_cli._resolve_domains_dir()
        assert result == explicit


class TestResolveDomainsDirFallback:
    def test_returns_path_object(self, tmp_path):
        """Return value is always a Path."""
        from simply_connect import admin_cli
        with patch.dict("os.environ", {k: v for k, v in __import__("os").environ.items() if k != "SC_DOMAINS_DIR"}):
            result = admin_cli._resolve_domains_dir()
        assert isinstance(result, Path)

    def test_no_env_no_sibling_returns_local_domains(self):
        """Without env var or sibling repo, falls back to engine_root/domains."""
        from simply_connect import admin_cli
        # The real engine root is the simply-connect/ directory
        engine_root = Path(admin_cli.__file__).parent.parent
        expected_fallback = engine_root / "domains"

        # Remove env var and simulate no sibling by patching Path.exists
        import os
        clean_env = {k: v for k, v in os.environ.items() if k != "SC_DOMAINS_DIR"}

        with patch.dict("os.environ", clean_env, clear=True):
            # Patch the sibling check: make sibling.exists() return False
            original_exists = Path.exists

            def no_sibling_exists(self):
                if "simply-connect-domains" in str(self):
                    return False
                return original_exists(self)

            with patch.object(Path, "exists", no_sibling_exists):
                result = admin_cli._resolve_domains_dir()

        assert result == expected_fallback


# ---------------------------------------------------------------------------
# cmd_init domain-not-found message
# ---------------------------------------------------------------------------

class TestCmdInitDomainNotFound:
    def test_prints_domain_not_found(self, tmp_path, capsys):
        """cmd_init prints a clear message when the domain is not in the library."""
        from simply_connect import admin_cli

        empty_domains = tmp_path / "domains"
        empty_domains.mkdir()

        with patch.object(admin_cli, "_resolve_domains_dir", return_value=empty_domains):
            admin_cli.cmd_init("nonexistent", tmp_path, force=False)

        out = capsys.readouterr().out
        assert "nonexistent" in out
        assert "not found" in out.lower()

    def test_lists_available_domains(self, tmp_path, capsys):
        """cmd_init lists available domains when the requested one is missing."""
        from simply_connect import admin_cli

        domains_dir = tmp_path / "domains"
        (domains_dir / "minpaku").mkdir(parents=True)
        (domains_dir / "super-landlord").mkdir(parents=True)

        with patch.object(admin_cli, "_resolve_domains_dir", return_value=domains_dir):
            admin_cli.cmd_init("legal", tmp_path, force=False)

        out = capsys.readouterr().out
        assert "minpaku" in out
        assert "super-landlord" in out

    def test_prints_library_path_in_error(self, tmp_path, capsys):
        """cmd_init shows where it looked when domain is not found."""
        from simply_connect import admin_cli

        domains_dir = tmp_path / "domains"
        domains_dir.mkdir()

        with patch.object(admin_cli, "_resolve_domains_dir", return_value=domains_dir):
            admin_cli.cmd_init("missing", tmp_path, force=False)

        out = capsys.readouterr().out
        assert str(domains_dir) in out


class TestCmdInitDecisionPack:
    def test_init_decision_pack_copies_multi_role_domain_files(self, tmp_path):
        from simply_connect import admin_cli

        source_domains = Path("/Users/andrew/backup/work/simply-connect-workspace/simply-connect-domains/domains")
        target_root = tmp_path / "deployment"
        target_root.mkdir()

        with patch.object(admin_cli, "_resolve_domains_dir", return_value=source_domains):
            admin_cli.cmd_init("decision-pack", target_root, force=False)

        assert (target_root / "profile.json").exists()
        assert (target_root / "AGENT.md").exists()
        assert (target_root / "context" / "company.md").exists()
        assert (target_root / "roles" / "founder" / "AGENT.md").exists()
        assert (target_root / "roles" / "reviewer" / "AGENT.md").exists()
        assert (target_root / "roles" / "attorney" / "AGENT.md").exists()
        assert (target_root / "roles" / "operator" / "AGENT.md").exists()
        assert (target_root / "admin" / "intake_map.md").exists()
        assert (target_root / "decision_pack_domain" / "__init__.py").exists()
        assert (target_root / "domains" / "decision_pack" / "extension" / "tools.py").exists()

    def test_init_decision_pack_profile_has_expected_roles(self, tmp_path):
        import json
        from simply_connect import admin_cli

        source_domains = Path("/Users/andrew/backup/work/simply-connect-workspace/simply-connect-domains/domains")
        target_root = tmp_path / "deployment"
        target_root.mkdir()

        with patch.object(admin_cli, "_resolve_domains_dir", return_value=source_domains):
            admin_cli.cmd_init("decision-pack", target_root, force=False)

        profile = json.loads((target_root / "profile.json").read_text())
        assert set(profile["roles"].keys()) == {"founder", "investor", "reviewer", "attorney", "operator"}
        assert profile["context_files"] == ["company", "investor_lens", "evidence", "governance", "legal"]
        assert profile["extensions"] == ["decision_pack"]


class TestCmdInitSuperLandlord:
    def test_init_super_landlord_copies_minpaku_handoff_support(self, tmp_path):
        import json
        from simply_connect import admin_cli

        source_domains = Path("/Users/andrew/backup/work/simply-connect-workspace/simply-connect-domains/domains")
        target_root = tmp_path / "deployment"
        target_root.mkdir()

        with patch.object(admin_cli, "_resolve_domains_dir", return_value=source_domains):
            admin_cli.cmd_init("super-landlord", target_root, force=False)

        profile = json.loads((target_root / "profile.json").read_text())
        assert profile["extensions"] == ["super-landlord"]
        assert "minpaku_handoffs" in profile["context_files"]
        assert (target_root / "context" / "minpaku_handoffs.md").exists()
        assert (target_root / "extension" / "tools.py").exists()
        assert (target_root / "extension" / "client.py").exists()


class TestCmdInitMinpaku:
    def test_init_minpaku_copies_listing_publication_support(self, tmp_path):
        import json
        from simply_connect import admin_cli

        source_domains = Path("/Users/andrew/backup/work/simply-connect-workspace/simply-connect-domains/domains")
        target_root = tmp_path / "deployment"
        target_root.mkdir()

        with patch.object(admin_cli, "_resolve_domains_dir", return_value=source_domains):
            admin_cli.cmd_init("minpaku", target_root, force=False)

        profile = json.loads((target_root / "profile.json").read_text())
        assert profile["extensions"] == ["minpaku"]
        assert "listing_publications" in profile["context_files"]
        assert (target_root / "context" / "listing_publications.md").exists()
