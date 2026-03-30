"""
Super-Contract — Brain

Claude intelligence layer. Reads AGENT.md from disk on every call (live reload).
Produces trust-aware responses that flag unconfirmed staging context.
Detects capture intent and extracts staging candidates.

Three public functions:
    respond()               — operator/admin session responses (single-shot, JSON output)
    respond_with_tools()    — operator session with tool_use loop (for extensions)
    review_staging_entry()  — AI admin staging review
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv, find_dotenv

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env + Claude client
# ---------------------------------------------------------------------------

try:
    load_dotenv(find_dotenv(usecwd=True), override=False)
except (FileNotFoundError, OSError):
    load_dotenv(find_dotenv(usecwd=False), override=False)

_claude = None


def _api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "")


def _get_claude():
    """Return an Anthropic SDK client. Raises clearly if no API key is set."""
    global _claude
    if _claude is None:
        key = _api_key()
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set.\n"
                "  For operator sessions without an API key, use the CLI runtime:\n"
                "    SC_CLAUDE_RUNTIME=cli sc\n"
                "  Or add ANTHROPIC_API_KEY to your .env file."
            )
        import anthropic
        _claude = anthropic.Anthropic(api_key=key)
    return _claude


def _call_text_subprocess(prompt: str) -> str:
    """Run a one-shot text prompt via `claude --print` (Claude Code OAuth, no API key needed)."""
    import subprocess
    result = subprocess.run(
        ["claude", "--print", "--output-format", "text", "--", prompt],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr[:200] or "claude subprocess failed")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Project root + AGENT.md resolution
# ---------------------------------------------------------------------------

def _resolve_project_root() -> Path:
    """Walk up from cwd looking for AGENT.md as a landmark."""
    candidate = Path.cwd()
    for _ in range(6):
        if (candidate / "AGENT.md").exists():
            return candidate
        candidate = candidate.parent
    return Path.cwd()


def _load_agent_md(path: Path | None = None) -> str:
    """Read AGENT.md from disk. Returns empty string if not found.

    Args:
        path: Explicit path to an AGENT.md file (e.g. for a role-specific file).
              Falls back to root AGENT.md when None.
    """
    if path is None:
        root = _resolve_project_root()
        path = root / "AGENT.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    log.warning(f"AGENT.md not found at {path} — running without agent instructions")
    return ""


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    """Robustly extract a JSON object from a Claude response."""
    text = text.strip()
    # Try ```json ... ``` block
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # Try ``` ... ``` block
    m = re.search(r"```\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # Try outermost { ... }
    m = re.search(r"(\{.*\})", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    raise ValueError(f"No JSON object found in response: {text[:200]}")


def _repair_json_via_model(raw_text: str) -> dict[str, Any]:
    """Ask the model/runtime to re-emit malformed JSON as valid JSON only."""
    repair_prompt = f"""Rewrite the following malformed response as a single valid JSON object only.

Requirements:
- Output only JSON.
- Preserve the original meaning.
- Use this exact top-level schema:
  {{"reply": string, "capture": object|null, "confidence": number, "used_unconfirmed": boolean, "raw_response": string}}
- Escape all newlines and quotes correctly.
- If capture is present, it must have:
  {{"summary": string, "content": string, "category": string}}

