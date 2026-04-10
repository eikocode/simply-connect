"""Curator Agent — evaluates session content and promotes worthy items to staging.

The curator is a model-driven evaluator that runs over session history and decides
what to capture to staging. It reuses the same model-driven evaluation mechanism
that already exists in brain.py, but with explicit promotion criteria instead of
phrase-matching.

Promotion criteria (configurable per domain):
- Enduring knowledge vs. operational ephemera
- Cross-session recurrence (signal vs. noise)
- Contradiction detection against committed context
- Source trust weight (domain role authority)

Performance optimizations:
- Parallel session processing with ThreadPoolExecutor
- Deterministic pre-filter for fast-path evaluation
- Configurable concurrency limit

Usage:
    sc-admin curate                  # curate current session
    sc-admin curate --session <id>   # curate specific session
    sc-admin curate --all            # curate all sessions
    sc-admin curate --daemon         # run as background daemon
    sc-admin curate --daemon --interval 30  # daemon with 30-min interval
"""
from __future__ import annotations

import json
import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default promotion criteria
# ---------------------------------------------------------------------------

DEFAULT_PROMOTION_CRITERIA = {
    "enduring_knowledge": True,
    "operational_ephemera": False,
    "cross_session_recurrence": True,
    "contradiction_detection": True,
    "source_trust_weight": 0.5,
}


def _load_promotion_criteria(cm) -> dict[str, Any]:
    """Load promotion criteria from profile.json, falling back to defaults."""
    profile = cm._profile
    criteria = cm.promotion_criteria
    if not criteria:
        return DEFAULT_PROMOTION_CRITERIA.copy()
    merged = DEFAULT_PROMOTION_CRITERIA.copy()
    merged.update(criteria)
    return merged


def _get_role_trust_weight(cm, role_name: str) -> float:
    """Get trust weight for a domain role. Falls back to default 0.5."""
    domain_roles = cm.domain_roles
    if not domain_roles or role_name not in domain_roles:
        return DEFAULT_PROMOTION_CRITERIA["source_trust_weight"]
    return domain_roles[role_name].get("trust_weight", DEFAULT_PROMOTION_CRITERIA["source_trust_weight"])


def _deterministic_prefilter(captures: list[dict[str, Any]], committed: dict[str, str]) -> list[dict[str, Any] | None]:
    """Fast deterministic filter for obvious promotions/rejections.

    Returns list of captures that can be decided without model call:
    - Fast reject: operational ephemera (time, confirmations, status)
    - Fast promote: explicitly trusted source with high confidence patterns

    Captures that need model evaluation return None (defer to model).
    """
    import re

    EPHEMERA_PATTERNS = [
        r"\d{1,2}:\d{2}\s*(am|pm)?",
        r"(meeting|call|appointment)\s+(at|on)\s+\d",
        r"confirmed|acknowledged|noted",
        r"^(ok|okay|yep|nope|sure|thanks)$",
    ]

    TRUSTED_HIGH_CONFIDENCE_PATTERNS = [
        r"(policy|guideline|rule):\s*.+",
        r"(owner|lead|responsible):\s*.+",
        r"(escalate|assign to|contact)\s+.+@",
    ]

    results = []
    for cap in captures:
        content = cap.get("content", "")
        summary = cap.get("summary", "")
        text = f"{summary} {content}".lower()

        is_ephemera = any(re.search(p, text, re.IGNORECASE) for p in EPHEMERA_PATTERNS)
        if is_ephemera:
            results.append({
                "capture_index": len(results) + 1,
                "recommendation": "reject",
                "reason": "Operational ephemera (time/date/confirmation)",
                "confidence": 0.95,
            })
            continue

        is_trusted = any(re.search(p, text, re.IGNORECASE) for p in TRUSTED_HIGH_CONFIDENCE_PATTERNS)
        if is_trusted:
            results.append({
                "capture_index": len(results) + 1,
                "recommendation": "promote",
                "reason": "Trusted pattern (policy/owner/escalation)",
                "confidence": 0.90,
            })
            continue

        results.append(None)

    return results


