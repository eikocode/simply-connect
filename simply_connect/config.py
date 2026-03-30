"""Configuration for simply-connect Telegram relay and MCP server.

Reads from environment variables (or .env file).

Variables:
  ANTHROPIC_API_KEY          — Claude API key (required for SDK runtime and Claude vision)
  SC_TELEGRAM_BOT_TOKEN      — Telegram bot token (required for relay)
  SC_TELEGRAM_ALLOWED_USERS  — Comma-separated Telegram user IDs (empty = all)
  SC_DATA_DIR                — Override for session/staging data directory
  SC_CLAUDE_RUNTIME          — "sdk" (default), "cli"/"claude", "kilo", or "opencode"
  SC_DOCUMENT_PARSER         — "claude" (default) or "docling" (local, no API key needed)
"""
import os

from dotenv import load_dotenv

load_dotenv(override=False)


class Config:
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("SC_TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_ALLOWED_USERS: str = os.getenv("SC_TELEGRAM_ALLOWED_USERS", "")
    SC_DATA_DIR: str = os.getenv("SC_DATA_DIR", "")
    CLAUDE_RUNTIME: str = os.getenv("SC_CLAUDE_RUNTIME", "sdk")
    DOCUMENT_PARSER: str = os.getenv("SC_DOCUMENT_PARSER", "claude")

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
            ok = False
        # Note: bot token validation is done in relay.main() to support per-role tokens
        return ok


config = Config()
