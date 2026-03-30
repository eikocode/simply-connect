"""
Tests for ContextManager — three-layer context architecture.

These tests use temporary directories and do not require an API key.
"""

import pytest
from pathlib import Path


@pytest.fixture
def project_root(tmp_path):
    """Create a minimal project root with required directories."""
    # Create AGENT.md landmark
    (tmp_path / "AGENT.md").write_text("# Test AGENT.md\n")
    # Create context directory with empty files
    ctx = tmp_path / "context"
    ctx.mkdir()
    for stem in ["business", "parties", "preferences", "contracts"]:
        (ctx / f"{stem}.md").write_text(f"# {stem.capitalize()}\n\n[empty]\n")
    # Create staging directory
    (tmp_path / "staging").mkdir()
    return tmp_path


@pytest.fixture
def cm(project_root):
    """ContextManager instance pointing at temp project root."""
    from simply_connect.context_manager import ContextManager
    return ContextManager(root=project_root)


class TestLoadCommitted:
    def test_loads_all_files(self, cm):
        result = cm.load_committed()
        assert set(result.keys()) == {"business", "parties", "preferences", "contracts"}

    def test_returns_string_content(self, cm):
        result = cm.load_committed()
        for stem, content in result.items():
            assert isinstance(content, str), f"{stem} should be a string"

    def test_missing_file_returns_empty_string(self, cm, project_root):
        (project_root / "context" / "parties.md").unlink()
        result = cm.load_committed()
        assert result["parties"] == ""


class TestCreateStagingEntry:
    def test_creates_file(self, cm, project_root):
        entry_id = cm.create_staging_entry(
            summary="Test entry",
            content="Some content to remember.",
            category="preferences",
            source="operator",
        )
        assert isinstance(entry_id, str) and len(entry_id) == 36  # UUID
        files = list((project_root / "staging").glob("*.md"))
        assert len(files) == 1

    def test_frontmatter_fields(self, cm, project_root):
        entry_id = cm.create_staging_entry(
            summary="Client prefers plain English",
            content="Client ABC wants plain English drafting.",
            category="preferences",
            source="operator",
        )
        entries = cm.list_staging()
        assert len(entries) == 1
        e = entries[0]
        assert e["id"] == entry_id
        assert e["status"] == "unconfirmed"
        assert e["source"] == "operator"
        assert e["category"] == "preferences"
        assert e["summary"] == "Client prefers plain English"
        assert "plain English" in e["content"]

    def test_slug_in_filename(self, cm, project_root):
        cm.create_staging_entry(
            summary="payment terms net 30",
            content="Standard net-30 payment terms.",
            category="contracts",
        )
        files = list((project_root / "staging").glob("*.md"))
        assert any("payment-terms-net-30" in f.name for f in files)


class TestListStaging:
    def test_filters_by_status(self, cm):
        id1 = cm.create_staging_entry("First entry", "Content 1", "business")
        id2 = cm.create_staging_entry("Second entry", "Content 2", "parties")
        # Mark one as approved
        cm.update_staging_status(id1, "approved", "human")

        unconfirmed = cm.list_staging(status="unconfirmed")
        approved = cm.list_staging(status="approved")

        assert len(unconfirmed) == 1
        assert unconfirmed[0]["id"] == id2
        assert len(approved) == 1
        assert approved[0]["id"] == id1

    def test_returns_all_when_no_filter(self, cm):
        cm.create_staging_entry("A", "Content A", "business")
        cm.create_staging_entry("B", "Content B", "parties")
        all_entries = cm.list_staging()
        assert len(all_entries) == 2

    def test_skips_readme(self, cm, project_root):
        cm.create_staging_entry("Real entry", "Content", "business")
        entries = cm.list_staging()
        for e in entries:
            assert "README" not in e.get("filepath", "")


class TestUpdateStagingStatus:
    def test_updates_status(self, cm):
        entry_id = cm.create_staging_entry("Test", "Content", "general")
        cm.update_staging_status(entry_id, "deferred", "human")
        entry = cm.get_staging_entry(entry_id)
        assert entry["status"] == "deferred"
        assert entry["reviewed_by"] == "human"
        assert entry["reviewed_at"] is not None

    def test_returns_false_for_unknown_id(self, cm):
        result = cm.update_staging_status("nonexistent-id", "approved", "human")
        assert result is False


class TestPromoteToCommitted:
    def test_appends_to_context_file(self, cm, project_root):
        entry_id = cm.create_staging_entry(
            summary="Jurisdiction is HK",
            content="This business operates under Hong Kong law.",
            category="business",
        )
        success = cm.promote_to_committed(entry_id, reviewed_by="human")
        assert success is True

        business_content = (project_root / "context" / "business.md").read_text()
        assert "Hong Kong law" in business_content

    def test_marks_entry_approved(self, cm):
        entry_id = cm.create_staging_entry("Test", "Some content.", "preferences")
        cm.promote_to_committed(entry_id, reviewed_by="human")
        entry = cm.get_staging_entry(entry_id)
        assert entry["status"] == "approved"
        assert entry["reviewed_by"] == "human"

    def test_returns_false_for_unknown_id(self, cm):
        result = cm.promote_to_committed("nonexistent-id")
        assert result is False


class TestLoadAllContext:
    def test_returns_expected_structure(self, cm):
        result = cm.load_all_context()
        assert "committed" in result
        assert "staging" in result
        assert isinstance(result["committed"], dict)
        assert isinstance(result["staging"], list)

    def test_staging_contains_only_unconfirmed(self, cm):
        id1 = cm.create_staging_entry("Entry 1", "Content 1", "business")
        id2 = cm.create_staging_entry("Entry 2", "Content 2", "parties")
        cm.update_staging_status(id1, "approved", "human")

        result = cm.load_all_context()
        ids_in_staging = [e["id"] for e in result["staging"]]
        assert id2 in ids_in_staging
        assert id1 not in ids_in_staging


class TestStatusSummary:
    def test_returns_expected_structure(self, cm):
        summary = cm.status_summary()
        assert "committed" in summary
        assert "staging" in summary
        assert isinstance(summary["committed"], list)
        assert isinstance(summary["staging"], dict)

    def test_counts_staging_by_status(self, cm):
        id1 = cm.create_staging_entry("E1", "C1", "business")
        cm.create_staging_entry("E2", "C2", "parties")
        cm.update_staging_status(id1, "approved", "human")

        summary = cm.status_summary()
        assert summary["staging"]["unconfirmed"] == 1
        assert summary["staging"]["approved"] == 1
