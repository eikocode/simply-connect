"""
Committed-Context Agent Framework — Admin CLI

Entry point registered as:
    simply-connect-admin
    sc-admin

Subcommands:
    sc-admin review              — interactive staging review
    sc-admin review --auto       — AI-powered auto-review
    sc-admin intake              — bootstrap context from AIOS files (profile-driven)
    sc-admin ingest <file>       — ingest a document into staging
    sc-admin publish-minpaku     — compatibility helper for publishing an approved Minpaku listing draft
    sc-admin update-minpaku      — compatibility helper for updating an existing published Minpaku listing
    sc-admin unlist-minpaku      — compatibility helper for deleting an existing published Minpaku listing
    sc-admin init <domain>       — initialise deployment from a domain template
    sc-admin new-domain          — scaffold a new domain template interactively
    sc-admin status              — context health summary
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

DIVIDER = "─" * 52
THIN = "·" * 52


def _extract_listing_payload(content: str) -> dict:
    match = re.search(r"```json\s*(\{.*?\})\s*```", content, flags=re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _iter_listing_sections(markdown: str) -> list[str]:
    parts = re.split(r"(?=^##\s+)", markdown, flags=re.MULTILINE)
    return [part.strip() for part in parts if part.strip().startswith("## ")]


def _find_matching_listing_record(markdown: str, payload: dict) -> str | None:
    if not payload:
        return None
    property_id = str(payload.get("propertyId") or "").strip()
    platform = str(payload.get("platform") or "").strip()
    title = str(payload.get("title") or "").strip()
    source_ref = str(payload.get("source_property_ref") or "").strip()
    for section in _iter_listing_sections(markdown):
        if "- Remote listing ID:" not in section:
            continue
        if property_id and f"- Property ID: {property_id}" not in section:
            continue
        if platform and f"- Platform: {platform}" not in section:
            continue
        if title and not section.startswith(f"## {title}"):
            continue
        if source_ref and f"- Source property ref: {source_ref}" not in section:
            continue
        return section
    return None


def _listing_next_step(cm, entry: dict) -> str:
    payload = _extract_listing_payload(entry.get("content", ""))
    committed = cm.load_committed().get("listing_publications", "")
    record = _find_matching_listing_record(committed, payload)
    status = str(payload.get("status") or "").strip().lower()
    if record:
        if status in {"inactive", "paused"}:
            return "  Next: return to sc --role operator and ask it to update the live listing status."
        return "  Next: return to sc --role operator and ask it to update the live listing."
    return "  Next: return to sc --role operator and ask it to publish the approved listing."


# ---------------------------------------------------------------------------
# Review subcommand
# ---------------------------------------------------------------------------

def cmd_review(cm, auto: bool) -> None:
    from simply_connect import brain
    from simply_connect.ext_loader import load_active_extensions

    entries = cm.list_staging(status="unconfirmed")

    if not entries:
        print("\n  No unconfirmed staging entries to review.\n")
        return

    print(f"\n  {len(entries)} unconfirmed entr{'y' if len(entries) == 1 else 'ies'} to review.\n")

    approved = rejected = deferred = skipped = 0
    active_extensions = load_active_extensions(cm)

    for i, entry in enumerate(entries, 1):
        entry_id = entry.get("id", "?")
        summary = entry.get("summary", "(no summary)")
        category = entry.get("category", "general")
        captured = entry.get("captured", "unknown")
        source = entry.get("source", "operator")
        content = entry.get("content", "")

        print(DIVIDER)
        print(f"  Entry {i} of {len(entries)}")
        print(f"  ID:        {entry_id[:8]}...")
        print(f"  Source:    {source}  ·  Category: {category}")
        print(f"  Captured:  {captured[:19]}")
        print(f"  Summary:   {summary}")
        print()
        print("  Content:")
        for line in content.strip().splitlines():
            print(f"    {line}")
        print()

        # Extension-aware review analysis
        committed = cm.load_committed()
        review = None
        for ext in active_extensions:
            review_hook = getattr(ext["module"], "review_staging_entry", None)
            if not callable(review_hook):
                continue
            review = review_hook(cm, entry)
            if review is not None:
                break
        if review is None:
            review = brain.review_staging_entry(entry, committed)
        rec = review.get("recommendation", "defer")
        reason = review.get("reason", "")
        conflicts = review.get("conflicts", [])
        confidence = review.get("confidence", 0.0)

        print(f"  AI review: {rec.upper()}  (confidence: {confidence:.0%})")
        print(f"  Reason:    {reason}")
        if conflicts:
            print("  Conflicts:")
            for c in conflicts:
                print(f"    ⚠  {c}")
        print()

        if auto:
            # Auto-review mode
            if rec == "approve" and confidence >= 0.85 and not conflicts:
                success = cm.promote_to_committed(entry_id, reviewed_by="ai-admin")
                if success:
                    print(f"  AUTO-APPROVED: {summary}")
                    approved += 1
                else:
                    print(f"  AUTO-APPROVE FAILED (write error): deferring")
                    cm.update_staging_status(entry_id, "deferred", "ai-admin")
                    deferred += 1
            else:
                cm.update_staging_status(entry_id, "deferred", "ai-admin")
                print(f"  DEFERRED: {summary}")
                if conflicts:
                    print(f"           (conflict detected — human review required)")
                elif confidence < 0.85:
                    print(f"           (low confidence — human review required)")
                deferred += 1
            print()
        else:
            # Interactive mode
            while True:
                choice = input("  [A]pprove / [R]eject / [D]efer / [S]kip: ").strip().lower()
                if choice in ("a", "approve"):
                    success = cm.promote_to_committed(entry_id, reviewed_by="human")
                    if success:
                        target = cm.CATEGORY_MAP.get(category, "business.md")
                        print(f"  ✓ Approved and committed to context/{target}")
                        for ext in active_extensions:
                            approval_hook = getattr(ext["module"], "on_staging_approved", None)
                            if not callable(approval_hook):
                                continue
                            try:
                                hook_result = approval_hook(cm, entry)
                            except Exception as exc:
                                print(f"  ! Post-approval sync failed: {exc}")
                                continue
                            if hook_result:
                                message = hook_result.get("message")
                                if hook_result.get("ok") and message:
                                    print(f"  ↳ {message}")
                                    if hook_result.get("host_id"):
                                        print(f"    Using Minpaku host id {hook_result['host_id']}.")
                                    if hook_result.get("property_id") and not hook_result.get("deleted_property_id"):
                                        print(f"    Minpaku property is now live as {hook_result['property_id']}.")
                                    if hook_result.get("deleted_property_id"):
                                        print(f"    Minpaku property {hook_result['deleted_property_id']} has been removed from live inventory.")
                                elif not hook_result.get("ok", True):
                                    print(f"  ! Post-approval sync failed: {hook_result.get('error', 'Unknown error')}")
                        if category == "listing_publications":
                            print(_listing_next_step(cm, entry))
                        approved += 1
                    else:
                        target = cm.CATEGORY_MAP.get(category, "business.md")
                        print(f"  ✗ Failed to write to context/{target}  (category: {category!r})")
                        print(f"    Run: sc-admin status  — to check context health")
                    break
                elif choice in ("r", "reject"):
                    reason_input = input("  Reason (optional): ").strip()
                    cm.update_staging_status(entry_id, "rejected", "human")
                    print(f"  ✗ Rejected{f': {reason_input}' if reason_input else ''}")
                    rejected += 1
                    break
                elif choice in ("d", "defer"):
                    cm.update_staging_status(entry_id, "deferred", "human")
                    print("  → Deferred for later review")
                    deferred += 1
                    break
                elif choice in ("s", "skip"):
                    print("  → Skipped (status unchanged)")
                    skipped += 1
                    break
                else:
                    print("  Please enter A, R, D, or S.")
            print()

    print(DIVIDER)
    print(f"  Review complete: {approved} approved  ·  {rejected} rejected  "
          f"·  {deferred} deferred  ·  {skipped} skipped")
    print()


# ---------------------------------------------------------------------------
# Intake subcommand (profile-driven)
# ---------------------------------------------------------------------------

def _call_claude_extract(prompt: str, api_key: str) -> str:
    """Call Claude for a single extraction prompt.

    Uses the Anthropic SDK when an API key is available; falls back to
    `claude --print` subprocess (Claude Code OAuth) when it is not.
    """
    if api_key:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    else:
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


def cmd_intake(cm) -> None:
    """Bootstrap committed context from source files via staging (profile-driven mapping)."""
    import os
    from dotenv import load_dotenv

    # Match sc: deployment-local .env should win over inherited shell env.
    load_dotenv(cm._root / ".env", override=True)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")

    # Locate AIOS context directory
    aios_context = _find_aios_context(cm._root)
    if not aios_context:
        print(
            "\n  Could not find AIOS context directory.\n"
            "  Expected: context/business-info.md somewhere above the project root.\n"
            "  Run sc-admin intake from inside or adjacent to your aios-starter-kit directory.\n"
        )
        return

    print(f"\n  Found AIOS context: {aios_context}")
    print(f"  Profile:            {cm.profile_name}")
    print()

    # Use intake sources from profile (not hardcoded)
    intake_sources = cm.intake_sources  # {filename: {category, description}}
    if not intake_sources:
        print("  No intake sources defined in this profile. Nothing to import.\n")
        return

    mode = "sdk" if api_key else "claude cli (OAuth)"
    print(f"  Using: {mode}")
    print()

    created = 0

    for filename, source_info in intake_sources.items():
        category = source_info.get("category", "general")
        description = source_info.get("description", f"{filename} content")

        aios_file = aios_context / filename
        if not aios_file.exists():
            print(f"  Skipping {filename} (not found)")
            continue

        content = aios_file.read_text(encoding="utf-8").strip()
        if not content or "[" in content[:200]:
            print(f"  Skipping {filename} (appears to be empty template)")
            continue

        print(f"  Processing {filename}...", end=" ", flush=True)

        prompt = f"""You are extracting context for a {cm.profile_name} assistant from an AIOS context file.

