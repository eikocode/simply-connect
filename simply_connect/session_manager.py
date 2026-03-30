"""
Super-Contract — Session Manager

Persists per-session conversation history to disk.
Survives process restarts — conversations never lost.

Storage: data/sessions/{session_id}.json
Session IDs are strings (UUID or user-provided names), not ints.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_HISTORY = 20  # Max turns to keep per session (10 full exchanges)


def _resolve_data_dir() -> Path:
    """
    Locate the data/sessions directory.
    Walks up from cwd looking for AGENT.md as a project root landmark.
    Falls back to cwd/data/sessions if not found.
    """
    cwd = Path.cwd()
    candidate = cwd
    for _ in range(5):
        if (candidate / "AGENT.md").exists():
            d = candidate / "data" / "sessions"
            d.mkdir(parents=True, exist_ok=True)
            return d
        candidate = candidate.parent
    # Fallback
    d = cwd / "data" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


class SessionManager:
    """Thread-safe per-session conversation storage."""

    _lock = threading.Lock()

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or _resolve_data_dir()

    def _path(self, session_id: str) -> Path:
        # Sanitize session_id for use as a filename
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
        return self._data_dir / f"{safe}.json"

    def load(self, session_id: str) -> dict[str, Any]:
        """Load session from disk. Returns empty dict if not found."""
        p = self._path(session_id)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                log.warning(f"Corrupt session {session_id!r}, starting fresh")
                return {}
        return {}

    def save(self, session_id: str, session: dict[str, Any]) -> None:
        """Atomically save session to disk."""
        p = self._path(session_id)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._data_dir, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, p)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Return conversation history for this session."""
        return self.load(session_id).get("history", [])

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        """Append a message turn and save. Trims to MAX_HISTORY."""
        with self._lock:
            session = self.load(session_id)
            history = session.get("history", [])
            history.append({"role": role, "content": content})
            history = history[-MAX_HISTORY:]
            session["history"] = history
            # Ensure metadata fields are present
            if "started_at" not in session:
                session["started_at"] = datetime.now(timezone.utc).isoformat()
            session["last_active"] = datetime.now(timezone.utc).isoformat()
            self.save(session_id, session)

    def init_session(self, session_id: str, role: str) -> dict[str, Any]:
        """
        Initialise a new session or load existing one.
        Records role and started_at on first creation.
        """
        with self._lock:
            session = self.load(session_id)
            if not session:
                session = {
                    "session_id": session_id,
                    "role": role,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "last_active": datetime.now(timezone.utc).isoformat(),
                    "history": [],
                }
                self.save(session_id, session)
            return session

    def clear(self, session_id: str) -> None:
        """Delete all session data for this session."""
        p = self._path(session_id)
        if p.exists():
            p.unlink()
            log.info(f"Cleared session {session_id!r}")

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all session files with metadata."""
        sessions = []
        for p in self._data_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": data.get("session_id", p.stem),
                    "role": data.get("role", "unknown"),
                    "started_at": data.get("started_at"),
                    "last_active": data.get("last_active"),
                    "turns": len(data.get("history", [])),
                })
            except Exception:
                pass
        return sorted(sessions, key=lambda s: s.get("last_active") or "", reverse=True)
