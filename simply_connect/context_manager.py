"""
Committed-Context Agent Framework — Context Manager

Manages the three-layer context architecture:
  Layer 1 — Committed context (context/*.md)  — authoritative, admin-controlled
  Layer 2 — Staging (staging/*.md)            — candidate updates, pending review
  Layer 3 — Session memory                    — ephemeral, handled by SessionManager

Context schema (which files exist, category mapping) is loaded from profile.json
at init time. Falls back to built-in defaults if no profile.json is present.

All path resolution uses _resolve_project_root() which walks up from cwd
looking for AGENT.md as a landmark file.
"""

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------

def _resolve_project_root(override: Path | None = None) -> Path:
    """
    Find the simply-connect project root by looking for AGENT.md.
    Walks up from cwd up to 6 levels. Falls back to cwd.
    """
    if override:
        return override
    candidate = Path.cwd()
    for _ in range(6):
        if (candidate / "AGENT.md").exists():
            return candidate
        candidate = candidate.parent
    return Path.cwd()


# ---------------------------------------------------------------------------
# Lightweight YAML frontmatter parser
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """
    Parse YAML frontmatter from a Markdown string.
    Returns (metadata_dict, body_text).
    Handles the --- delimited header with simple key: value pairs.
    """
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    header = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")

    meta: dict[str, Any] = {}
    for line in header.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            # Normalise null
            if value.lower() in ("null", "~", ""):
                meta[key] = None
            else:
                meta[key] = value
    return meta, body


def _render_frontmatter(meta: dict[str, Any], body: str) -> str:
    """Render metadata dict + body back to frontmatter Markdown."""
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {value if value is not None else 'null'}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

_DEFAULT_CONTEXT_FILES = ["business", "parties", "preferences", "contracts"]
_DEFAULT_CATEGORY_MAP = {
    "business": "business.md",
    "parties": "parties.md",
    "preferences": "preferences.md",
    "contracts": "contracts.md",
    "general": "business.md",
}
_DEFAULT_INTAKE_SOURCES = {
    "business-info.md": {"category": "business",    "description": "Business context"},
    "personal-info.md": {"category": "preferences", "description": "Working preferences"},
    "strategy.md":      {"category": "contracts",   "description": "Strategy"},
    "current-data.md":  {"category": "business",    "description": "Current data"},
}
_DEFAULT_STARTER_PROMPTS = {
    "operator": [
        "Show me the current context and the best next step.",
        "What should I work on next?",
    ],
}


def _iter_property_titles(markdown: str) -> list[str]:
    titles: list[str] = []
    for block in re.split(r"(?=^##\s+)", markdown or "", flags=re.MULTILINE):
        block = block.strip()
        if not block.startswith("## "):
            continue
        first = block.splitlines()[0].replace("##", "", 1).strip()
        if first:
            titles.append(first)
    return titles


