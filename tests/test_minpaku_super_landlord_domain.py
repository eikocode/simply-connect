from pathlib import Path

from simply_connect.context_manager import ContextManager


MINPAKU_ROOT = Path(
    "/Users/andrew/backup/work/simply-connect-workspace/deployments/minpaku"
)
SUPER_LANDLORD_ROOT = Path(
    "/Users/andrew/backup/work/simply-connect-workspace/deployments/super-landlord"
)


def test_minpaku_operator_role_and_starters_are_wired():
    cm = ContextManager(root=MINPAKU_ROOT)

    assert "operator" in cm.roles
    assert "host" in cm.roles

    operator_starters = cm.starter_prompts_for_role("operator")
    assert any("publish" in prompt.lower() for prompt in operator_starters)
    assert any("booking" in prompt.lower() and "payment verification" in prompt.lower() for prompt in operator_starters)


def test_minpaku_operator_role_file_documents_domain_approval_boundary():
    role_path = MINPAKU_ROOT / "roles" / "operator" / "AGENT.md"
    text = role_path.read_text(encoding="utf-8")

    assert "Approval Boundary" in text
    assert "Framework approval (`sc-admin review`) commits staged context." in text
    assert "Domain approval stays here with the operator." in text
    assert "booking is confirmed only after payment verification" in text


def test_super_landlord_operator_profile_and_starters_are_present():
    cm = ContextManager(root=SUPER_LANDLORD_ROOT)

    assert set(cm.roles.keys()) == {"operator"}
    operator_ctx = cm.load_context_for_role("operator")
    assert set(operator_ctx["committed"].keys()) == {
        "properties",
        "tenants",
        "utilities",
        "debit_notes",
        "minpaku_handoffs",
    }

    starters = cm.starter_prompts_for_role("operator")
    assert any("debit note" in prompt.lower() for prompt in starters)
    assert any("available for minpaku" in prompt.lower() for prompt in starters)


def test_super_landlord_root_agent_documents_framework_vs_domain_split():
    agent_text = (SUPER_LANDLORD_ROOT / "AGENT.md").read_text(encoding="utf-8")

    assert "Approval Boundary" in agent_text
    assert "`sc-admin review` decides whether staged information becomes committed context." in agent_text
    assert "`sc --role operator` owns the landlord-facing domain work after that committed state exists." in agent_text