def _extract_session_captures(session_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract capture turns from session history.

    Captures are stored as turns with role="capture" by the SDK runtime
    when session_type is "domain".
    """
    history = session_data.get("history", [])
    session_role = session_data.get("role", "unknown")
    captures = []
    for turn in history:
        if turn.get("role") == "capture":
            try:
                content = json.loads(turn.get("content", "{}"))
                captures.append({
                    "summary": content.get("summary", ""),
                    "content": content.get("content", ""),
                    "category": content.get("category", "general"),
                    "captured_at": turn.get("timestamp"),
                    "source_role": session_role,
                })
            except (json.JSONDecodeError, TypeError):
                # Non-JSON capture content — treat as raw
                captures.append({
                    "summary": turn.get("content", "")[:80],
                    "content": turn.get("content", ""),
                    "category": "general",
                    "captured_at": turn.get("timestamp"),
                    "source_role": session_role,
                })
    return captures


def _build_curator_prompt(
    captures: list[dict[str, Any]],
    committed_context: dict[str, str],
    criteria: dict[str, Any],
    domain_roles: dict[str, Any] | None = None,
) -> str:
    """Build the curator's evaluation prompt."""
    committed_text = "\n\n".join(
        f"## {stem.capitalize()}\n{content}"
        for stem, content in committed_context.items()
        if content and content.strip()
    ) or "(no committed context yet)"

    captures_text = ""
    for i, cap in enumerate(captures, 1):
        captures_text += f"## Capture {i}\n"
        captures_text += f"Summary: {cap['summary']}\n"
        captures_text += f"Category: {cap['category']}\n"
        source_role = cap.get("source_role", "unknown")
        trust = DEFAULT_PROMOTION_CRITERIA["source_trust_weight"]
        if domain_roles and source_role in domain_roles:
            trust = domain_roles[source_role].get("trust_weight", trust)
        captures_text += f"Source role: {source_role} (trust weight: {trust})\n"
        captures_text += f"Content:\n{cap['content']}\n\n"

    criteria_text = ""
    if criteria.get("enduring_knowledge"):
        criteria_text += "- Prefer enduring knowledge over operational ephemera\n"
    if criteria.get("operational_ephemera") is False:
        criteria_text += "- Reject operational ephemera (temporary state, confirmations, status updates)\n"
    if criteria.get("cross_session_recurrence"):
        criteria_text += "- Prioritize items that appear across multiple sessions\n"
    if criteria.get("contradiction_detection"):
        criteria_text += "- Flag items that contradict committed context for defer\n"

    trust_weight = criteria.get("source_trust_weight", 0.5)

    role_trust_text = ""
    if domain_roles:
        role_trust_text = "## Role Trust Weights\n"
        for role_name, role_config in domain_roles.items():
            tw = role_config.get("trust_weight", trust_weight)
            role_trust_text += f"- {role_name}: {tw}\n"
        role_trust_text += "\n"

    return f"""You are a curator agent evaluating session captures for promotion to staging.

## Captures to Evaluate

{captures_text}

{role_trust_text}## Current Committed Context

{committed_text}

## Promotion Criteria

{criteria_text}
- Default source trust weight: {trust_weight}

## Your Task

Evaluate each capture and decide whether it should be promoted to staging.

Respond with ONLY a valid JSON object:
{{
  "evaluations": [
    {{
      "capture_index": 1,
      "recommendation": "promote" | "defer" | "reject",
      "reason": "one sentence explaining the decision",
      "suggested_summary": "optional refined summary",
      "suggested_content": "optional refined content",
      "suggested_category": "optional refined category",
      "confidence": 0.0
    }}
  ]
}}

## Decision Rules
- promote: Content is enduring knowledge, adds value, and is non-conflicting. confidence >= 0.7.
- reject: Content is noise, operational ephemera, or clearly redundant.
- defer: Content may be valid but conflicts with existing context, is ambiguous, or confidence < 0.7.

Be strict about conflicts. Even partial contradictions should result in defer, not promote.
Higher source trust weight increases confidence in the capture's validity.
"""


def _call_curator_model(prompt: str) -> dict[str, Any]:
    """Call Claude to evaluate captures. Uses Haiku for speed."""
    from .brain import _api_key, _get_claude, _call_text_subprocess, _extract_json

    try:
        if _api_key():
            claude = _get_claude()
            response = claude.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
        else:
            raw = _call_text_subprocess(prompt)
        return _extract_json(raw)
    except Exception as e:
        log.exception("Curator model call failed")
        return {"evaluations": []}


def curate_session(
    cm,
    sm,
    session_id: str,
    criteria: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Evaluate a session's captures and promote worthy items to staging.

    Args:
        cm: ContextManager instance.
        sm: SessionManager instance.
        session_id: The session to curate.
        criteria: Optional promotion criteria override.
        dry_run: If True, return recommendations without creating staging entries.

    Returns:
        {
            "session_id": str,
            "captures_evaluated": int,
            "promoted": int,
            "deferred": int,
            "rejected": int,
            "entry_ids": list[str],  # staging entry IDs created (empty if dry_run)
            "evaluations": list[dict],
        }
    """
    session_data = sm.load(session_id)
    if not session_data:
        return {
            "session_id": session_id,
            "captures_evaluated": 0,
            "promoted": 0,
            "deferred": 0,
            "rejected": 0,
            "entry_ids": [],
            "evaluations": [],
            "error": "Session not found",
        }

    captures = _extract_session_captures(session_data)
    if not captures:
        return {
            "session_id": session_id,
            "captures_evaluated": 0,
            "promoted": 0,
            "deferred": 0,
            "rejected": 0,
            "entry_ids": [],
            "evaluations": [],
            "note": "No captures found in session",
        }

    if criteria is None:
        criteria = _load_promotion_criteria(cm)

    # Load committed context for contradiction detection
    committed = cm.load_committed()

    # Load domain role trust weights
    domain_roles = cm.domain_roles

    # Deterministic prefilter for fast-path
    prefilter_results = _deterministic_prefilter(captures, committed)

    # Separate fast-path decisions from captures needing model evaluation
    fast_track = [p for p in prefilter_results if p is not None]
    model_captures = [captures[i] for i, p in enumerate(prefilter_results) if p is None]

    # Build prompt and call model only for captures that need evaluation
    evaluations = []
    model_latency_ms = 0
    if model_captures:
        import time
        t0 = time.perf_counter()
        prompt = _build_curator_prompt(model_captures, committed, criteria, domain_roles)
        result = _call_curator_model(prompt)
        model_latency_ms = (time.perf_counter() - t0) * 1000
        evaluations = result.get("evaluations", [])

    log.debug(
        f"Curator session={session_id}: {len(fast_track)} fast-track, "
        f"{len(model_captures)} model-eval, latency={model_latency_ms:.0f}ms"
    )

    entry_ids = []
    promoted = 0
    deferred = 0
    rejected = 0
    all_evaluations = list(fast_track)

    # Count fast-track results
    for ft in fast_track:
        rec = ft.get("recommendation", "defer")
        if rec == "promote":
            promoted += 1
        elif rec == "defer":
            deferred += 1
        else:
            rejected += 1

    # Process model evaluations (map from model_captures indices back to original)
    model_to_original = []
    for i, pref in enumerate(prefilter_results):
        if pref is None:
            model_to_original.append(i)

    for eval_item in evaluations:
        idx = eval_item.get("capture_index", 0) - 1
        if idx < 0 or idx >= len(model_captures):
            continue

        original_idx = model_to_original[idx]
        cap = model_captures[idx]
        recommendation = eval_item.get("recommendation", "defer")

        eval_with_index = dict(eval_item)
        eval_with_index["capture_index"] = original_idx + 1
        all_evaluations.append(eval_with_index)

        if recommendation == "promote":
            if not dry_run:
                summary = eval_item.get("suggested_summary") or cap["summary"]
                content = eval_item.get("suggested_content") or cap["content"]
                category = eval_item.get("suggested_category") or cap["category"]
                source_role = cap.get("source_role", "unknown")
                entry_id = cm.create_staging_entry(
                    summary=summary,
                    content=content,
                    category=category,
                    source=f"curator:{source_role}:{session_id}",
                )
                entry_ids.append(entry_id)
            promoted += 1
        elif recommendation == "defer":
            deferred += 1
        else:
            rejected += 1

    return {
        "session_id": session_id,
        "captures_evaluated": len(captures),
        "promoted": promoted,
        "deferred": deferred,
        "rejected": rejected,
        "entry_ids": entry_ids,
        "evaluations": all_evaluations,
    }


def curate_all_sessions(
    cm,
    sm,
    criteria: dict[str, Any] | None = None,
    dry_run: bool = False,
    max_workers: int = 4,
) -> list[dict[str, Any]]:
    """Curate all sessions that have captures.

    Uses parallel processing for improved throughput when curating multiple sessions.

    Args:
        cm: ContextManager instance.
        sm: SessionManager instance.
        criteria: Optional promotion criteria.
        dry_run: If True, don't create staging entries.
        max_workers: Max concurrent sessions to process (default 4).

    Returns:
        List of per-session results.
    """
    sessions = sm.list_sessions()
    session_ids = [s.get("session_id", "") for s in sessions if s.get("session_id")]

    if not session_ids:
        return []

    if len(session_ids) == 1:
        result = curate_session(cm, sm, session_ids[0], criteria=criteria, dry_run=dry_run)
        return [result] if result.get("captures_evaluated", 0) > 0 else []

    results = []

    def curate_one(sid: str) -> dict[str, Any] | None:
        try:
            result = curate_session(cm, sm, sid, criteria=criteria, dry_run=dry_run)
            return result if result.get("captures_evaluated", 0) > 0 else None
        except Exception as e:
            log.exception(f"Failed to curate session {sid}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(curate_one, sid): sid for sid in session_ids}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    return results


# ---------------------------------------------------------------------------
# Daemon mode and scheduling
# ---------------------------------------------------------------------------

class CuratorDaemon:
    """Background daemon that periodically curates sessions."""

    def __init__(
        self,
        cm,
        sm,
        interval_minutes: int = 30,
        criteria: dict[str, Any] | None = None,
        dry_run: bool = False,
    ):
        self.cm = cm
        self.sm = sm
        self.interval_minutes = interval_minutes
        self.criteria = criteria
        self.dry_run = dry_run
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the daemon in a background thread."""
        if self._thread and self._thread.is_alive():
            log.warning("Daemon already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="curator-daemon")
        self._thread.start()
        log.info(f"Curator daemon started with {self.interval_minutes}min interval")

    def stop(self) -> None:
        """Stop the daemon gracefully."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Curator daemon stopped")

    def _run_loop(self) -> None:
        """Main daemon loop."""
        log.info(f"Curator daemon running (interval: {self.interval_minutes}min)")
        while not self._stop_event.is_set():
            try:
                self._curate_once()
            except Exception as e:
                log.exception(f"Curator daemon iteration failed: {e}")

            self._stop_event.wait(self.interval_minutes * 60)

        log.info("Curator daemon exiting")

    def _curate_once(self) -> dict[str, Any]:
        """Run a single curation pass. Returns summary."""
        timestamp = datetime.now().isoformat()
        results = curate_all_sessions(self.cm, self.sm, criteria=self.criteria, dry_run=self.dry_run)

        total_promoted = sum(r.get("promoted", 0) for r in results)
        total_deferred = sum(r.get("deferred", 0) for r in results)
        total_rejected = sum(r.get("rejected", 0) for r in results)
        total_evaluated = sum(r.get("captures_evaluated", 0) for r in results)

        log.info(
            f"[{timestamp}] Curator: evaluated={total_evaluated}, "
            f"promoted={total_promoted}, deferred={total_deferred}, rejected={total_rejected}"
        )

        return {
            "timestamp": timestamp,
            "sessions_curated": len(results),
            "total_evaluated": total_evaluated,
            "total_promoted": total_promoted,
            "total_deferred": total_deferred,
            "total_rejected": total_rejected,
        }


def start_curator_daemon(
    cm,
    interval_minutes: int = 30,
    criteria: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> CuratorDaemon:
    """Start the curator daemon. Returns the daemon instance."""
    from .session_manager import SessionManager

    sm = SessionManager(data_dir=cm._root / "data" / "sessions")
    daemon = CuratorDaemon(
        cm=cm,
        sm=sm,
        interval_minutes=interval_minutes,
        criteria=criteria,
        dry_run=dry_run,
    )
    daemon.start()
    return daemon


def schedule_curator(
    cm,
    interval_minutes: int = 30,
    criteria: dict[str, Any] | None = None,
    dry_run: bool = False,
    run_once: bool = False,
) -> dict[str, Any]:
    """Run curator on a schedule (or once).

    Args:
        cm: ContextManager instance.
        interval_minutes: How often to run (default 30).
        criteria: Optional promotion criteria.
        dry_run: If True, don't create staging entries.
        run_once: If True, run once and return (not daemon mode).

    Returns:
        If run_once: dict with curation results.
        If daemon: dict with daemon control info.
    """
    from .session_manager import SessionManager

    sm = SessionManager(data_dir=cm._root / "data" / "sessions")

    if run_once:
        results = curate_all_sessions(cm, sm, criteria=criteria, dry_run=dry_run)
        return {
            "mode": "once",
            "results": results,
        }

    daemon = CuratorDaemon(
        cm=cm,
        sm=sm,
        interval_minutes=interval_minutes,
        criteria=criteria,
        dry_run=dry_run,
    )
    daemon.start()

    return {
        "mode": "daemon",
        "interval_minutes": interval_minutes,
        "daemon_active": True,
    }