def _normalize_property_ref(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _parse_property_removal_request(content: str) -> dict[str, str | None]:
    heading = re.search(r"^##\s+Property Removal Request$", content or "", re.MULTILINE)
    target = re.search(r"^- Property:\s*`?(.+?)`?$", content or "", re.MULTILINE)
    full_address = re.search(r"^- Full service address:\s*`?(.+?)`?$", content or "", re.MULTILINE)
    return {
        "is_removal": "yes" if heading else None,
        "property_ref": target.group(1).strip() if target else None,
        "full_address": full_address.group(1).strip() if full_address else None,
    }


def _property_matches_removal_target(committed_title: str, removal_target: str) -> bool:
    committed = committed_title.strip().lower()
    target = removal_target.strip().lower()
    return committed == target or committed.startswith(target + ",") or target in committed


def _looks_like_debit_note(text: str) -> bool:
    haystack = text or ""
    lowered = haystack.lower()
    if "debit note" in lowered:
        return True
    if re.search(r"\bdn-\d{4}-\d+\b", haystack, re.IGNORECASE):
        return True
    return False


def _load_profile(root: Path) -> dict:
    """Load profile.json from project root. Returns built-in defaults if absent or invalid."""
    path = root / "profile.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data
        except Exception:
            pass
    return {
        "name": "Default",
        "description": "Committed-context agent (default profile)",
        "context_files": _DEFAULT_CONTEXT_FILES,
        "category_map": _DEFAULT_CATEGORY_MAP,
        "intake_sources": _DEFAULT_INTAKE_SOURCES,
        "extensions": [],
        "starter_prompts": _DEFAULT_STARTER_PROMPTS,
    }


# ---------------------------------------------------------------------------
# ContextManager
# ---------------------------------------------------------------------------

class ContextManager:
    """
    Manages committed context and staging layer.

    Context schema (files, categories) is driven by profile.json.
    Falls back to built-in defaults when no profile.json is present,
    preserving full backward compatibility.

    Usage:
        cm = ContextManager()                  # auto-detects project root
        cm = ContextManager(root=Path("/...")) # explicit root
    """

    def __init__(self, root: Path | None = None):
        self._root = _resolve_project_root(root)
        self._profile = _load_profile(self._root)
        self.CONTEXT_FILES: list[str] = self._profile.get("context_files", _DEFAULT_CONTEXT_FILES)
        self.CATEGORY_MAP: dict[str, str] = self._profile.get("category_map", _DEFAULT_CATEGORY_MAP)
        self._context_dir = self._root / "context"
        self._staging_dir = self._root / "staging"
        self._staging_dir.mkdir(parents=True, exist_ok=True)

    @property
    def profile_name(self) -> str:
        """Display name from profile.json, e.g. 'Legal Counsel' or 'Landlord — Debit Notes'."""
        return self._profile.get("name", "Default")

    @property
    def intake_sources(self) -> dict:
        """Intake source map from profile.json: filename → {category, description}."""
        return self._profile.get("intake_sources", _DEFAULT_INTAKE_SOURCES)

    @property
    def active_extensions(self) -> list[str]:
        """List of active extension names declared in profile.json."""
        return self._profile.get("extensions", [])

    @property
    def roles(self) -> dict:
        """Role definitions from profile.json. Empty dict if no roles declared."""
        return self._profile.get("roles", {})

    @property
    def starter_prompts(self) -> dict[str, list[str]]:
        """Role-keyed starter prompts from profile.json."""
        return self._profile.get("starter_prompts", _DEFAULT_STARTER_PROMPTS)

    def starter_prompts_for_role(self, role_name: str) -> list[str]:
        """Return starter prompts for a role with a generic fallback."""
        prompts = self.starter_prompts.get(role_name)
        if prompts:
            return prompts
        return self.starter_prompts.get("operator", _DEFAULT_STARTER_PROMPTS["operator"])

    def load_context_for_role(self, role_name: str) -> dict[str, Any]:
        """
        Load context filtered to what the given role is allowed to see.

        If the role declares a context_filter, only those context file stems
        are included in committed context. Staging is always fully visible.
        Falls back to load_all_context() if no role config or no filter.

        Args:
            role_name: Role key from profile.json "roles" dict (e.g. "lawyer").

        Returns:
            Same structure as load_all_context(): {"committed": {...}, "staging": [...]}
        """
        role_config = self.roles.get(role_name, {})
        context_filter = role_config.get("context_filter")

        committed = self.load_committed()
        if context_filter:
            committed = {k: v for k, v in committed.items() if k in context_filter}

        return {
            "committed": committed,
            "staging": self.list_staging(status="unconfirmed"),
        }

    def agent_md_path_for_role(self, role_name: str) -> "Path | None":
        """
        Return the Path to the AGENT.md for the given role, or None to use root AGENT.md.

        The path in profile.json is relative to the project root.
        Returns None if no role config or no agent_md declared.
        """
        role_config = self.roles.get(role_name, {})
        agent_md_rel = role_config.get("agent_md")
        if not agent_md_rel:
            return None
        return self._root / agent_md_rel

    def bot_token_env_for_role(self, role_name: str) -> str:
        """
        Return the env var name for this role's Telegram bot token.
        Falls back to 'SC_TELEGRAM_BOT_TOKEN' if not declared.
        """
        role_config = self.roles.get(role_name, {})
        return role_config.get("telegram_bot_env", "SC_TELEGRAM_BOT_TOKEN")

    # ------------------------------------------------------------------
    # Committed context
    # ------------------------------------------------------------------

    def load_committed(self) -> dict[str, str]:
        """
        Load all committed context files.
        Returns dict keyed by stem name (e.g. "business", "parties").
        """
        result: dict[str, str] = {}
        for stem in self.CONTEXT_FILES:
            path = self._context_dir / f"{stem}.md"
            if path.exists():
                result[stem] = path.read_text(encoding="utf-8")
            else:
                result[stem] = ""
        return result

    def build_working_set_snapshot(self, role_name: str | None = None) -> dict[str, Any]:
        """Return a lightweight working-set overlay for model fallback."""
        if role_name and self.roles and role_name in self.roles:
            context = self.load_context_for_role(role_name)
        else:
            context = self.load_all_context()

        committed = context.get("committed", {})
        staging = context.get("staging", [])

        committed_properties = _iter_property_titles(committed.get("properties", ""))
        pending_property_additions: list[dict[str, str]] = []
        pending_property_removals: list[dict[str, str]] = []

        for entry in staging:
            if entry.get("category") != "properties":
                continue
            content = str(entry.get("content") or "")
            parsed_removal = _parse_property_removal_request(content)
            if parsed_removal.get("is_removal") == "yes" and parsed_removal.get("property_ref"):
                pending_property_removals.append(
                    {
                        "entry_id": str(entry.get("id") or ""),
                        "property_ref": str(parsed_removal.get("property_ref") or ""),
                        "full_address": str(parsed_removal.get("full_address") or ""),
                    }
                )
                continue
            title_match = re.search(r"^##\s+(.+)$", content, re.MULTILINE)
            pending_property_additions.append(
                {
                    "entry_id": str(entry.get("id") or ""),
                    "title": title_match.group(1).strip() if title_match else str(entry.get("summary") or ""),
                    "summary": str(entry.get("summary") or ""),
                }
            )

        active_properties = [
            title
            for title in committed_properties
            if not any(_property_matches_removal_target(title, item["property_ref"]) for item in pending_property_removals)
        ]

        return {
            "role": role_name or "operator",
            "committed_properties": committed_properties,
            "active_properties": active_properties,
            "pending_property_additions": pending_property_additions,
            "pending_property_removals": pending_property_removals,
        }

    def update_committed(self, category: str, content: str) -> bool:
        """
        Append a reviewed content block to the appropriate committed context file.
        Admin-only operation — called by promote_to_committed().
        Returns True on success.
        """
        _fallback = self._profile.get("context_files", ["business"])[0] + ".md"
        filename = self.CATEGORY_MAP.get(category, _fallback)
        path = self._context_dir / filename
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        block = f"\n\n<!-- Committed {timestamp} -->\n{content.strip()}\n"

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(block)
            return True
        except Exception as e:
            log.error(f"update_committed failed — category={category!r} path={path}: {e}")
            return False

    # ------------------------------------------------------------------
    # Staging layer
    # ------------------------------------------------------------------

    def create_staging_entry(
        self,
        summary: str,
        content: str,
        category: str = "general",
        source: str = "operator",
    ) -> str:
        """
        Create a new staging entry file.
        Returns the entry id (UUID).
        """
        if "debit_notes" in self.CATEGORY_MAP and _looks_like_debit_note(f"{summary}\n{content}"):
            category = "debit_notes"

        entry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y-%m-%dT%H%M%S")
        # Build a slug from the summary
        slug = re.sub(r"[^a-z0-9]+", "-", summary.lower())[:40].strip("-")
        filename = f"{timestamp_str}-{slug}.md"

        meta = {
            "id": entry_id,
            "captured": now.isoformat(),
            "source": source,
            "status": "unconfirmed",
            "category": category,
            "summary": summary,
            "reviewed_by": "pending",
            "reviewed_at": None,
        }

        file_content = _render_frontmatter(meta, content.strip())
        path = self._staging_dir / filename

        # Atomic write
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._staging_dir, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(file_content)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        return entry_id

    def list_staging(self, status: str | None = None) -> list[dict[str, Any]]:
        """
        List staging entries, optionally filtered by status.
        Returns list of dicts with metadata + content + filepath.
        """
        entries = []
        for path in sorted(self._staging_dir.glob("*.md")):
            if path.name == "README.md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(text)
                if not meta.get("id"):
                    continue
                entry = {**meta, "content": body.strip(), "filepath": str(path)}
                if status is None or entry.get("status") == status:
                    entries.append(entry)
            except Exception:
                pass
        return entries

    def get_staging_entry(self, entry_id: str) -> dict[str, Any] | None:
        """Find and return a single staging entry by id."""
        for entry in self.list_staging():
            if entry.get("id") == entry_id:
                return entry
        return None

    def update_staging_status(
        self,
        entry_id: str,
        status: str,
        reviewed_by: str = "human",
    ) -> bool:
        """
        Update the status and reviewer fields in a staging entry's frontmatter.
        Returns True on success.
        """
        entry = self.get_staging_entry(entry_id)
        if not entry:
            return False

        path = Path(entry["filepath"])
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)

        meta["status"] = status
        meta["reviewed_by"] = reviewed_by
        meta["reviewed_at"] = datetime.now(timezone.utc).isoformat()

        new_text = _render_frontmatter(meta, body)

        tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(new_text)
            os.replace(tmp_path, path)
            return True
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return False

    def promote_to_committed(self, entry_id: str, reviewed_by: str = "human") -> bool:
        """
        Approve a staging entry: append its content to committed context,
        mark the entry as approved.
        Returns True on success.
        """
        entry = self.get_staging_entry(entry_id)
        if not entry:
            return False

        category = entry.get("category", "general")
        content = entry.get("content", "")

        if not content.strip():
            return False

        success = self.update_committed(category, content)
        if success:
            self.update_staging_status(entry_id, "approved", reviewed_by)
        return success

    # ------------------------------------------------------------------
    # Trust-aware combined load
    # ------------------------------------------------------------------

    def load_all_context(self) -> dict[str, Any]:
        """
        Load all context for use by brain at session start.
        Returns:
            {
                "committed": {stem: content, ...},
                "staging": [list of unconfirmed entries],
            }
        """
        return {
            "committed": self.load_committed(),
            "staging": self.list_staging(status="unconfirmed"),
        }

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def status_summary(self) -> dict[str, Any]:
        """Return a summary dict for display in CLI status commands."""
        committed_info = []
        for stem in self.CONTEXT_FILES:
            path = self._context_dir / f"{stem}.md"
            if path.exists():
                text = path.read_text(encoding="utf-8")
                words = len(text.split())
                mtime = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%d")
            else:
                words = 0
                mtime = "(not found)"
            committed_info.append({
                "file": f"{stem}.md",
                "words": words,
                "last_modified": mtime,
            })

        staging_counts: dict[str, int] = {
            "unconfirmed": 0,
            "approved": 0,
            "rejected": 0,
            "deferred": 0,
        }
        for entry in self.list_staging():
            s = entry.get("status", "unconfirmed")
            staging_counts[s] = staging_counts.get(s, 0) + 1

        return {
            "committed": committed_info,
            "staging": staging_counts,
        }
