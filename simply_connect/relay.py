#!/usr/bin/env python3
"""Telegram relay for simply-connect operator assistant.

Polls Telegram for messages and dispatches to the configured Claude runtime.
Supports text messages, photos, and document (PDF/image) uploads.

Claude runtime is selected via SC_CLAUDE_RUNTIME env var:
  sdk  (default) — Anthropic SDK, in-process, brain.respond() handles context
  cli            — claude -p subprocess, MCP server handles context via tools

Document parser is selected via SC_DOCUMENT_PARSER env var:
  claude   (default) — Claude vision API; requires ANTHROPIC_API_KEY
  docling             — local Docling parser; no API key needed

Usage:
  python -m simply_connect.relay
  sc-relay
"""
import os
import sys
import tempfile
import time
from pathlib import Path

import requests

from .config import config
from .runtimes import get_runtime


# Mime types accepted for document upload
_SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
}

_MIME_TO_SUFFIX = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


class TelegramRelay:
    """Relay messages between Telegram and Claude via the configured runtime."""

    def __init__(self, token: str, role_name: str = "operator") -> None:
        self.token = token
        self.role_name = role_name
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.file_url = f"https://api.telegram.org/file/bot{token}"
        self.offset = 0
        self.runtime = get_runtime(config.CLAUDE_RUNTIME, role_name=role_name)

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    def get_updates(self, timeout: int = 30) -> list[dict]:
        try:
            response = requests.get(
                f"{self.api_url}/getUpdates",
                params={"timeout": timeout, "offset": self.offset + 1},
                timeout=timeout + 10,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                return data.get("result", [])
            return []
        except requests.RequestException as e:
            print(f"Error getting updates: {e}")
            return []

    def send_message(self, chat_id: int, text: str, parse_mode: str = "HTML") -> bool:
        try:
            response = requests.post(
                f"{self.api_url}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
                timeout=30,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"Error sending message: {e}")
            return False

    def send_typing(self, chat_id: int) -> None:
        try:
            requests.post(
                f"{self.api_url}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
                timeout=10,
            )
        except Exception:
            pass

    def download_file(self, file_id: str) -> bytes:
        """Download a file from Telegram by file_id. Returns raw bytes."""
        # Step 1: get the file path from Telegram
        response = requests.get(
            f"{self.api_url}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"getFile failed: {data.get('description', 'unknown error')}")
        file_path = data["result"]["file_path"]

        # Step 2: download the actual bytes
        dl_response = requests.get(
            f"{self.file_url}/{file_path}",
            timeout=60,
        )
        dl_response.raise_for_status()
        return dl_response.content

    # ------------------------------------------------------------------
    # Document upload handler
    # ------------------------------------------------------------------

    def handle_document(self, chat_id: int, user_id: int, message: dict) -> None:
        """Handle photo or document upload — ingest into staging."""
        caption = message.get("caption", "")

        # Resolve file_id and suffix
        if "photo" in message:
            # photos are an array of sizes — take the largest
            file_id = message["photo"][-1]["file_id"]
            suffix = ".jpg"
            label = "photo"
        elif "document" in message:
            doc = message["document"]
            mime_type = doc.get("mime_type", "")
            if mime_type not in _SUPPORTED_MIME_TYPES:
                self.send_message(
                    chat_id,
                    f"⚠️ Unsupported file type: <code>{mime_type or 'unknown'}</code>\n\n"
                    "Send a PDF or image (JPG, PNG, WEBP).",
                )
                return
            file_id = doc["file_id"]
            suffix = _MIME_TO_SUFFIX.get(mime_type, ".bin")
            label = doc.get("file_name", f"document{suffix}")
        else:
            return

        self.send_typing(chat_id)
        self.send_message(chat_id, f"📄 Reading {label}…")

        tmp_path = None
        try:
            # Download bytes from Telegram
            file_bytes = self.download_file(file_id)

            # Extension document hook — allows domains to override default staging
            from .context_manager import ContextManager
            from .ext_loader import maybe_handle_document as _ext_doc
            cm = ContextManager()
            mime_type = (message.get("photo") and "image/jpeg") or \
                        (message.get("document") and message["document"].get("mime_type", ""))
            ext_reply = _ext_doc(
                file_bytes, label, mime_type or "", caption, cm,
                role_name=self.role_name, user_id=user_id,
            )
            if ext_reply is not None:
                self.send_message(chat_id, ext_reply)
                return

            # Write to temp file so ingest_document() can read it
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(file_bytes)
                tmp_path = Path(f.name)

            # Ingest
            from .ingestion import ingest_document

            committed = cm.load_committed()
            result = ingest_document(
                tmp_path,
                committed,
                cm._profile,
                parser=config.DOCUMENT_PARSER,
            )

            if not result.get("success"):
                self.send_message(
                    chat_id,
                    f"⚠️ Could not read document:\n<code>{result.get('error', 'unknown error')}</code>",
                )
                return

            extractions = result.get("extractions", [])
            if not extractions:
                self.send_message(
                    chat_id,
                    "📄 Document read — no structured content found to stage.\n\n"
                    "The document may be empty or contain only template text.",
                )
                return

            # Create staging entries
            staged = 0
            for item in extractions:
                source_label = f"relay:{label}"
                if caption:
                    source_label += f" ({caption[:40]})"
                cm.create_staging_entry(
                    summary=item.get("summary", label),
                    content=item.get("content", ""),
                    category=item.get("category", "general"),
                    source=source_label,
                )
                staged += 1

            parser_label = result.get("parser", config.DOCUMENT_PARSER)
            reply = (
                f"📄 <b>{staged} item{'s' if staged != 1 else ''} staged for review</b>\n\n"
                f"Parser: <code>{parser_label}</code>\n"
                f"Run <code>sc-admin review</code> to approve and commit."
            )
            if caption:
                reply += f"\n\nCaption: <i>{caption}</i>"
            self.send_message(chat_id, reply)

        except Exception as e:
            log.error(f"handle_document failed: {e}")
            self.send_message(
                chat_id,
                f"⚠️ Failed to process document:\n<code>{e}</code>",
            )
        finally:
            if tmp_path and tmp_path.exists():
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def handle_command(self, chat_id: int, user_id: int, text: str) -> None:
        cmd = text.split()[0].lower()

        if cmd == "/start":
            self.send_message(
                chat_id,
                "📄 <b>Simply-Connect Assistant</b>\n"
                "<i>Your AI-powered domain working partner</i>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "I can help you:\n"
                "• Answer questions using your committed context\n"
                "• Draft documents and review terms\n"
                "• Remember things for future sessions\n"
                "• 📎 Stage uploaded photos and PDFs for review\n\n"
                "Try: <i>\"What contracts do we have on file?\"</i>\n"
                "Or send a PDF or photo to stage it.\n\n"
                "Use /help for all commands.\n\n"
                "What are you working on today?",
            )
            return

        if cmd == "/help":
            self.send_message(
                chat_id,
                "📖 <b>Commands</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "/start — Welcome message\n"
                "/status — Context health dashboard\n"
                "/reset — Clear conversation history\n"
                "/help — Show this menu\n\n"
                "<b>Or just chat naturally!</b>\n"
                "\"What do we know about party X?\"\n"
                "\"Note that our standard payment term is 30 days\"\n\n"
                "<b>📎 Document upload</b>\n"
                "Send any PDF or photo to stage it for review.\n"
                f"Parser: <code>{config.DOCUMENT_PARSER}</code>  "
                f"(SC_DOCUMENT_PARSER=claude|docling)",
            )
            return

        if cmd == "/status":
            self._handle_status(chat_id)
            return

        if cmd == "/reset":
            self.runtime.reset(user_id)
            self.send_message(
                chat_id,
                "🔄 Conversation cleared.\n\n"
                "Context and staging are preserved — only the chat history was reset.\n\n"
                "What are you working on?",
            )
            return

        # Unknown command — pass to runtime
        self.send_typing(chat_id)
        response = self.runtime.call(text, user_id)
        self._send_chunked(chat_id, response)

    def _handle_status(self, chat_id: int) -> None:
        """Show context health dashboard."""
        try:
            from .context_manager import ContextManager
            cm = ContextManager()
            summary = cm.status_summary()

            committed = summary.get("committed", [])
            staging = summary.get("staging", {})

            committed_lines = []
            for info in committed:
                words = info.get("words", 0)
                populated = "✓" if words else "○"
                committed_lines.append(f"  {populated} {info['file']} ({words} words)")

            unconfirmed = staging.get("unconfirmed", 0)
            approved = staging.get("approved", 0)
            rejected = staging.get("rejected", 0)
            deferred = staging.get("deferred", 0)

            status_text = (
                "📊 <b>Context Status</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "<b>Committed Context</b>\n"
                + "\n".join(committed_lines or ["  (no context files)"])
                + "\n\n"
                "<b>Staging Queue</b>\n"
                f"  ⏳ Unconfirmed: {unconfirmed}\n"
                f"  ✓ Approved: {approved}\n"
                f"  ✗ Rejected: {rejected}\n"
                f"  ◷ Deferred: {deferred}\n\n"
                f"Runtime: <code>{config.CLAUDE_RUNTIME}</code>\n"
                f"Parser:  <code>{config.DOCUMENT_PARSER}</code>\n"
                f"Role:    <code>{self.role_name}</code>"
            )
            self.send_message(chat_id, status_text)

        except Exception as e:
            self.send_message(chat_id, f"⚠️ Could not load status: {e}")

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    def handle_message(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat_id = message["chat"]["id"]
        user_id = message["from"]["id"]
        first_name = message["from"].get("first_name", "")

        # Stash first_name on runtime for extensions to access
        if not hasattr(self.runtime, "_user_meta"):
            self.runtime._user_meta = {}
        self.runtime._user_meta[user_id] = {"first_name": first_name}

        # Allowlist check
        allowed = config.allowed_users()
        if allowed and user_id not in allowed:
            self.send_message(chat_id, "⛔ You are not authorized to use this bot.")
            return

        self.offset = update["update_id"]

        # Route: photo upload
        if "photo" in message:
            self.handle_document(chat_id, user_id, message)
            return

        # Route: document upload (PDF, image file)
        if "document" in message:
            self.handle_document(chat_id, user_id, message)
            return

        text = message.get("text", "")

        if not text:
            self.send_message(
                chat_id,
                "Please send a text message, photo, or PDF document.",
            )
            return

        if text.startswith("/"):
            self.handle_command(chat_id, user_id, text)
            return

        print(f"Message from {user_id}: {text[:60]}...")
        self.send_typing(chat_id)

        response = self.runtime.call(text, user_id)
        self._send_chunked(chat_id, response)

    def _send_chunked(self, chat_id: int, text: str, chunk_size: int = 4000) -> None:
        """Send a long message in chunks (Telegram has a 4096 char limit)."""
        if len(text) <= chunk_size:
            self.send_message(chat_id, text)
        else:
            for i in range(0, len(text), chunk_size):
                self.send_message(chat_id, text[i: i + chunk_size])

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        print("Simply-Connect Telegram Relay starting...")
        print(f"  Runtime:        {config.CLAUDE_RUNTIME}")
        print(f"  Document parser:{config.DOCUMENT_PARSER}")
        print(f"  Role:           {self.role_name}")
        print(f"  Allowed users:  {config.allowed_users() or 'All users'}")
        print()

        try:
            response = requests.get(f"{self.api_url}/getMe", timeout=10)
            response.raise_for_status()
            bot_info = response.json()
            if bot_info.get("ok"):
                username = bot_info["result"]["username"]
                print(f"✓ Telegram: @{username}")
        except Exception as e:
            print(f"✗ Telegram connection failed: {e}")
            sys.exit(1)

        print("\nWaiting for messages...")
        print("-" * 40)

        while True:
            updates = self.get_updates(timeout=30)
            for update in updates:
                try:
                    self.handle_message(update)
                except Exception as e:
                    print(f"Error handling update: {e}")
            time.sleep(0.5)


import logging
log = logging.getLogger(__name__)


def main() -> None:
    import argparse as _argparse

    parser = _argparse.ArgumentParser(description="Simply-Connect Telegram Relay")
    parser.add_argument(
        "--role",
        type=str,
        default=None,
        help="Role to run as (must match a key in profile.json 'roles'). "
             "Determines which bot token env var and AGENT.md to use.",
    )
    args = parser.parse_args()

    # Resolve role and bot token
    role_name = args.role or "operator"
    if args.role:
        from .context_manager import ContextManager
        cm = ContextManager()
        if args.role not in cm.roles:
            print(f"Warning: role '{args.role}' not in profile.json — using default token")
        bot_token_env = cm.bot_token_env_for_role(args.role)
        bot_token = os.getenv(bot_token_env, "") or config.TELEGRAM_BOT_TOKEN
    else:
        bot_token = config.TELEGRAM_BOT_TOKEN

    if not bot_token:
        print("ERROR: No bot token found. Set SC_TELEGRAM_BOT_TOKEN or the role-specific env var.")
        sys.exit(1)

    relay = TelegramRelay(bot_token, role_name=role_name)
    relay.run()


if __name__ == "__main__":
    main()
