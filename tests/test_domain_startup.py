from pathlib import Path


DEPLOYMENTS = {
    "minpaku": {
        "root": Path("/Users/andrew/backup/work/simply-connect-workspace/deployments/minpaku"),
        "role": "operator",
        "header": "Starter prompts for operator",
        "expected_text": "Publish the latest listing draft.",
    },
    "super_landlord": {
        "root": Path("/Users/andrew/backup/work/simply-connect-workspace/deployments/super-landlord"),
        "role": "operator",
        "header": "Starter prompts for operator",
        "expected_text": "Mark 12 Harbour View Road, Unit A & B available for Minpaku.",
    },
    "legal_contracts": {
        "root": Path("/Users/andrew/backup/work/simply-connect-workspace/deployments/legal-contracts"),
        "role": "operator",
        "header": "Starter prompts for operator",
        "expected_text": "Review Contract-PDF-Samples/contract1_hardware_supplier_agreement.md",
    },
    "decision_pack": {
        "root": Path("/Users/andrew/backup/work/simply-connect-workspace/deployments/decision-pack"),
        "role": "operator",
        "header": "Starter prompts for operator",
        "expected_text": "Show the operator overview for the governed state.",
    },
}


def _run_starter(monkeypatch, capsys, root: Path, role: str) -> str:
    from simply_connect.cli import main

    inputs = iter(["/starter", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr(
        "sys.argv",
        ["sc", "--data-dir", str(root), "--role", role],
    )
    main()
    return capsys.readouterr().out


def test_main_deployments_starter_prompts_load_without_role_warnings(monkeypatch, capsys):
    outputs = {}

    for key, config in DEPLOYMENTS.items():
        outputs[key] = _run_starter(monkeypatch, capsys, config["root"], config["role"])

    for key, config in DEPLOYMENTS.items():
        out = outputs[key]
        assert config["header"] in out
        assert config["expected_text"] in out
        assert "Warning: role" not in out
