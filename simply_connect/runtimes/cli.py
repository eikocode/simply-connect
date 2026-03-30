"""CLI runtimes — run external agent CLIs as subprocesses with MCP config.

The agent subprocess connects to the simply-connect MCP server, which exposes
get_committed_context, get_staging_entries, and capture_to_staging as tools.
The runtime just marshals I/O and session continuity.
"""

import json
import logging
import os
import subprocess
from pathlib import Path

from .base import ClaudeRuntime

log = logging.getLogger(__name__)

# Session storage for CLI (maps user_id -> claude session_id)
_sessions: dict[int, str] = {}


def _find_project_root() -> Path:
    """Walk up from cwd looking for AGENT.md as project root landmark."""
    candidate = Path.cwd()
    for _ in range(6):
        if (candidate / "AGENT.md").exists():
            return candidate
        candidate = candidate.parent
    return Path.cwd()


def _mcp_config_path(project_root: Path | None = None, role_name: str = "operator") -> Path:
    """Return path to mcp_config.json, creating it if needed."""
    root = project_root or _find_project_root()
    config_path = root / "mcp_config.json"
    # Always rewrite so role/env changes are reflected across sessions.
    env = {"PYTHONPATH": str(root), "SC_SESSION_ROLE": role_name}
    for key in (
        "ANTHROPIC_API_KEY",
        "MINPAKU_API_URL",
        "MINPAKU_BASE_URL",
        "MINPAKU_API_KEY",
        "SC_DOCUMENT_PARSER",
        "SC_DOMAINS_DIR",
    ):
        value = os.getenv(key)
        if value:
            env[key] = value
    mcp_config = {
        "mcpServers": {
            "simply-connect": {
                "command": "python",
                "args": ["-m", "simply_connect.mcp_server"],
                "cwd": str(root),
                "env": env,
            }
        }
    }
    config_path.write_text(json.dumps(mcp_config, indent=2))
    return config_path


def _shared_mcp_env(role_name: str) -> dict[str, str]:
    env = {"SC_SESSION_ROLE": role_name}
    for key in (
        "ANTHROPIC_API_KEY",
        "MINPAKU_API_URL",
        "MINPAKU_BASE_URL",
        "MINPAKU_API_KEY",
        "SC_DOCUMENT_PARSER",
        "SC_DOMAINS_DIR",
    ):
        value = os.getenv(key)
        if value:
            env[key] = value
    return env


def _opencode_config_content(project_root: Path, role_name: str, agent_md_path: Path | None = None) -> str:
    config: dict[str, object] = {
        "instructions": [str(agent_md_path or (project_root / "AGENT.md"))],
        "experimental": {"mcp_timeout": 120000},
        "mcp": {
            "simply-connect": {
                "type": "local",
                "enabled": True,
                "command": ["python", "-m", "simply_connect.mcp_server"],
                "environment": {
                    "PYTHONPATH": str(project_root),
                    **_shared_mcp_env(role_name),
                },
                "timeout": 120000,
            }
        },
    }
    return json.dumps(config)


def _extract_claude_message(stdout: str, stderr: str) -> str | None:
    """Pull a user-meaningful message from Claude CLI output, even on nonzero exit."""
    payload = (stdout or "").strip()
    if payload:
        try:
            data = json.loads(payload)
            message = data.get("result") or data.get("text")
            if isinstance(message, str) and message.strip():
                return message.strip()
            if data.get("is_error") and isinstance(data, dict):
                return str(data).strip()
        except json.JSONDecodeError:
            if payload:
                return payload
    err = (stderr or "").strip()
    return err or None


def _extract_jsonl_runtime_message(stdout: str, stderr: str) -> tuple[str | None, str | None]:
    """Parse JSONL event streams like kilo/opencode run --format json."""
    session_id = None
    text_parts: list[str] = []
    errors: list[str] = []

    for line in (stdout or "").splitlines():
        payload = line.strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not session_id:
            session_id = event.get("sessionID") or event.get("sessionId")
        if event.get("type") == "text":
            part = event.get("part") or {}
            text = part.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
        if event.get("type") == "error":
            part = event.get("part") or {}
            errors.append(str(part.get("text") or event))

    message = "\n".join(part for part in text_parts if part).strip()
    if message:
        return message, session_id
    stderr_text = (stderr or "").strip()
    if stderr_text:
        return stderr_text, session_id
    if errors:
        return "\n".join(errors), session_id
    return None, session_id