Malformed response:
{raw_text}
"""
    if _api_key():
        claude = _get_claude()
        response = claude.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": repair_prompt}],
        )
        repaired = response.content[0].text.strip()
    else:
        repaired = _call_text_subprocess(repair_prompt)
    return _extract_json(repaired)


def _strip_unconfirmed_note(reply: str) -> str:
    note = "*(note: drawing on unconfirmed context — pending admin review)*"
    cleaned = reply.replace(f"\n\n{note}", "").replace(f"\n{note}", "").replace(note, "")
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Context formatting helpers
# ---------------------------------------------------------------------------

def _format_committed(committed: dict[str, str]) -> str:
    """Format committed context as labelled sections for the system prompt."""
    parts = []
    for stem, content in committed.items():
        if content and content.strip():
            parts.append(f"## [COMMITTED] {stem.capitalize()}\n{content.strip()}")
        else:
            parts.append(f"## [COMMITTED] {stem.capitalize()}\n(empty — not yet populated)")
    return "\n\n".join(parts)


def _format_staging(staging: list[dict[str, Any]]) -> str:
    """Format unconfirmed staging entries as flagged sections."""
    if not staging:
        return "(no unconfirmed staging entries)"
    parts = []
    for entry in staging:
        summary = entry.get("summary", "no summary")
        content = entry.get("content", "")
        captured = entry.get("captured", "unknown")
        parts.append(
            f"## [UNCONFIRMED — pending admin review]\n"
            f"Summary: {summary}\nCaptured: {captured}\n\n{content.strip()}"
        )
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# respond() — main operator/admin brain
# ---------------------------------------------------------------------------

def respond(
    message: str,
    context: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
    role: str = "operator",
    agent_md_path: Path | None = None,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """
    Generate a trust-aware response to an operator or admin message.

    Args:
        message:  The user's latest message.
        context:  Output of context_manager.load_all_context().
                  Keys: "committed" (dict), "staging" (list).
        history:  Conversation history list of {"role": ..., "content": ...}.
        role:     "operator" or "admin".

    Returns:
        {
            "reply": str,
            "capture": None | {"summary": str, "content": str, "category": str},
            "confidence": float,
            "used_unconfirmed": bool,
            "raw_response": str,
        }
    """
    agent_instructions = _load_agent_md(agent_md_path)

    committed_block = _format_committed(context.get("committed", {}))
    staging_entries = context.get("staging", [])
    staging_block = _format_staging(staging_entries)
    has_staging = bool(staging_entries)

    _default_categories = ["business", "parties", "preferences", "contracts", "general"]
    category_hint = " | ".join(categories) if categories else " | ".join(_default_categories)

    system_prompt = f"""{agent_instructions}

---

# Session Context

## Role
Current session role: {role}

## Committed Context (authoritative — full trust)

{committed_block}

## Staging Context (unconfirmed — pending admin review)

{staging_block}

---

# Response Instructions

You MUST respond with ONLY a valid JSON object in this exact format — no other text:

{{
  "reply": "your response to the operator (plain text, \\n for line breaks)",
  "capture": null,
  "confidence": 0.95,
  "used_unconfirmed": false,
  "raw_response": "brief internal note about your reasoning"
}}

## Capture Detection
Populate the capture field when the operator's message signals intent to persist something.
This includes:

**Explicit capture phrases:** "remember this", "note that", "learn this", "keep this in mind",
"going forward", "for future reference", "add this to context", "don't forget that"

**Implicit confirmation of a prior draft:** if the operator says "confirm", "yes", "ok",
"issue it", "send it", or similar short affirmatives AND the previous assistant turn contained
a draft document (debit note, invoice, contract, etc.) — capture that document to staging.
Use the category that matches the document type (e.g. "debit_notes" for a debit note draft).

{{
  "capture": {{
    "summary": "one-line summary of what to remember",
    "content": "the full content to store",
    "category": "{category_hint}"
  }}
}}

And append to the reply: "\\n\\nStaged — run sc-admin review to commit."

**Never claim to write directly to context files.** All updates go through staging.

## Unconfirmed Context
Set "used_unconfirmed": true if any staging entry above influenced your response.
When used_unconfirmed is true, append to your reply:
"\\n\\n*(note: drawing on unconfirmed context — pending admin review)*"

