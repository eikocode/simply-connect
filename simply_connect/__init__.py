"""
simply-connect — Committed-context agent framework with swappable domain profiles.

Three-layer memory architecture:
  Layer 1 — Committed context (context/*.md)  authoritative, admin-controlled
  Layer 2 — Staging (staging/*.md)            candidate updates, pending review
  Layer 3 — Session memory                    ephemeral, per-session

Two roles:
  Operator  — uses sc / simply-connect for daily domain work
  Admin     — uses sc-admin / simply-connect-admin to govern context

Three surfaces:
  CLI       — sc (operator), sc-admin (admin)
  Telegram  — sc-relay (operator bot)
  WebMCP    — sc-mcp --http + webmcp.html (browser surface)

Starter profiles:
  decision-pack           — multi-role underwriting workflow
  legal-counsel          — contract drafting and matter management
  landlord-debit-notes   — property management, utility bills, debit notes

Quick start:
  pip install -e .
  mkdir -p ../deployments/decision-pack
  sc-admin --data-dir ../deployments/decision-pack init decision-pack
  cd ../deployments/decision-pack
  sc --role founder                    # start role-aware CLI session
  sc-relay                             # start Telegram bot
  sc-mcp --http                        # start MCP server for WebMCP
  sc-admin status                      # check context health
  sc-admin intake                      # bootstrap from AIOS context files
  sc-admin ingest bill.pdf             # ingest a document into staging
  sc-admin review                      # review and approve staged updates
"""

from .brain import respond, review_staging_entry
from .context_manager import ContextManager
from .session_manager import SessionManager
from .runtimes import get_runtime, ClaudeRuntime
from .ingestion import ingest_document

__all__ = [
    "respond",
    "review_staging_entry",
    "ContextManager",
    "SessionManager",
    "get_runtime",
    "ClaudeRuntime",
    "ingest_document",
]