Source file: {filename}
Target category: {category}
Purpose: {description}

Source content:
{content}

Extract only the information that would be relevant and useful for a {cm.profile_name} assistant.
Remove template instructions, placeholder text, and section headers.
Return just the extracted facts as clean, concise prose or a short list.
If there is nothing useful to extract, respond with exactly: SKIP
"""
        try:
            extracted = _call_claude_extract(prompt, api_key)

            if extracted.upper() == "SKIP" or not extracted:
                print("skipped (no useful content)")
                continue

            entry_id = cm.create_staging_entry(
                summary=f"AIOS intake: {filename}",
                content=extracted,
                category=category,
                source="intake",
            )
            print(f"staged ({entry_id[:8]}...)")
            created += 1

        except Exception as e:
            print(f"error: {e}")
            log.exception(f"Intake failed for {filename}")

    print()
    if created:
        print(f"  {created} staging entr{'y' if created == 1 else 'ies'} created from AIOS context.")
        print("  Run sc-admin review to approve and commit to context.")
    else:
        print("  No staging entries created — AIOS context files may be empty templates.")
    print()


def _find_aios_context(project_root: Path) -> Path | None:
    """
    Walk up from project_root looking for a context/ directory
    containing business-info.md (AIOS pattern).
    """
    candidate = project_root
    for _ in range(6):
        ctx = candidate / "context"
        if (ctx / "business-info.md").exists():
            return ctx
        candidate = candidate.parent
    return None


# ---------------------------------------------------------------------------
# Ingest subcommand
# ---------------------------------------------------------------------------

def ingest_to_staging(cm, filepath: Path) -> dict:
    """Ingest a document file into staging and return a structured result."""
    from simply_connect.ingestion import ingest_document
    from simply_connect.ext_loader import load_active_extensions

    if not filepath.exists():
        return {
            "ok": False,
            "error": f"File not found: {filepath}",
            "entries": [],
        }

    committed = cm.load_committed()
    result = ingest_document(filepath, committed, cm._profile)

    if not result.get("success"):
        return {
            "ok": False,
            "error": result.get("error", "unknown error"),
            "entries": [],
        }

    extractions = result.get("extractions", [])
    if not extractions:
        return {
            "ok": False,
            "error": "No useful content extracted from document.",
            "entries": [],
        }

    created_entries = []
    for item in extractions:
        summary = item.get("summary", filepath.name)
        content = item.get("content", "")
        category = item.get("category", "general")
        if not content.strip():
            continue
        entry_id = cm.create_staging_entry(
            summary=summary,
            content=content,
            category=category,
            source=f"ingest:{filepath.name}",
        )
        created_entries.append(
            {
                "entry_id": entry_id,
                "summary": summary,
                "category": category,
            }
        )

    if not created_entries:
        return {
            "ok": False,
            "error": "No content to stage after filtering.",
            "entries": [],
        }

    final_result = {
        "ok": True,
        "filepath": str(filepath),
        "filename": filepath.name,
        "entries": created_entries,
    }

    for ext in load_active_extensions(cm):
        hook = getattr(ext["module"], "on_ingest_to_staging", None)
        if not callable(hook):
            continue
        hook_result = hook(cm, filepath, final_result)
        if hook_result:
            final_result.setdefault("post_ingest", []).append(hook_result)

    return final_result


def cmd_ingest(cm, filepath: Path) -> None:
    """Ingest a document file into staging."""
    print(f"\n  Ingesting: {filepath.name}")
    print(f"  Profile:   {cm.profile_name}")
    print()

    result = ingest_to_staging(cm, filepath)
    if not result.get("ok"):
        print(f"  Failed: {result.get('error', 'unknown error')}\n")
        return

    entries = result.get("entries", [])
    for item in entries:
        print(f"  Staged ({item['category']}): {item['summary']}")
        print(f"  Entry ID: {item['entry_id'][:8]}...")
        print()

    for hook_result in result.get("post_ingest", []):
        message = hook_result.get("message")
        if message:
            print(f"  ↳ {message}")
            print()

    print(f"  {len(entries)} staging entr{'y' if len(entries) == 1 else 'ies'} created.")
    print("  Run sc-admin review to approve and commit.")
    print()


# ---------------------------------------------------------------------------
# Publish Minpaku subcommand
# ---------------------------------------------------------------------------

def cmd_publish_minpaku(cm, entry_id: str | None = None) -> None:
    """Compatibility helper for publishing an approved Minpaku listing draft via the active domain extension."""
    from simply_connect.ext_loader import load_active_extensions

    for ext in load_active_extensions(cm):
        publish_fn = getattr(ext["module"], "publish_minpaku_listing", None)
        if not callable(publish_fn):
            continue

        result = publish_fn(cm, entry_id=entry_id)
        print()
        if result.get("ok"):
            print("  Compatibility helper used: prefer sc --role operator for Minpaku domain actions.")
            print("  Published Minpaku listing")
            print(f"  Title:       {result.get('title', '(unknown)')}")
            print(f"  Listing ID:  {result.get('listing_id', '(unknown)')}")
            print(f"  Property ID: {result.get('property_id', '(unknown)')}")
            print(f"  Source:      {result.get('source_property_ref', '(not provided)')}")
            print(f"  Entry ID:    {str(result.get('entry_id', '(unknown)'))[:8]}...")
            print()
            return

        print(f"  Publish failed: {result.get('error', 'Unknown error')}")
        available_entries = result.get("available_entries") or []
        if available_entries:
            print("  Approved draft entries:")
            for entry in available_entries:
                print(f"    - {entry['id'][:8]}...  {entry['summary']}")
        print()
        return

    print(
        "\n  Publish failed: no active extension in this deployment can publish Minpaku listings.\n"
    )


def cmd_update_minpaku(cm, entry_id: str | None = None) -> None:
    """Compatibility helper for updating an existing published Minpaku listing via the active domain extension."""
    from simply_connect.ext_loader import load_active_extensions

    for ext in load_active_extensions(cm):
        update_fn = getattr(ext["module"], "update_minpaku_listing", None)
        if not callable(update_fn):
            continue

        result = update_fn(cm, entry_id=entry_id)
        print()
        if result.get("ok"):
            print("  Compatibility helper used: prefer sc --role operator for Minpaku domain actions.")
            print("  Updated Minpaku listing")
            print(f"  Title:       {result.get('title', '(unknown)')}")
            print(f"  Listing ID:  {result.get('listing_id', '(unknown)')}")
            print(f"  Property ID: {result.get('property_id', '(unknown)')}")
            print(f"  Source:      {result.get('source_property_ref', '(not provided)')}")
            print(f"  Entry ID:    {str(result.get('entry_id', '(unknown)'))[:8]}...")
            print()
            return

        print(f"  Update failed: {result.get('error', 'Unknown error')}")
        available_entries = result.get("available_entries") or []
        if available_entries:
            print("  Approved draft entries:")
            for entry in available_entries:
                print(f"    - {entry['id'][:8]}...  {entry['summary']}")
        print()
        return

    print(
        "\n  Update failed: no active extension in this deployment can update Minpaku listings.\n"
    )


def cmd_unlist_minpaku(cm, entry_id: str | None = None) -> None:
    """Compatibility helper for deleting an existing published Minpaku listing via the active domain extension."""
    from simply_connect.ext_loader import load_active_extensions

    for ext in load_active_extensions(cm):
        delete_fn = getattr(ext["module"], "delete_minpaku_listing", None)
        if not callable(delete_fn):
            continue

        result = delete_fn(cm, entry_id=entry_id)
        print()
        if result.get("ok"):
            print("  Compatibility helper used: prefer sc --role operator for Minpaku domain actions.")
            print("  Unlisted Minpaku listing")
            print(f"  Title:       {result.get('title', '(unknown)')}")
            print(f"  Listing ID:  {result.get('listing_id', '(unknown)')}")
            print(f"  Property ID: {result.get('property_id', '(unknown)')}")
            print(f"  Source:      {result.get('source_property_ref', '(not provided)')}")
            print(f"  Entry ID:    {str(result.get('entry_id', '(unknown)'))[:8]}...")
            print()
            return

        print(f"  Unlist failed: {result.get('error', 'Unknown error')}")
        available_entries = result.get("available_entries") or []
        if available_entries:
            print("  Approved draft entries:")
            for entry in available_entries:
                print(f"    - {entry['id'][:8]}...  {entry['summary']}")
        print()
        return

    print(
        "\n  Unlist failed: no active extension in this deployment can delete Minpaku listings.\n"
    )


# ---------------------------------------------------------------------------
# Init subcommand
# ---------------------------------------------------------------------------

def _resolve_domains_dir() -> Path:
    """Resolve the domains library directory.

    Resolution order:
    1. SC_DOMAINS_DIR env var (explicit override)
    2. Sibling repo: ../simply-connect-domains/domains (relative to engine root)
    3. Local fallback: domains/ next to engine (for development)
    """
    import os
    env_override = os.getenv("SC_DOMAINS_DIR", "")
    if env_override:
        return Path(env_override)

    engine_root = Path(__file__).parent.parent
    sibling = engine_root.parent / "simply-connect-domains" / "domains"
    if sibling.exists():
        return sibling

    return engine_root / "domains"


def cmd_init(profile_name: str, target_root: Path, force: bool) -> None:
    """Initialise a new deployment from a named domain template."""
    import shutil

    domains_dir = _resolve_domains_dir()
    profile_dir = domains_dir / profile_name

    if not profile_dir.exists():
        available = (
            [p.name for p in domains_dir.iterdir() if p.is_dir()]
            if domains_dir.exists()
            else []
        )
        print(f"\n  Domain '{profile_name}' not found.")
        print(f"  Library: {domains_dir}")
        if available:
            print(f"  Available domains: {', '.join(sorted(available))}")
        else:
            print(f"  No domains found. Set SC_DOMAINS_DIR or clone simply-connect-domains alongside simply-connect.")
        print()
        return

    print(f"\n  Initialising from profile: {profile_name}")
    print(f"  Target directory: {target_root}")
    print()

    # Identity files are always overwritten — they define the domain deployment.
    # Content files (context/*.md) are protected unless --force is set.
    IDENTITY_FILES = {"profile.json", "AGENT.md"}

    files_copied = 0
    files_skipped = 0

    for src in sorted(profile_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(profile_dir)
        dst = target_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        is_identity = rel.name in IDENTITY_FILES and rel.parent == Path(".")
        always_overwrite = is_identity or force

        if dst.exists() and not always_overwrite:
            print(f"  skipped  {rel}  (already exists — use --force to overwrite)")
            files_skipped += 1
        else:
            shutil.copy2(src, dst)
            action = "overwrote" if dst.exists() else "copied  "
            print(f"  {action}  {rel}")
            files_copied += 1

    print()
    print(f"  Done: {files_copied} files copied, {files_skipped} skipped.")
    if files_skipped:
        print("  Run with --force to overwrite existing files.")
    print()
    print("  Next steps:")
    print("    1. Edit context/ files with your actual data")
    print("    2. sc-admin intake       — import from AIOS if available")
    print("    3. sc-admin ingest <f>   — ingest a document")
    print("    4. sc-admin review       — approve staged updates")
    print("    5. sc-admin status       — verify context health")
    print()


# ---------------------------------------------------------------------------
# New-domain subcommand
# ---------------------------------------------------------------------------

def cmd_new_domain(domains_dir: Path) -> None:
    """Interactive wizard — scaffold a new domain template in the domains library."""
    import json
    import re

    print()
    print("  New Domain Wizard")
    print(DIVIDER)
    print("  Press Enter to accept defaults shown in [brackets].")
    print()

    # 1. Domain name
    while True:
        name = input("  Domain name (e.g. minpaku, super-landlord): ").strip().lower()
        if re.match(r"^[a-z][a-z0-9-]*$", name):
            break
        print("  Use lowercase letters, numbers, and hyphens only.")

    domain_dir = domains_dir / name
    if domain_dir.exists():
        print(f"\n  Domain '{name}' already exists at {domain_dir}")
        overwrite = input("  Overwrite? [y/N]: ").strip().lower()
        if overwrite != "y":
            print("  Cancelled.\n")
            return

    # 2. Display name
    display_default = name.replace("-", " ").title()
    display = input(f"  Display name [{display_default}]: ").strip() or display_default

    # 3. Context files
    print()
    print("  Context files — what topics does this domain track?")
    print("  Enter names separated by commas (no .md extension).")
    ctx_input = input("  Context files [properties, operations, contacts]: ").strip()
    if ctx_input:
        context_files = [f.strip().lower().replace(" ", "_") for f in ctx_input.split(",") if f.strip()]
    else:
        context_files = ["properties", "operations", "contacts"]

    # 4. Roles
    print()
    print("  Roles — who are the different users of this domain?")
    print("  Enter role names separated by commas, or press Enter to skip.")
    roles_input = input("  Roles (e.g. host, guest, housekeeping): ").strip()
    roles = (
        [r.strip().lower().replace(" ", "_") for r in roles_input.split(",") if r.strip()]
        if roles_input else []
    )

    # 5. Extension
    print()
    ext_input = input("  Does this domain need a live-data extension (external API)? [y/N]: ").strip().lower()
    needs_extension = ext_input == "y"

    # Build the domain
    print()
    print(f"  Creating domain: {name}")
    print(DIVIDER)

    domain_dir.mkdir(parents=True, exist_ok=True)

    # profile.json
    profile: dict = {
        "name": display,
        "context_files": context_files,
        "category_map": {f: f"{f}.md" for f in context_files},
        "intake_sources": {
            "business-info.md": {
                "category": context_files[0] if context_files else "general",
                "description": f"Business information relevant to {display}",
            }
        },
        "extensions": [name] if needs_extension else [],
        "roles": {},
    }

    if roles:
        domain_upper = name.replace("-", "_").upper()
        for role in roles:
            profile["roles"][role] = {
                "agent_md": f"roles/{role}/AGENT.md",
                "context_filter": context_files,
                "telegram_bot_env": f"{domain_upper}_{role.upper()}_BOT_TOKEN",
            }

    (domain_dir / "profile.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print("  created  profile.json")

    # AGENT.md
    ctx_list = "\n".join(f"- **{f}.md** — [describe what this file tracks]" for f in context_files)
    agent_md = f"""# {display} — Agent Instructions