## General Rules
- Never refuse due to missing context. Work with what is available.
- If context is empty, help the operator and suggest they run sc-admin intake to populate it.
- Keep responses focused on contract work.
- Be concise and practical — operators are working, not reading essays.
"""

    messages = []
    if history:
        for turn in history[-10:]:
            role_val = turn.get("role", "user")
            content_val = turn.get("content", "")
            if content_val:
                messages.append({"role": role_val, "content": content_val})

    messages.append({"role": "user", "content": message})

    defaults: dict[str, Any] = {
        "reply": "",
        "capture": None,
        "confidence": 0.5,
        "used_unconfirmed": False,
        "raw_response": "",
    }

    try:
        if _api_key():
            claude = _get_claude()
            response = claude.messages.create(
                model="claude-opus-4-5",
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
            )
            raw = response.content[0].text.strip()
        else:
            # No API key — fall back to claude --print subprocess (Claude Code OAuth)
            history_text = ""
            for msg in messages[:-1]:
                label = "User" if msg["role"] == "user" else "Assistant"
                history_text += f"\n{label}: {msg['content']}\n"
            flat_prompt = f"{system_prompt}\n\n---\n{history_text}\nUser: {message}"
            raw = _call_text_subprocess(flat_prompt)
        try:
            result = _extract_json(raw)
        except Exception:
            log.warning("Brain.respond received malformed JSON; attempting repair pass")
            result = _repair_json_via_model(raw)

        for k, v in defaults.items():
            result.setdefault(k, v)

        if not has_staging and result.get("used_unconfirmed"):
            result["used_unconfirmed"] = False
            result["reply"] = _strip_unconfirmed_note(result.get("reply", ""))

        # If staging context exists but model forgot to flag it, do a heuristic check
        if has_staging and not result.get("used_unconfirmed"):
            # Check if any staging summary words appear in the reply
            for entry in staging_entries:
                summary_words = set(entry.get("summary", "").lower().split())
                reply_words = set(result.get("reply", "").lower().split())
                if summary_words & reply_words:
                    result["used_unconfirmed"] = True
                    break

        log.info(
            f"Brain respond: confidence={result['confidence']} "
            f"capture={'yes' if result['capture'] else 'no'} "
            f"used_unconfirmed={result['used_unconfirmed']}"
        )
        return result

    except Exception as e:
        log.exception("Brain.respond failed")
        return {
            **defaults,
            "reply": "I ran into an issue processing that. Please try again.",
            "raw_response": str(e),
        }


# ---------------------------------------------------------------------------
# respond_with_tools() — tool_use loop for extension-enabled profiles
# ---------------------------------------------------------------------------

# capture_to_staging tool definition (inline, for respond_with_tools)
def _make_capture_tool(categories: list[str] | None = None) -> dict:
    _default_categories = ["business", "parties", "preferences", "contracts", "general"]
    hint = " | ".join(categories) if categories else " | ".join(_default_categories)
    return {
        "name": "capture_to_staging",
        "description": (
            "Create a new staging entry — a candidate context update to be reviewed by admin "
            "before being committed to authoritative context. "
            "Use this when the operator explicitly asks to remember, note, or save something. "
            "Do NOT use for casual conversation — only capture deliberate instructions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One-line summary of what is being captured (max 80 chars).",
                },
                "content": {
                    "type": "string",
                    "description": "The full content to store in staging.",
                },
                "category": {
                    "type": "string",
                    "description": (
                        f"Context category matching the active profile ({hint})"
                    ),
                },
            },
            "required": ["summary", "content", "category"],
        },
    }


def respond_with_tools(
    message: str,
    context: dict[str, Any],
    tools: list[dict],
    dispatch_fn,
    history: list[dict[str, Any]] | None = None,
    role: str = "operator",
    agent_md_path: Path | None = None,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """
    Generate a response using the tool_use loop — for profiles with active extensions.

    Unlike respond(), this function:
    - Does NOT require JSON output format — Claude responds naturally
    - Runs a tool_use loop: calls dispatch_fn for each tool_use block until end_turn
    - Includes capture_to_staging as a native tool (dispatch_fn handles it)

    Args:
        message:     The user's latest message.
        context:     Output of context_manager.load_all_context().
        tools:       Extension tool definitions (from _loader.get_all_tools()).
        dispatch_fn: callable(name: str, args: dict) -> str
                     Handles both extension tools and capture_to_staging.
        history:     Conversation history list of {"role": ..., "content": ...}.
        role:        "operator" or "admin".

    Returns:
        {
            "reply": str,
            "capture": None,
            "confidence": 1.0,
            "used_unconfirmed": False,
            "raw_response": "",
        }
    """
    if not _api_key():
        # No API key — fall back to respond() which handles subprocess routing
        return respond(message, context, history=history, role=role, agent_md_path=agent_md_path)

    claude = _get_claude()
    agent_instructions = _load_agent_md(agent_md_path)

    committed_block = _format_committed(context.get("committed", {}))
    staging_entries = context.get("staging", [])
    staging_block = _format_staging(staging_entries)

    system_prompt = f"""{agent_instructions}

---

# Session Context

## Role
Current session role: {role}

## Committed Context (authoritative — full trust)

{committed_block}

## Staging Context (unconfirmed — pending admin review)

{staging_block}

---

# Response Instructions

Respond naturally in plain text. You have access to tools — use them when the operator's
question requires live data or computation. Present tool results clearly in your reply.

When the operator signals capture intent ("remember this", "note that", "learn this",
"keep this in mind", "going forward", "for future reference", "add this to context",
"don't forget that") — call the capture_to_staging tool with summary, content, and category.
Append to your reply: "Captured — pending admin review."

When you draw on staging (unconfirmed) context in your reply, append:
"*(note: drawing on unconfirmed context — pending admin review)*"

