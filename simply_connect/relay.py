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
import json
import logging
import os
import queue
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import config
from .runtimes import get_runtime

log = logging.getLogger(__name__)


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


@dataclass
class _DocJob:
    chat_id: int
    user_id: int
    file_bytes: bytes
    filename: str
    mime_type: str
    caption: str
    role_name: str
    source: str = "telegram"           # "telegram" | "web"
    job_path: "Path | None" = None     # sidecar JSON path for web uploads


class DocumentWorker:
    """Background daemon thread — processes document jobs one at a time.

    Sources:
      - Telegram uploads: enqueued directly via enqueue() from handle_document()
      - Web uploads: discovered by _dir_watcher() scanning SC_WEB_UPLOAD_DIR
    """

    TYPING_INTERVAL = 5    # seconds between typing indicators
    DIR_POLL_INTERVAL = 2  # seconds between directory scans

    def __init__(self, relay: "TelegramRelay") -> None:
        self._relay = relay
        self._queue: queue.Queue[_DocJob] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="doc-worker"
        )
        self._dir_thread = threading.Thread(
            target=self._dir_watcher, daemon=True, name="doc-dir-watcher"
        )
        self._seen_jobs: set[str] = set()  # sidecar basenames already enqueued this session

    def start(self) -> None:
        self._drain_upload_dir()   # crash-recovery: pick up any leftover jobs
        self._thread.start()
        self._dir_thread.start()

    def enqueue(self, job: _DocJob) -> None:
        self._queue.put(job)

    # ------------------------------------------------------------------
    # Directory-based queue support (web uploads)
    # ------------------------------------------------------------------

    def _upload_dir(self) -> Path:
        return config.web_upload_dir()

    def _drain_upload_dir(self) -> None:
        """On startup: enqueue any .json sidecars already sitting in the upload dir."""
        try:
            for sidecar in sorted(self._upload_dir().glob("*.json")):
                self._enqueue_from_sidecar(sidecar)
        except Exception as e:
            log.warning(f"DocumentWorker: cannot drain upload dir: {e}")

    def _dir_watcher(self) -> None:
        """Poll upload directory every DIR_POLL_INTERVAL seconds for new jobs."""
        while True:
            try:
                for sidecar in sorted(self._upload_dir().glob("*.json")):
                    self._enqueue_from_sidecar(sidecar)
            except Exception as e:
                log.warning(f"DocumentWorker dir-watcher error: {e}")
            time.sleep(self.DIR_POLL_INTERVAL)

    def _enqueue_from_sidecar(self, sidecar: Path) -> None:
        """Read a .json sidecar + matching binary file and enqueue a _DocJob."""
        key = sidecar.name
        if key in self._seen_jobs:
            return

        try:
            meta = json.loads(sidecar.read_text())
        except Exception as e:
            log.warning(f"DocumentWorker: bad sidecar {sidecar}: {e}")
            self._seen_jobs.add(key)  # skip permanently to avoid log spam
            return

        stem = sidecar.stem
        suffix = _MIME_TO_SUFFIX.get(meta.get("mime_type", ""), ".bin")
        bin_path = sidecar.parent / f"{stem}{suffix}"

        if not bin_path.exists():
            log.warning(f"DocumentWorker: missing binary for {sidecar}, skipping")
            self._seen_jobs.add(key)
            return

        try:
            file_bytes = bin_path.read_bytes()
        except Exception as e:
            log.warning(f"DocumentWorker: cannot read {bin_path}: {e}")
            return  # don't mark seen — will retry next poll

        self._seen_jobs.add(key)
        job = _DocJob(
            chat_id=meta.get("chat_id", 0),
            user_id=meta.get("user_id", 0),
            file_bytes=file_bytes,
            filename=meta.get("filename", bin_path.name),
            mime_type=meta.get("mime_type", "application/octet-stream"),
            caption=meta.get("caption", ""),
            role_name=meta.get("role_name", "operator"),
            source="web",
            job_path=sidecar,
        )
        log.info(f"DocumentWorker: enqueuing web upload {stem} ({job.filename})")
        self._queue.put(job)

    def _cleanup_job_files(self, job: _DocJob) -> None:
        """Delete sidecar JSON and binary file after successful processing."""
        if job.job_path is None:
            return
        sidecar = job.job_path
        stem = sidecar.stem
        suffix = _MIME_TO_SUFFIX.get(job.mime_type, ".bin")
        bin_path = sidecar.parent / f"{stem}{suffix}"
        for path in (sidecar, bin_path):
            try:
                if path.exists():
                    path.unlink()
                    log.debug(f"DocumentWorker: deleted {path}")
            except Exception as e:
                log.warning(f"DocumentWorker: could not delete {path}: {e}")

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._process(job)
            except Exception as e:
                log.exception(f"DocumentWorker: job failed for chat {job.chat_id}: {e}")
                if job.source == "telegram":
                    try:
                        self._relay.send_message(
                            job.chat_id,
                            f"⚠️ Failed to process document:\n<code>{e}</code>",
                        )
                    except Exception:
                        pass
                # Web jobs: log only, no Telegram chat to notify
            finally:
                self._queue.task_done()

    def _process(self, job: _DocJob) -> None:
        from .context_manager import ContextManager
        from .ext_loader import maybe_handle_document as _ext_doc
        from .ingestion import ingest_document

        # Typing keepalive only for Telegram jobs
        stop_typing = threading.Event()
        typing_thread = None
        if job.source == "telegram":
            typing_thread = threading.Thread(
                target=self._typing_loop,
                args=(job.chat_id, stop_typing),
                daemon=True,
                name="doc-typing",
            )
            typing_thread.start()

        tmp_path = None
        try:
            cm = ContextManager()

            # Extension hook (save-my-brain intelligence pipeline lives here)
            ext_reply = _ext_doc(
                job.file_bytes, job.filename, job.mime_type, job.caption, cm,
                role_name=job.role_name, user_id=job.user_id,
            )
            if ext_reply is not None:
                if job.source == "telegram":
                    self._relay._send_chunked(job.chat_id, ext_reply)
                else:
                    log.info(f"DocumentWorker: web upload processed OK: {job.filename}")
                self._cleanup_job_files(job)
                return

            # Default staging fallback (non-save-my-brain deployments)
            suffix = _MIME_TO_SUFFIX.get(job.mime_type, ".bin")
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(job.file_bytes)
                tmp_path = Path(f.name)

            committed = cm.load_committed()
            result = ingest_document(
                tmp_path, committed, cm._profile,
                parser=config.DOCUMENT_PARSER,
            )

            if not result.get("success"):
                if job.source == "telegram":
                    self._relay.send_message(
                        job.chat_id,
                        f"⚠️ Could not read document:\n<code>{result.get('error', 'unknown error')}</code>",
                    )
                else:
                    log.warning(f"DocumentWorker: web ingest failed: {result.get('error')}")
                self._cleanup_job_files(job)
                return

            extractions = result.get("extractions", [])
            if not extractions:
                if job.source == "telegram":
                    self._relay.send_message(
                        job.chat_id,
                        "📄 Document read — no structured content found to stage.",
                    )
                self._cleanup_job_files(job)
                return

            staged = 0
            for item in extractions:
                source_label = f"relay:{job.filename}"
                if job.caption:
                    source_label += f" ({job.caption[:40]})"
                cm.create_staging_entry(
                    summary=item.get("summary", job.filename),
                    content=item.get("content", ""),
                    category=item.get("category", "general"),
                    source=source_label,
                )
                staged += 1

            if job.source == "telegram":
                parser_label = result.get("parser", config.DOCUMENT_PARSER)
                reply = (
                    f"📄 <b>{staged} item{'s' if staged != 1 else ''} staged for review</b>\n\n"
                    f"Parser: <code>{parser_label}</code>"
                )
                if job.caption:
                    reply += f"\n\nCaption: <i>{job.caption}</i>"
                self._relay._send_chunked(job.chat_id, reply)
            else:
                log.info(f"DocumentWorker: web upload staged {staged} items from {job.filename}")

            self._cleanup_job_files(job)

        finally:
            stop_typing.set()
            if typing_thread is not None:
                typing_thread.join(timeout=2)
            if tmp_path and tmp_path.exists():
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def _typing_loop(self, chat_id: int, stop: threading.Event) -> None:
        """Send typing indicator every TYPING_INTERVAL seconds until processing done."""
        while not stop.wait(timeout=self.TYPING_INTERVAL):
            try:
                self._relay.send_typing(chat_id)
            except Exception:
                pass