You are a {display} assistant with access to committed context about this business domain.

## Purpose

[Describe what this agent helps with in 2-3 sentences.]

## Context Files

{ctx_list}

## How to Help

- Draw on committed context to answer questions accurately
- When you learn something new, capture it: "Captured — pending admin review"
- Flag staging context clearly when you use it
- If context is missing, say so and ask the operator to fill it in

## Capture Instruction

When the operator says "remember this" or similar, extract the key fact and respond with:
> Captured — pending admin review.
"""
    (domain_dir / "AGENT.md").write_text(agent_md, encoding="utf-8")
    print("  created  AGENT.md")

    # context/ skeleton files
    ctx_dir = domain_dir / "context"
    ctx_dir.mkdir(exist_ok=True)
    (ctx_dir / "README.md").write_text(
        f"# {display} — Committed Context\n\n"
        "These files are the authoritative knowledge base for this domain.\n"
        "Edit directly or use `sc-admin intake` / `sc-admin ingest` to populate.\n",
        encoding="utf-8",
    )
    for f in context_files:
        (ctx_dir / f"{f}.md").write_text(
            f"# {f.replace('_', ' ').title()}\n\n<!-- Fill in {f} information here -->\n",
            encoding="utf-8",
        )
        print(f"  created  context/{f}.md")

    # roles/
    if roles:
        roles_dir = domain_dir / "roles"
        roles_dir.mkdir(exist_ok=True)
        for role in roles:
            role_dir = roles_dir / role
            role_dir.mkdir(exist_ok=True)
            role_agent = f"""# {display} — {role.title()} Role

