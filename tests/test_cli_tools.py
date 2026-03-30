"""CLI checks for extension-backed tool use."""

from __future__ import annotations

import json
from pathlib import Path


class TestCliWithExtensions:
    def test_cli_uses_tool_path_when_extensions_are_active(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        monkeypatch.chdir(target_root)

        inputs = iter(["Show me the current working state.", "/quit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        called = {"tool_path": False}

        def fake_respond_with_tools(*, message, context, tools, dispatch_fn, history, role, agent_md_path):
            called["tool_path"] = True
            tool_names = {tool["name"] for tool in tools}
            assert "decision_pack_get_working_state" in tool_names
            return {
                "reply": "Working state is empty; create the first submission.",
                "capture": None,
                "confidence": 0.99,
                "used_unconfirmed": False,
                "raw_response": "cli tool path",
            }

        def fail_plain_respond(*args, **kwargs):
            raise AssertionError("CLI should not use plain respond() when extensions are active")

        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fake_respond_with_tools)
        monkeypatch.setattr("simply_connect.brain.respond", fail_plain_respond)

        from simply_connect.cli import main

        monkeypatch.setattr("sys.argv", ["sc", "--data-dir", str(target_root), "--role", "founder"])
        main()

        out = capsys.readouterr().out
        assert called["tool_path"] is True
        assert "Working state is empty" in out

    def test_cli_starter_and_help_commands_show_role_prompts(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli

        target_root = tmp_path / "decision-pack-project"
        target_root.mkdir()
        admin_cli.cmd_init("decision-pack", target_root, force=False)

        monkeypatch.chdir(target_root)

        inputs = iter(["/starter", "/help", "/quit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        def fail_tool_path(*args, **kwargs):
            raise AssertionError("/starter and /help should not invoke model/tool paths")

        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fail_tool_path)
        monkeypatch.setattr("simply_connect.brain.respond", fail_tool_path)

        from simply_connect.cli import main

        monkeypatch.setattr("sys.argv", ["sc", "--data-dir", str(target_root), "--role", "reviewer"])
        main()

        out = capsys.readouterr().out
        assert "Starter prompts for reviewer" in out
        assert "Hold the active material change for policy review." in out
        assert "/starter — role-specific starter prompts" in out
        assert "Create and assess a new FluxHalo submission." not in out

    def test_cli_ingest_command_stages_document_without_model_path(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        bill_path = target_root / "bill.pdf"
        bill_path.write_text("fake bill", encoding="utf-8")

        monkeypatch.chdir(target_root)

        inputs = iter([f"ingest {bill_path.name}", "/quit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        def fake_ingest(cm, filepath):
            assert filepath == bill_path
            return {
                "ok": True,
                "filepath": str(filepath),
                "entries": [
                    {
                        "entry_id": "entry-1",
                        "summary": "CLP electricity bill for Unit B",
                        "category": "utilities",
                    }
                ],
            }

        def fail_tool_path(*args, **kwargs):
            raise AssertionError("ingest command should not invoke model/tool paths")

        monkeypatch.setattr("simply_connect.admin_cli.ingest_to_staging", fake_ingest)
        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fail_tool_path)
        monkeypatch.setattr("simply_connect.brain.respond", fail_tool_path)

        from simply_connect.cli import main

        monkeypatch.setattr("sys.argv", ["sc", "--data-dir", str(target_root), "--role", "operator"])
        main()

        out = capsys.readouterr().out
        assert "Ingested bill.pdf into staging." in out
        assert "Staged (utilities): CLP electricity bill for Unit B" in out
        assert "Next: run sc-admin review to approve and commit." in out

    def test_cli_uses_claude_cli_runtime_when_configured(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        monkeypatch.chdir(target_root)
        monkeypatch.setattr("simply_connect.config.config.CLAUDE_RUNTIME", "cli")

        inputs = iter(["List the current properties.", "/quit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        called = {"runtime": False}

        class FakeRuntime:
            def call(self, user_message, user_id):
                called["runtime"] = True
                assert user_message == "List the current properties."
                return "Sakura House Namba"

        def fake_get_runtime(runtime, role_name="operator", project_root=None, agent_md_path=None):
            assert runtime == "cli"
            assert role_name == "host"
            assert project_root == target_root
            assert agent_md_path == target_root / "roles" / "host" / "AGENT.md"
            return FakeRuntime()

        def fail_plain_respond(*args, **kwargs):
            raise AssertionError("CLI should not use in-process brain path when SC_CLAUDE_RUNTIME=cli")

        monkeypatch.setattr("simply_connect.runtimes.get_runtime", fake_get_runtime)
        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fail_plain_respond)
        monkeypatch.setattr("simply_connect.brain.respond", fail_plain_respond)

        from simply_connect.cli import main

        monkeypatch.setattr("sys.argv", ["sc", "--data-dir", str(target_root), "--role", "host"])
        main()

        out = capsys.readouterr().out
        assert called["runtime"] is True
        assert "Sakura House Namba" in out

    def test_cli_uses_opencode_runtime_when_configured(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)
        (target_root / ".env").write_text("SC_CLAUDE_RUNTIME=opencode\n", encoding="utf-8")

        monkeypatch.chdir(target_root)
        monkeypatch.setenv("SC_CLAUDE_RUNTIME", "sdk")

        inputs = iter(["List the current properties.", "/quit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        called = {"runtime": False}

        class FakeRuntime:
            def call(self, user_message, user_id):
                called["runtime"] = True
                assert user_message == "List the current properties."
                return "Local opencode runtime"

        def fake_get_runtime(runtime, role_name="operator", project_root=None, agent_md_path=None):
            assert runtime == "opencode"
            assert role_name == "host"
            assert project_root == target_root
            assert agent_md_path == target_root / "roles" / "host" / "AGENT.md"
            return FakeRuntime()

        def fail_plain_respond(*args, **kwargs):
            raise AssertionError("CLI should not use in-process brain path when SC_CLAUDE_RUNTIME=opencode")

        monkeypatch.setattr("simply_connect.runtimes.get_runtime", fake_get_runtime)
        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fail_plain_respond)
        monkeypatch.setattr("simply_connect.brain.respond", fail_plain_respond)

        from simply_connect.cli import main

        monkeypatch.setattr("sys.argv", ["sc", "--data-dir", str(target_root), "--role", "host"])
        main()

        out = capsys.readouterr().out
        assert called["runtime"] is True
        assert "Local opencode runtime" in out

    def test_cli_runtime_subprocess_allows_simply_connect_mcp_tools(self, tmp_path, monkeypatch):
        from simply_connect.runtimes.cli import CLIRuntime

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "AGENT.md").write_text("# AGENT\n")

        captured = {}

        class Result:
            returncode = 0
            stdout = json.dumps({"result": "OK", "session_id": "sess-1"})
            stderr = ""

        def fake_run(cmd, capture_output, text, timeout, cwd):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        runtime = CLIRuntime(role_name="host", project_root=project_root, agent_md_path=project_root / "AGENT.md")
        reply = runtime.call("List the current properties.", user_id=1)

        assert reply == "OK"
        assert "--allowedTools" in captured["cmd"]
        idx = captured["cmd"].index("--allowedTools")
        assert captured["cmd"][idx + 1] == "mcp__simply-connect"

    def test_cli_runtime_surfaces_structured_claude_error_message(self, tmp_path, monkeypatch):
        from simply_connect.runtimes.cli import CLIRuntime

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "AGENT.md").write_text("# AGENT\n")

        class Result:
            returncode = 1
            stdout = json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "result": "You've hit your limit · resets 3am (America/Los_Angeles)",
                }
            )
            stderr = ""

        def fake_run(cmd, capture_output, text, timeout, cwd):
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        runtime = CLIRuntime(role_name="host", project_root=project_root, agent_md_path=project_root / "AGENT.md")
        reply = runtime.call("show all properties", user_id=1)

        assert "You've hit your limit" in reply

    def test_get_runtime_supports_kilo_and_opencode(self, tmp_path):
        from simply_connect.runtimes import get_runtime
        from simply_connect.runtimes.cli import KiloRuntime, OpenCodeRuntime

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "AGENT.md").write_text("# AGENT\n")

        kilo_runtime = get_runtime("kilo", role_name="host", project_root=project_root, agent_md_path=project_root / "AGENT.md")
        opencode_runtime = get_runtime("opencode", role_name="host", project_root=project_root, agent_md_path=project_root / "AGENT.md")

        assert isinstance(kilo_runtime, KiloRuntime)
        assert isinstance(opencode_runtime, OpenCodeRuntime)

    def test_kilo_runtime_parses_jsonl_and_sets_config_env(self, tmp_path, monkeypatch):
        from simply_connect.runtimes.cli import KiloRuntime

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "AGENT.md").write_text("# AGENT\n")

        captured = {}

        class Result:
            returncode = 0
            stdout = "\n".join(
                [
                    json.dumps({"type": "step_start", "sessionID": "ses-kilo-1"}),
                    json.dumps({"type": "text", "sessionID": "ses-kilo-1", "part": {"text": "Hello from kilo"}}),
                ]
            )
            stderr = ""

        def fake_run(cmd, capture_output, text, timeout, cwd, env):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            captured["env"] = env
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        runtime = KiloRuntime(role_name="host", project_root=project_root, agent_md_path=project_root / "AGENT.md")
        reply = runtime.call("show all properties", user_id=7)

        assert reply == "Hello from kilo"
        assert captured["cmd"][:4] == ["kilo", "run", "--format", "json"]
        assert "--auto" in captured["cmd"]
        assert "KILO_CONFIG_CONTENT" in captured["env"]
        config = json.loads(captured["env"]["KILO_CONFIG_CONTENT"])
        assert config["mcp"]["simply-connect"]["type"] == "local"
        assert config["mcp"]["simply-connect"]["command"] == ["python", "-m", "simply_connect.mcp_server"]
        assert config["mcp"]["simply-connect"]["environment"]["SC_SESSION_ROLE"] == "host"
        assert config["instructions"] == [str(project_root / "AGENT.md")]

    def test_opencode_runtime_parses_jsonl_and_sets_config_env(self, tmp_path, monkeypatch):
        from simply_connect.runtimes.cli import OpenCodeRuntime

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "AGENT.md").write_text("# AGENT\n")

        captured = {}

        class Result:
            returncode = 0
            stdout = "\n".join(
                [
                    json.dumps({"type": "step_start", "sessionID": "ses-open-1"}),
                    json.dumps({"type": "text", "sessionID": "ses-open-1", "part": {"text": "Hello from opencode"}}),
                ]
            )
            stderr = ""

        def fake_run(cmd, capture_output, text, timeout, cwd, env):
            captured["cmd"] = cmd
            captured["cwd"] = cwd
            captured["env"] = env
            return Result()

        monkeypatch.setattr("subprocess.run", fake_run)

        runtime = OpenCodeRuntime(role_name="host", project_root=project_root, agent_md_path=project_root / "AGENT.md")
        reply = runtime.call("show all properties", user_id=8)

        assert reply == "Hello from opencode"
        assert captured["cmd"][:4] == ["opencode", "run", "--format", "json"]
        assert "--auto" not in captured["cmd"]
        assert "OPENCODE_CONFIG_CONTENT" in captured["env"]
        config = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
        assert config["mcp"]["simply-connect"]["type"] == "local"
        assert config["mcp"]["simply-connect"]["environment"]["SC_SESSION_ROLE"] == "host"

    def test_cli_runtime_writes_guest_role_into_mcp_config_for_minpaku(self, tmp_path, monkeypatch):
        from simply_connect import admin_cli
        import json

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        monkeypatch.chdir(target_root)

        from simply_connect.runtimes.cli import _mcp_config_path

        config_path = _mcp_config_path(target_root, "guest")
        config = json.loads(config_path.read_text())
        env = config["mcpServers"]["simply-connect"]["env"]
        assert env["SC_SESSION_ROLE"] == "guest"

    def test_cli_refreshes_role_aware_context_each_turn_after_admin_review(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager

        target_root = tmp_path / "minpaku-project"
        target_root.mkdir()
        admin_cli.cmd_init("minpaku", target_root, force=False)

        cm = ContextManager(root=target_root)
        entry_id = cm.create_staging_entry(
            summary="Minpaku listing draft for Harbour View",
            content="## Draft\n",
            category="listing_publications",
            source="operator",
        )

        monkeypatch.chdir(target_root)

        inputs = iter(["first turn", "second turn", "/quit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        seen = {"calls": 0}

        def fake_respond_with_tools(*, message, context, tools, dispatch_fn, history, role, agent_md_path):
            seen["calls"] += 1
            if seen["calls"] == 1:
                assert role == "host"
                assert len(context["staging"]) == 1
                assert context["staging"][0]["id"] == entry_id
                cm.update_staging_status(entry_id, "approved", "human")
                return {
                    "reply": "First turn saw one pending entry.",
                    "capture": None,
                    "confidence": 0.99,
                    "used_unconfirmed": False,
                    "raw_response": "first",
                }
            assert len(context["staging"]) == 0
            assert set(context["committed"].keys()) == {"properties", "operations", "pricing", "contacts"}
            return {
                "reply": "Second turn saw zero pending entries.",
                "capture": None,
                "confidence": 0.99,
                "used_unconfirmed": False,
                "raw_response": "second",
            }

        def fail_plain_respond(*args, **kwargs):
            raise AssertionError("CLI should use extension tool path in this test")

        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fake_respond_with_tools)
        monkeypatch.setattr("simply_connect.brain.respond", fail_plain_respond)

        from simply_connect.cli import main

        monkeypatch.setattr("sys.argv", ["sc", "--data-dir", str(target_root), "--role", "host"])
        main()

        out = capsys.readouterr().out
        assert "First turn saw one pending entry." in out
        assert "Second turn saw zero pending entries." in out
        assert seen["calls"] == 2

    def test_super_landlord_starter_prompts_include_minpaku_handoff_flow(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)

        monkeypatch.chdir(target_root)

        inputs = iter(["/starter", "/quit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        def fail_tool_path(*args, **kwargs):
            raise AssertionError("/starter should not invoke model/tool paths")

        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fail_tool_path)
        monkeypatch.setattr("simply_connect.brain.respond", fail_tool_path)

        from simply_connect.cli import main

        monkeypatch.setattr("sys.argv", ["sc", "--data-dir", str(target_root)])
        main()

        out = capsys.readouterr().out
        assert "Mark 12 Harbour View Road, Unit A & B available for Minpaku." in out

    def test_super_landlord_direct_handoff_message_creates_unconfirmed_staging(self, tmp_path, monkeypatch, capsys):
        from simply_connect import admin_cli
        from simply_connect.context_manager import ContextManager

        target_root = tmp_path / "super-landlord-project"
        target_root.mkdir()
        admin_cli.cmd_init("super-landlord", target_root, force=False)
        baseline_cm = ContextManager(root=target_root)
        baseline_pending = len(baseline_cm.list_staging(status="unconfirmed"))

        monkeypatch.chdir(target_root)
        monkeypatch.setattr("simply_connect.config.config.CLAUDE_RUNTIME", "cli")

        inputs = iter(["Mark 12 Harbour View Road, Unit A & B available for Minpaku.", "/quit"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))

        called = {"runtime": False, "respond_with_tools": False, "respond": False}

        class FakeRuntime:
            def call(self, user_message, user_id):
                called["runtime"] = True
                return "runtime should not be used"

        def fake_get_runtime(*args, **kwargs):
            return FakeRuntime()

        def fail_respond_with_tools(*args, **kwargs):
            called["respond_with_tools"] = True
            raise AssertionError("Direct handoff intent should not fall through to respond_with_tools")

        def fail_respond(*args, **kwargs):
            called["respond"] = True
            raise AssertionError("Direct handoff intent should not fall through to respond")

        monkeypatch.setattr("simply_connect.runtimes.get_runtime", fake_get_runtime)
        monkeypatch.setattr("simply_connect.brain.respond_with_tools", fail_respond_with_tools)
        monkeypatch.setattr("simply_connect.brain.respond", fail_respond)

        from simply_connect.cli import main

        monkeypatch.setattr("sys.argv", ["sc", "--data-dir", str(target_root)])
        main()

        out = capsys.readouterr().out
        assert "Handoff staged:" in out

        cm = ContextManager(root=target_root)
        pending = cm.list_staging(status="unconfirmed")
        assert len(pending) == baseline_pending + 1
        matching = [entry for entry in pending if "12 Harbour View Road, Unit A & B" in entry["summary"]]
        assert matching
        assert matching[-1]["category"] == "minpaku_handoffs"
        assert called == {"runtime": False, "respond_with_tools": False, "respond": False}