Never refuse due to missing context. Work with what is available.
"""

    # Build initial messages
    messages: list[dict[str, Any]] = []
    if history:
        for turn in history[-10:]:
            role_val = turn.get("role", "user")
            content_val = turn.get("content", "")
            if content_val:
                messages.append({"role": role_val, "content": content_val})
    messages.append({"role": "user", "content": message})

    # Merge capture_to_staging into the tools list
    all_tools = [_make_capture_tool(categories)] + list(tools)

    reply_text = ""

    try:
        while True:
            response = claude.messages.create(
                model="claude-opus-4-5",
                max_tokens=2048,
                system=system_prompt,
                tools=all_tools,
                messages=messages,
            )

            stop_reason = response.stop_reason

            if stop_reason == "tool_use":
                # Append the assistant's tool_use message to history
                messages.append({"role": "assistant", "content": response.content})

                # Dispatch each tool_use block
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    tool_name = block.name
                    tool_args = block.input or {}
                    tool_use_id = block.id

                    log.info(
                        f"Tool call: {tool_name} args={str(tool_args)[:120]}"
                    )

                    try:
                        tool_result = dispatch_fn(tool_name, tool_args)
                    except Exception as exc:
                        import json as _json
                        tool_result = _json.dumps({"error": str(exc)})

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": tool_result,
                    })

                messages.append({"role": "user", "content": tool_results})
                # Continue the loop

            elif stop_reason == "end_turn":
                # Extract text reply
                for block in response.content:
                    if hasattr(block, "text"):
                        reply_text += block.text
                break

            else:
                # Unexpected stop reason — break to avoid infinite loop
                log.warning(f"respond_with_tools: unexpected stop_reason={stop_reason}")
                break

    except Exception:
        log.exception("Brain.respond_with_tools failed")
        reply_text = "I ran into an issue processing that. Please try again."

    log.info(f"Brain respond_with_tools: reply_len={len(reply_text)}")

    return {
        "reply": reply_text.strip(),
        "capture": None,
        "confidence": 1.0,
        "used_unconfirmed": False,
        "raw_response": "",
    }


# ---------------------------------------------------------------------------
# review_staging_entry() — AI admin review
# ---------------------------------------------------------------------------

def review_staging_entry(
    entry: dict[str, Any],
    committed_context: dict[str, str],
) -> dict[str, Any]:
    """
    AI admin review of a staging entry against committed context.
    Uses claude-haiku-4-5 for speed.

    Returns:
        {
            "recommendation": "approve" | "reject" | "defer",
            "reason": str,
            "conflicts": list[str],
            "suggested_category": str,
            "confidence": float,
        }
    """
    claude = _get_claude() if _api_key() else None

    committed_text = "\n\n".join(
        f"## {stem.capitalize()}\n{content}"
        for stem, content in committed_context.items()
        if content and content.strip()
    ) or "(no committed context yet)"

    entry_summary = entry.get("summary", "no summary")
    entry_content = entry.get("content", "")
    entry_category = entry.get("category", "general")
    entry_source = entry.get("source", "operator")

    prompt = f"""You are reviewing a candidate context update for a contract assistant system.

## Staged Entry
Source: {entry_source}
Category: {entry_category}
Summary: {entry_summary}

Content:
{entry_content}

## Current Committed Context
{committed_text}

## Your Task
Review this staged entry and decide whether it should be approved, rejected, or deferred.

Respond with ONLY a valid JSON object:
{{
  "recommendation": "approve" | "reject" | "defer",
  "reason": "one sentence explaining the decision",
  "conflicts": ["list any specific conflicts with committed context, or empty array"],
  "suggested_category": "{entry_category}",
  "confidence": 0.0
}}

## Decision Rules
- approve: Content is factual, useful, non-conflicting, and clearly adds value. confidence >= 0.85.
- reject: Content is noise, incorrect, harmful, or clearly redundant.
- defer: Content may be valid but conflicts with existing context, is ambiguous, or confidence < 0.85.

Be strict about conflicts. Even partial contradictions should result in defer, not approve.
"""

    defaults: dict[str, Any] = {
        "recommendation": "defer",
        "reason": "Could not complete review",
        "conflicts": [],
        "suggested_category": entry_category,
        "confidence": 0.0,
    }

    try:
        if _api_key():
            response = claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        else:
            raw = _call_text_subprocess(prompt)
        result = _extract_json(raw)
        for k, v in defaults.items():
            result.setdefault(k, v)
        return result

    except Exception as e:
        log.exception("Brain.review_staging_entry failed")
        return {**defaults, "reason": f"Review failed: {e}"}