You are the {role} interface for the {display} system.

## Scope

[Describe what this role can see and do.]

## Tone

[Describe how this role should communicate — formal, casual, brief, etc.]

## Context Access

This role sees: {', '.join(context_files)}

## Capture Instruction

When the {role} provides new information, capture it for admin review.
"""
            (role_dir / "AGENT.md").write_text(role_agent, encoding="utf-8")
            print(f"  created  roles/{role}/AGENT.md")

    # admin/intake_map.md
    admin_dir = domain_dir / "admin"
    admin_dir.mkdir(exist_ok=True)
    first = context_files[0] if context_files else "general"
    second = context_files[1] if len(context_files) > 1 else first
    last = context_files[-1] if context_files else "general"
    intake_map = f"""# {display} — Intake Map

Maps AIOS context sources to domain context files.

| AIOS Source | Target File | What to Extract |
|---|---|---|
| business-info.md | {first}.md | [describe] |
| personal-info.md | {second}.md | [describe] |
| current-data.md  | {last}.md  | [describe] |

Edit `profile.json → intake_sources` to align with this map.
"""
    (admin_dir / "intake_map.md").write_text(intake_map, encoding="utf-8")
    print("  created  admin/intake_map.md")

    # extension/
    if needs_extension:
        ext_dir = domain_dir / "extension"
        ext_dir.mkdir(exist_ok=True)
        (ext_dir / "__init__.py").write_text("", encoding="utf-8")

        env_prefix = name.upper().replace("-", "_")
        class_name = display.replace(" ", "").replace("-", "")
        tools_py = f'''"""
{display} Extension — Tool Definitions

