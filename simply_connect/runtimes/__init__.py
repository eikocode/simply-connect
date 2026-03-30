"""Runtime factory for simply-connect.

Select runtime via SC_CLAUDE_RUNTIME env var:
  sdk      (default) — Anthropic SDK, in-process, calls brain.respond()
  cli      / claude  — Claude CLI subprocess with MCP-backed tool access
  kilo               — Kilo CLI subprocess with MCP-backed tool access
  opencode           — OpenCode CLI subprocess with MCP-backed tool access
"""

from .base import ClaudeRuntime


def get_runtime(
    runtime: str = "sdk",
    role_name: str = "operator",
    project_root=None,
    agent_md_path=None,
) -> ClaudeRuntime:
    """Instantiate and return the configured Claude runtime."""
    if runtime == "sdk":
        from .sdk import SDKRuntime
        return SDKRuntime(role_name=role_name)
    if runtime in ("cli", "claude"):
        from .cli import CLIRuntime
        return CLIRuntime(role_name=role_name, project_root=project_root, agent_md_path=agent_md_path)
    if runtime == "kilo":
        from .cli import KiloRuntime
        return KiloRuntime(role_name=role_name, project_root=project_root, agent_md_path=agent_md_path)
    if runtime == "opencode":
        from .cli import OpenCodeRuntime
        return OpenCodeRuntime(role_name=role_name, project_root=project_root, agent_md_path=agent_md_path)
    raise ValueError(f"Unknown runtime: {runtime!r}. Choose 'sdk', 'cli', 'kilo', or 'opencode'.")


__all__ = ["ClaudeRuntime", "get_runtime"]
