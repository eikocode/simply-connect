from pathlib import Path

from simply_connect.context_manager import ContextManager


LEGAL_CONTRACTS_ROOT = Path(
    "/Users/andrew/backup/work/simply-connect-workspace/deployments/legal-contracts"
)


def test_legal_contracts_profile_exposes_expected_roles_and_starters():
    cm = ContextManager(root=LEGAL_CONTRACTS_ROOT)

    assert set(cm.roles.keys()) == {"operator", "counsel", "reviewer", "compliance", "business"}

    reviewer_starters = cm.starter_prompts_for_role("reviewer")
    compliance_starters = cm.starter_prompts_for_role("compliance")
    business_starters = cm.starter_prompts_for_role("business")

    assert any("top legal risks" in prompt.lower() for prompt in reviewer_starters)
    assert any("gdpr" in prompt.lower() or "hipaa" in prompt.lower() for prompt in compliance_starters)
    assert any("plain english" in prompt.lower() or "non-lawyer" in prompt.lower() for prompt in business_starters)


def test_legal_contracts_role_context_filters_match_profile_intent():
    cm = ContextManager(root=LEGAL_CONTRACTS_ROOT)

    compliance_ctx = cm.load_context_for_role("compliance")
    business_ctx = cm.load_context_for_role("business")

    assert set(compliance_ctx["committed"].keys()) == {"contracts", "compliance", "products"}
    assert set(business_ctx["committed"].keys()) == {"contracts", "counterparties", "products"}
    assert "staging" in compliance_ctx
    assert isinstance(compliance_ctx["staging"], list)


def test_legal_contracts_agent_documents_framework_vs_domain_boundary():
    agent_text = (LEGAL_CONTRACTS_ROOT / "AGENT.md").read_text(encoding="utf-8")

    assert "Framework vs Domain Boundary" in agent_text
    assert "`sc-admin review` is the simply-connect framework approval step." in agent_text
    assert "Legal judgment stays in domain roles inside `sc`." in agent_text


def test_legal_contracts_role_agent_files_exist_and_are_distinct():
    role_texts = {}
    for role_name in ("operator", "counsel", "reviewer", "compliance", "business"):
        role_path = LEGAL_CONTRACTS_ROOT / "roles" / role_name / "AGENT.md"
        assert role_path.exists()
        text = role_path.read_text(encoding="utf-8")
        assert text.strip()
        role_texts[role_name] = text

    assert "severity" in role_texts["reviewer"].lower()
    assert "redline" in role_texts["counsel"].lower() or "draft" in role_texts["counsel"].lower()
    assert "gdpr" in role_texts["compliance"].lower() or "hipaa" in role_texts["compliance"].lower()
    assert "plain english" in role_texts["business"].lower() or "non-lawyer" in role_texts["business"].lower()