Provides live data access tools for the {display} domain.
Add real tool definitions below and implement their handlers.
"""

from __future__ import annotations
from typing import Any

# ---------------------------------------------------------------------------
# Tool schemas (Anthropic-compatible)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {{
        "name": "{name.replace("-", "_")}_example",
        "description": "[Describe what this tool fetches or does]",
        "input_schema": {{
            "type": "object",
            "properties": {{
                "query": {{
                    "type": "string",
                    "description": "[Describe the query parameter]",
                }}
            }},
            "required": ["query"],
        }},
    }},
]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(name: str, args: dict[str, Any], cm) -> str:
    """Route a tool call to the appropriate handler."""
    if name == "{name.replace("-", "_")}_example":
        return _example(args.get("query", ""))
    raise ValueError(f"Unknown tool: {{name}}")


def _example(query: str) -> str:
    """Replace with real implementation."""
    # from .client import {class_name}Client
    # with {class_name}Client() as client:
    #     return str(client.search(query))
    return f"[{name} extension] query: {{query}} — implement _example() in tools.py"
'''
        (ext_dir / "tools.py").write_text(tools_py, encoding="utf-8")

        client_py = f'''"""
{display} API Client

Wraps the {display} external API using httpx.
Set {env_prefix}_API_URL and {env_prefix}_API_KEY in .env
"""

from __future__ import annotations
import os
import httpx
from dotenv import load_dotenv

load_dotenv(override=False)

BASE_URL = os.getenv("{env_prefix}_API_URL", "http://localhost:8000")
API_KEY  = os.getenv("{env_prefix}_API_KEY", "")


class {class_name}Client:
    """HTTP client for the {display} API."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={{"X-API-Key": API_KEY}},
            timeout=10.0,
        )

    def example_request(self, query: str) -> dict:
        """Replace with real API endpoint."""
        response = self._client.get("/example", params={{"q": query}})
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
'''
        (ext_dir / "client.py").write_text(client_py, encoding="utf-8")
        print("  created  extension/__init__.py")
        print("  created  extension/tools.py")
        print("  created  extension/client.py")

    # Summary
    print()
    print(f"  Domain '{name}' scaffolded at:")
    print(f"  {domain_dir}")
    print()
    print("  Next steps:")
    print(f"    1. Edit AGENT.md              — define the agent's persona and purpose")
    print(f"    2. Edit context/*.md           — describe what each file tracks")
    if roles:
        print(f"    3. Edit roles/*/AGENT.md      — define per-role scope and tone")
    if needs_extension:
        step = 4 if roles else 3
        print(f"    {step}. Edit extension/tools.py   — implement real API calls")
        print(f"    {step + 1}. Edit extension/client.py  — implement real API client")
    print(f"    n. sc-admin init {name}  — deploy to simply-connect")
    print()


# ---------------------------------------------------------------------------
# Curate subcommand
# ---------------------------------------------------------------------------

def cmd_curate(cm, session: str | None = None, curate_all: bool = False, dry_run: bool = False) -> None:
    from simply_connect.curator import curate_session, curate_all_sessions
    from simply_connect.session_manager import SessionManager

    sm = SessionManager(data_dir=cm._root / "data" / "sessions")

    if session:
        results = [curate_session(cm, sm, session, dry_run=dry_run)]
    elif curate_all:
        results = curate_all_sessions(cm, sm, dry_run=dry_run)
    else:
        # Default: curate most recent session with captures
        sessions = sm.list_sessions()
        if not sessions:
            print("\n  No sessions found.")
            return
        # Find first session with captures
        for s in sessions:
            sid = s.get("session_id", "")
            if not sid:
                continue
            session_data = sm.load(sid)
            history = session_data.get("history", [])
            if any(turn.get("role") == "capture" for turn in history):
                results = [curate_session(cm, sm, sid, dry_run=dry_run)]
                break
        else:
            print("\n  No sessions with captures found. Use --all to curate all sessions.")
            return

    print()
    mode = "DRY RUN — no staging entries created" if dry_run else "LIVE"
    print(f"  Curator ({mode})")
    print(DIVIDER)

    total_promoted = 0
    total_deferred = 0
    total_rejected = 0
    total_evaluated = 0

    for result in results:
        sid = result.get("session_id", "unknown")
        evaluated = result.get("captures_evaluated", 0)
        promoted = result.get("promoted", 0)
        deferred = result.get("deferred", 0)
        rejected = result.get("rejected", 0)
        entry_ids = result.get("entry_ids", [])
        error = result.get("error")
        note = result.get("note")

        if error:
            print(f"\n  Session {sid}: {error}")
            continue
        if note and evaluated == 0:
            print(f"\n  Session {sid}: {note}")
            continue

        total_evaluated += evaluated
        total_promoted += promoted
        total_deferred += deferred
        total_rejected += rejected

        print(f"\n  Session: {sid}")
        print(f"    Captures evaluated: {evaluated}")
        print(f"    Promoted: {promoted}  ·  Deferred: {deferred}  ·  Rejected: {rejected}")

        if entry_ids:
            print(f"    Staging entries created:")
            for eid in entry_ids:
                print(f"      - {eid}")

        if dry_run:
            evaluations = result.get("evaluations", [])
            for ev in evaluations:
                rec = ev.get("recommendation", "?")
                reason = ev.get("reason", "")
                idx = ev.get("capture_index", 0)
                print(f"    [{rec.upper()}] Capture {idx}: {reason}")

    print()
    if results:
        print(f"  Total: {total_evaluated} evaluated, {total_promoted} promoted, "
              f"{total_deferred} deferred, {total_rejected} rejected")
    print()


# ---------------------------------------------------------------------------
# Status subcommand
# ---------------------------------------------------------------------------

def cmd_status(cm) -> None:
    summary = cm.status_summary()

    print()
    print(f"  {cm.profile_name} — Context Health")
    print(DIVIDER)
    print("  Committed context:")
    for info in summary["committed"]:
        words = info["words"]
        mtime = info["last_modified"]
        if words:
            status_str = f"{words} words  ·  last modified {mtime}"
        else:
            status_str = "(empty)"
        print(f"    {info['file']:<22} {status_str}")

    counts = summary["staging"]
    total = sum(counts.values())
    print()
    print(f"  Staging ({total} total):")
    print(
        f"    {counts.get('unconfirmed', 0)} unconfirmed  ·  "
        f"{counts.get('approved', 0)} approved  ·  "
        f"{counts.get('rejected', 0)} rejected  ·  "
        f"{counts.get('deferred', 0)} deferred"
    )

    # Session count
    sessions_dir = cm._root / "data" / "sessions"
    if sessions_dir.exists():
        session_count = len(list(sessions_dir.glob("*.json")))
        print(f"\n  Sessions: {session_count} stored")

    # Domain roles
    domain_roles = cm.domain_roles
    if domain_roles:
        print(f"\n  Domain roles:")
        for role_name, role_config in domain_roles.items():
            trust = role_config.get("trust_weight", 0.5)
            auto = role_config.get("auto_promote", False)
            print(f"    {role_name}: trust={trust}, auto_promote={auto}")

    # Promotion criteria
    criteria = cm.promotion_criteria
    if criteria:
        print(f"\n  Promotion criteria:")
        for key, value in criteria.items():
            print(f"    {key}: {value}")

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def admin_main() -> None:
    parser = argparse.ArgumentParser(
        description="Committed-Context Agent — Admin",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  sc-admin status\n"
            "  sc-admin new-domain\n"
            "  sc-admin --data-dir ../deployments/decision-pack init decision-pack\n"
            "  sc-admin --data-dir ../deployments/minpaku init minpaku\n"
            "  sc-admin --data-dir ../deployments/super-landlord init super-landlord\n"
            "  sc-admin intake\n"
            "  sc-admin ingest bill.pdf\n"
            "  # Minpaku business actions normally happen in sc --role operator\n"
            "  sc-admin publish-minpaku\n"
            "  sc-admin update-minpaku\n"
            "  sc-admin unlist-minpaku\n"
            "  sc-admin review\n"
            "  sc-admin review --auto\n"
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Path to the project root (auto-detected by default)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # review
    review_parser = subparsers.add_parser("review", help="Review staging entries")
    review_parser.add_argument(
        "--auto",
        action="store_true",
        help="AI-powered auto-review: approve clean entries, defer conflicts",
    )

    # intake
    subparsers.add_parser("intake", help="Bootstrap context from AIOS source files (profile-driven)")

    # ingest
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a document into staging")
    ingest_parser.add_argument(
        "file",
        type=Path,
        help="Path to document file (.txt, .md, .pdf, .jpg, .png, .webp)",
    )

    # publish-minpaku
    publish_parser = subparsers.add_parser(
        "publish-minpaku",
        help="Compatibility helper: publish a Minpaku listing draft through the active domain extension",
    )
    publish_parser.add_argument(
        "entry_id",
        nargs="?",
        default=None,
        help="Optional staging entry ID. Defaults to the latest listing draft.",
    )

    # update-minpaku
    update_parser = subparsers.add_parser(
        "update-minpaku",
        help="Compatibility helper: update an existing published Minpaku listing using a listing draft",
    )
    update_parser.add_argument(
        "entry_id",
        nargs="?",
        default=None,
        help="Optional staging entry ID. Defaults to the latest listing draft.",
    )

    # unlist-minpaku
    delete_parser = subparsers.add_parser(
        "unlist-minpaku",
        help="Compatibility helper: delete an existing published Minpaku listing using a listing draft",
    )
    delete_parser.add_argument(
        "entry_id",
        nargs="?",
        default=None,
        help="Optional staging entry ID. Defaults to the latest listing draft.",
    )

    # init
    init_parser = subparsers.add_parser("init", help="Initialise deployment from a domain template")
    init_parser.add_argument(
        "profile",
        help="Domain name (e.g. minpaku, super-landlord)",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )

    # new-domain
    subparsers.add_parser(
        "new-domain",
        help="Scaffold a new domain template interactively",
    )

    # status
    subparsers.add_parser("status", help="Context health summary")

    # curate
    curate_parser = subparsers.add_parser(
        "curate",
        help="Curate session captures and promote worthy items to staging",
    )
    curate_parser.add_argument(
        "--session",
        type=str,
        default=None,
        help="Session ID to curate (default: all sessions)",
    )
    curate_parser.add_argument(
        "--all",
        action="store_true",
        help="Curate all sessions with captures",
    )
    curate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show recommendations without creating staging entries",
    )
    curate_parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run curator as background daemon",
    )
    curate_parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Daemon interval in minutes (default: 30)",
    )
    curate_parser.add_argument(
        "--once",
        action="store_true",
        help="Run once on schedule (non-daemon) and exit",
    )

    args = parser.parse_args()

    from simply_connect.context_manager import ContextManager

    cm = ContextManager(root=args.data_dir)
    load_dotenv(cm._root / ".env", override=True)

    if args.command == "review":
        cmd_review(cm, auto=args.auto)
    elif args.command == "intake":
        cmd_intake(cm)
    elif args.command == "ingest":
        cmd_ingest(cm, args.file)
    elif args.command == "publish-minpaku":
        cmd_publish_minpaku(cm, entry_id=args.entry_id)
    elif args.command == "update-minpaku":
        cmd_update_minpaku(cm, entry_id=args.entry_id)
    elif args.command == "unlist-minpaku":
        cmd_unlist_minpaku(cm, entry_id=args.entry_id)
    elif args.command == "init":
        cmd_init(args.profile, cm._root, force=args.force)
    elif args.command == "new-domain":
        cmd_new_domain(_resolve_domains_dir())
    elif args.command == "status":
        cmd_status(cm)
    elif args.command == "curate":
        from simply_connect.curator import schedule_curator

        result = schedule_curator(
            cm,
            interval_minutes=args.interval,
            dry_run=args.dry_run,
            run_once=not args.daemon,
        )
        if result["mode"] == "daemon":
            print(f"\n  Curator daemon started (interval: {args.interval} min)")
            print("  Press Ctrl+C to stop")
            import time
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n  Stopping daemon...")
        else:
            results = result.get("results", [])
            total_promoted = sum(r.get("promoted", 0) for r in results)
            total_deferred = sum(r.get("deferred", 0) for r in results)
            total_rejected = sum(r.get("rejected", 0) for r in results)
            print(f"\n  Curated {len(results)} session(s): {total_promoted} promoted, {total_deferred} deferred, {total_rejected} rejected")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    admin_main()