class CLIRuntime(ClaudeRuntime):
    """Claude CLI runtime.

    Spawns a `claude -p` subprocess for each message. Uses --resume to maintain
    conversation continuity across turns per user. The MCP server handles
    context retrieval and staging via tool calls that claude executes autonomously.
    """

    def __init__(self, role_name: str = "operator", project_root: Path | None = None, agent_md_path: Path | None = None):
        self._role_name = role_name
        self._project_root = project_root or _find_project_root()
        self._agent_md_path = agent_md_path

    def _load_system_prompt(self) -> str:
        path = self._agent_md_path or (self._project_root / "AGENT.md")
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def call(self, user_message: str, user_id: int) -> str:
        """Send a message to claude subprocess and return the reply."""
        mcp_config = _mcp_config_path(self._project_root, self._role_name)
        session_id = _sessions.get(user_id)

        cmd = [
            "claude",
            "--print",
            "--output-format", "json",
            "--mcp-config", str(mcp_config),
            "--allowedTools", "mcp__simply-connect",
        ]

        if session_id:
            cmd += ["--resume", session_id]
        else:
            system_prompt = self._load_system_prompt()
            if system_prompt.strip():
                cmd += ["--system-prompt", system_prompt]

        cmd += ["--", user_message]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(self._project_root),
            )

            if result.returncode != 0:
                message = _extract_claude_message(result.stdout, result.stderr)
                log.error(f"claude subprocess failed: {(message or result.stderr)[:200]}")
                return message or "I encountered an error processing that request. Please try again."

            stdout = result.stdout.strip()
            if not stdout:
                return "No response received."

            # Parse JSON output from claude --output-format json
            try:
                data = json.loads(stdout)
                # Store the session ID for future resume
                new_session_id = data.get("session_id") or data.get("sessionId")
                if new_session_id:
                    _sessions[user_id] = new_session_id
                # Extract the text reply
                reply = data.get("result") or data.get("text") or str(data)
                return reply
            except json.JSONDecodeError:
                # If not JSON, return raw output
                return stdout

        except subprocess.TimeoutExpired:
            log.error(f"claude subprocess timed out for user {user_id}")
            return "Request timed out. Please try a shorter message."
        except FileNotFoundError:
            log.error("'claude' command not found — is Claude Code CLI installed?")
            return "Claude CLI not available. Please check installation."
        except Exception as e:
            log.exception(f"CLIRuntime.call failed for user {user_id}")
            return f"Unexpected error: {e}"

    def reset(self, user_id: int) -> None:
        """Clear conversation session for a user."""
        _sessions.pop(user_id, None)
        log.info(f"CLI session cleared for user {user_id}")


class _JSONLSubprocessRuntime(ClaudeRuntime):
    """Shared runtime for kilo/opencode style JSONL subprocess CLIs."""

    binary_name = ""
    config_env_name = ""

    def __init__(self, role_name: str = "operator", project_root: Path | None = None, agent_md_path: Path | None = None):
        self._role_name = role_name
        self._project_root = project_root or _find_project_root()
        self._agent_md_path = agent_md_path

    def _build_config_env(self) -> dict[str, str]:
        return {
            self.config_env_name: _opencode_config_content(
                self._project_root,
                self._role_name,
                self._agent_md_path,
            )
        }

    def _build_cmd(self, user_message: str, session_id: str | None) -> list[str]:
        cmd = [self.binary_name, "run", "--format", "json"]
        if self.binary_name == "kilo":
            cmd.append("--auto")
        if session_id:
            cmd += ["--session", session_id]
        cmd += [user_message]
        return cmd

    def call(self, user_message: str, user_id: int) -> str:
        session_id = _sessions.get(user_id)
        cmd = self._build_cmd(user_message, session_id)
        env = {
            **os.environ,
            **self._build_config_env(),
        }
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(self._project_root),
                env=env,
            )
            message, new_session_id = _extract_jsonl_runtime_message(result.stdout, result.stderr)
            if new_session_id:
                _sessions[user_id] = new_session_id
            if result.returncode != 0:
                log.error(f"{self.binary_name} subprocess failed: {(message or result.stderr)[:200]}")
                return message or "I encountered an error processing that request. Please try again."
            return message or "No response received."
        except subprocess.TimeoutExpired:
            log.error(f"{self.binary_name} subprocess timed out for user {user_id}")
            return "Request timed out. Please try a shorter message."
        except FileNotFoundError:
            log.error(f"'{self.binary_name}' command not found")
            return f"{self.binary_name} CLI not available. Please check installation."
        except Exception as e:
            log.exception(f"{self.binary_name} runtime failed for user {user_id}")
            return f"Unexpected error: {e}"

    def reset(self, user_id: int) -> None:
        _sessions.pop(user_id, None)
        log.info(f"{self.binary_name} session cleared for user {user_id}")


class KiloRuntime(_JSONLSubprocessRuntime):
    binary_name = "kilo"
    config_env_name = "KILO_CONFIG_CONTENT"


class OpenCodeRuntime(_JSONLSubprocessRuntime):
    binary_name = "opencode"
    config_env_name = "OPENCODE_CONFIG_CONTENT"
