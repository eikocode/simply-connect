"""Configuration for simply-connect Telegram relay and MCP server.

Reads from environment variables (or .env file).

Variables:
  ANTHROPIC_API_KEY          — Claude API key (required for SDK runtime and Claude vision)
  SC_TELEGRAM_BOT_TOKEN      — Telegram bot token (required for relay)
  SC_TELEGRAM_ALLOWED_USERS  — Comma-separated Telegram user IDs (empty = all)
  SC_DATA_DIR                — Override for session/staging data directory
  SC_CLAUDE_RUNTIME          — "sdk" (default), "cli"/"claude", "kilo", or "opencode"
  SC_DOCUMENT_PARSER         — "claude" (default) or "docling" (local, no API key needed)
  SC_INTELLIGENCE_MODEL      — "haiku" (default), "sonnet", or "auto" (domain decides per-type)
  SC_FORCE_VISION            — "1" to skip EYES text extraction and always use Claude vision
  SC_LLM_BACKEND             — "anthropic" (default), "openai", or "gemini" (future)
"""
import os

from dotenv import load_dotenv

load_dotenv(override=False)


class Config:
    ANTHROPIC_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_ALLOWED_USERS: str = ""
    SC_DATA_DIR: str = ""
    CLAUDE_RUNTIME: str = "sdk"
    DOCUMENT_PARSER: str = "claude"
    INTELLIGENCE_MODEL: str = "haiku"
    FORCE_VISION: bool = False
    LLM_BACKEND: str = "anthropic"

    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
        self.TELEGRAM_BOT_TOKEN = os.getenv("SC_TELEGRAM_BOT_TOKEN", "")
        self.TELEGRAM_ALLOWED_USERS = os.getenv("SC_TELEGRAM_ALLOWED_USERS", "")
        self.SC_DATA_DIR = os.getenv("SC_DATA_DIR", "")
        self.CLAUDE_RUNTIME = os.getenv("SC_CLAUDE_RUNTIME", "sdk")
        self.DOCUMENT_PARSER = os.getenv("SC_DOCUMENT_PARSER", "claude")
        self.INTELLIGENCE_MODEL = os.getenv("SC_INTELLIGENCE_MODEL", "haiku")
        self.FORCE_VISION = os.getenv("SC_FORCE_VISION", "").strip() in ("1", "true", "yes")
        self.LLM_BACKEND = os.getenv("SC_LLM_BACKEND", "anthropic").strip().lower()

    def allowed_users(self) -> list[int]:
        """Return list of allowed Telegram user IDs. Empty means all allowed."""
        raw = self.TELEGRAM_ALLOWED_USERS
        if not raw:
            return []
        return [int(u.strip()) for u in raw.split(",") if u.strip().isdigit()]

    def validate(self) -> bool:
        """Validate required configuration. Prints errors and returns False if invalid."""
        ok = True
        if not self.TELEGRAM_BOT_TOKEN:
            print("ERROR: SC_TELEGRAM_BOT_TOKEN is not set")
            ok = False
        if self.CLAUDE_RUNTIME not in ("sdk", "cli", "claude", "kilo", "opencode"):
            print(
                "ERROR: SC_CLAUDE_RUNTIME must be 'sdk', 'cli'/'claude', 'kilo', or 'opencode', "
                f"got '{self.CLAUDE_RUNTIME}'"
            )
            ok = False
        if self.DOCUMENT_PARSER not in ("claude", "docling"):
            print(f"ERROR: SC_DOCUMENT_PARSER must be 'claude' or 'docling', got '{self.DOCUMENT_PARSER}'")
        if self.INTELLIGENCE_MODEL not in ("haiku", "sonnet", "auto"):
            print(f"WARNING: SC_INTELLIGENCE_MODEL should be 'haiku', 'sonnet', or 'auto', got '{self.INTELLIGENCE_MODEL}'")
            ok = False
        if self.LLM_BACKEND not in ("anthropic", "claude", "openai", "codex"):
            print(f"WARNING: SC_LLM_BACKEND should be 'anthropic' or 'openai', got '{self.LLM_BACKEND}'")
            ok = False
        # Note: bot token validation is done in relay.main() to support per-role tokens
        return ok


config = Config()