class TelegramRelay:
    """Relay messages between Telegram and Claude via the configured runtime."""

    def __init__(self, token: str, role_name: str = "operator") -> None:
        self.token = token
        self.role_name = role_name
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.file_url = f"https://api.telegram.org/file/bot{token}"
        self.offset = 0
        self.runtime = get_runtime(config.CLAUDE_RUNTIME, role_name=role_name)
        self._doc_worker = DocumentWorker(self)

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
        """Validate, download, and enqueue a document for background processing."""
        caption = message.get("caption", "")

        if "photo" in message:
            file_id = message["photo"][-1]["file_id"]
            mime_type = "image/jpeg"
            filename = "photo"
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
            filename = doc.get("file_name", f"document{suffix}")
        else:
            return

        try:
            file_bytes = self.download_file(file_id)
        except Exception as e:
            self.send_message(chat_id, f"⚠️ Could not download file:\n<code>{e}</code>")
            return

        self._doc_worker.enqueue(_DocJob(
            chat_id=chat_id,
            user_id=user_id,
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
            caption=caption,
            role_name=self.role_name,
        ))
        self.send_message(chat_id, f"📄 Got it — analysing <b>{filename}</b>…")

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

        # Give extensions first crack at every message — including /commands.
        # This lets domain onboarding FSMs intercept /start before the relay's
        # generic handler runs.
        try:
            from .context_manager import ContextManager
            from .ext_loader import maybe_handle_message as _ext_msg
            _cm = ContextManager()
            ext_reply = _ext_msg(
                text, _cm,
                role_name=self.role_name,
                history=None,
                user_id=user_id,
                first_name=first_name,
            )
            if ext_reply:
                self._send_chunked(chat_id, ext_reply)
                return
        except Exception as _ext_err:
            log.warning(f"Extension pre-check failed: {_ext_err}", exc_info=True)

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

    def _drain_pending_updates(self) -> None:
        """Skip all updates queued before this startup to avoid replaying old messages.

        Telegram re-delivers unacknowledged updates on reconnect. Without draining,
        old messages (e.g. "1" from a previous session) get replayed and can corrupt
        stateful flows like onboarding. We call getUpdates with offset=-1 to find the
        latest update_id and set our offset to it — subsequent calls will only return
        genuinely new messages.
        """
        try:
            response = requests.get(
                f"{self.api_url}/getUpdates",
                params={"offset": -1, "timeout": 0},
                timeout=5,
            )
            data = response.json()
            if data.get("ok"):
                updates = data.get("result", [])
                if updates:
                    self.offset = updates[-1]["update_id"]
                    print(f"  Skipped pending updates (offset → {self.offset})")
        except Exception as e:
            print(f"  Warning: could not drain pending updates: {e}")

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

        self._doc_worker.start()
        print("  Document worker: started")
        self._drain_pending_updates()
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
