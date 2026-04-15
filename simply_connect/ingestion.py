"""
Committed-Context Agent Framework — Document Ingestion

Parses documents into staging-ready extractions using Claude or Docling.
Called by `sc-admin ingest`, the `ingest_document` MCP tool, and the Telegram relay.

Supported formats:
  .txt, .md           — read as text directly
  .pdf                — EYES (PyMuPDF coordinate-aware → Docling fallback), vision fallback
  .jpg, .jpeg, .png,
  .webp, .gif         — EYES (Docling OCR) → Claude vision fallback

Parser selection via SC_DOCUMENT_PARSER env var:
  claude   (default) — Anthropic vision API; requires ANTHROPIC_API_KEY
  docling             — local parsing via Docling; no API key needed for extraction

Intelligence pipeline (activated when extension provides get_document_schemas):
  When a ContextManager is passed and the active extension exposes get_document_schemas(),
  the full classify → extract pipeline runs instead of the generic summarisation prompt.
  Returns a typed extraction (doc_type, transactions, policy, etc.) as staging entries.

Returns a list of extractions, each with summary, content, and category,
ready to be written as staging entries.
"""

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True), override=False)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MIME helpers
# ---------------------------------------------------------------------------

def _suffix_to_mime(suffix: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
        ".txt": "text/plain", ".md": "text/plain",
    }.get(suffix, "application/octet-stream")


# ---------------------------------------------------------------------------
# Claude call helpers
# ---------------------------------------------------------------------------

def _call_text_prompt(prompt: str, api_key: str) -> str:
    """Call Claude with a plain-text prompt.

    Uses the Anthropic SDK when an API key is available; falls back to
    `claude --print` subprocess (Claude Code OAuth) when it is not.
    """
    if api_key:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1024,
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


def _call_claude_vision(filepath: Path, api_key: str, prompt: str) -> str:
    """Call Claude vision API for image or image-based PDF.

    Always requires ANTHROPIC_API_KEY — vision is multimodal SDK only.
    """
    if not api_key:
        raise RuntimeError(
            "Claude vision requires ANTHROPIC_API_KEY.\n"
            "  Set ANTHROPIC_API_KEY in .env, or use SC_DOCUMENT_PARSER=docling for local parsing."
        )

    import anthropic
    suffix = filepath.suffix.lower()
    file_bytes = filepath.read_bytes()
    image_data = base64.standard_b64encode(file_bytes).decode()

    if suffix == ".pdf":
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": image_data},
        }
    else:
        media_type_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
        }
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type_map.get(suffix, "image/jpeg"), "data": image_data},
        }

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": [content_block, {"type": "text", "text": prompt}]}],
    )
    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------

def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Docling parser (explicit SC_DOCUMENT_PARSER=docling path)
# ---------------------------------------------------------------------------

def _parse_with_docling(filepath: Path) -> str:
    """Convert a document to markdown text using Docling (local, no API key needed).

    Handles PDFs (text and image-based), images, Word docs, and more.
    Install with: pip install -e '.[docling]'
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        raise RuntimeError(
            "Docling is not installed.\n"
            "  Install with: pip install -e '.[docling]'\n"
            "  Or switch to Claude vision: SC_DOCUMENT_PARSER=claude"
        )
    log.info(f"Docling parsing: {filepath.name}")
    result = DocumentConverter().convert(str(filepath))
    return result.document.export_to_markdown()


# ---------------------------------------------------------------------------
# Document-to-text router (generic path, no intelligence pipeline)
# ---------------------------------------------------------------------------

def _parse_document_to_text(filepath: Path, api_key: str, parser: str) -> str | None:
    """
    Extract document content as plain text.

    Returns:
        str  — extracted text (use _call_text_prompt for categorisation)
        None — format requires direct Claude vision handling (claude parser, image/image-PDF)

    The Docling path always returns text — it handles all formats locally.
    The claude path uses EYES for PDF/images, falling back to vision when text is too short.
    """
    suffix = filepath.suffix.lower()

    if parser == "docling":
        return _parse_with_docling(filepath)

    # Claude path — text formats are handled directly
    if suffix in (".txt", ".md"):
        return _read_text_file(filepath)

    # For PDF and images: use EYES (coordinate-aware PyMuPDF + Docling OCR)
    if suffix in (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif"):
        try:
            from . import eyes as _eyes
            mime = _suffix_to_mime(suffix)
            file_bytes = filepath.read_bytes()
            eyes_result = _eyes.extract_text(file_bytes, mime, filepath.name)
            if eyes_result.text.strip():
                log.info(f"EYES extraction: method={eyes_result.method} len={len(eyes_result.text)}")
                return eyes_result.text
        except Exception as e:
            log.warning(f"EYES extraction failed, falling back to vision: {e}")
        # EYES got nothing → vision fallback
        return None

    return None


# ---------------------------------------------------------------------------
# Extraction prompt (generic path)
# ---------------------------------------------------------------------------

def _build_extraction_prompt(content: str, profile: dict, committed_context: dict[str, str]) -> str:
    """Build the Claude prompt for extracting staging entries from document content."""
    profile_name = profile.get("name", "assistant")
    category_map = profile.get("category_map", {})
    categories = [k for k in category_map if k != "general"]

    committed_summary = "\n".join(
        f"  {stem}: {len(text.split())} words"
        for stem, text in committed_context.items()
        if text and text.strip()
    ) or "  (no committed context yet)"

    return f"""You are extracting structured information from a document for a {profile_name} assistant.

The assistant uses a committed-context architecture with these categories:
{chr(10).join(f'  - {c}' for c in categories)}

Existing committed context (word counts — for deduplication):
{committed_summary}

Document content:
---
{content[:6000]}
---

Extract the information from this document as one or more context updates.
For each distinct piece of information worth capturing, return a JSON object.
Return a JSON array of extractions:

[
  {{
    "summary": "one-line description of what this captures (max 80 chars)",
    "content": "the extracted content, clean and structured",
    "category": "one of: {', '.join(categories + ['general'])}"
  }}
]

Domain-specific extraction rules:
- Utility bills / invoices: extract billing period, total amount, service address, account number, due date
- Contracts / agreements: extract parties, key obligations, dates, payment terms
- Property documents: extract address, unit details, tenancy terms, rates
- General documents: extract factual, specific, reusable information only

Do NOT extract:
- Template placeholder text or instructions
- Procedural descriptions (how to use the system)
- Vague or subjective statements

If nothing useful can be extracted, return an empty array: []
Return ONLY the JSON array — no other text, no markdown fences.
"""


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> list[dict]:
    """Parse JSON array from Claude response, stripping any markdown fences."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines).strip()
    result = json.loads(text)
    return result if isinstance(result, list) else []


# ---------------------------------------------------------------------------
# Intelligence pipeline result → staging entries
# ---------------------------------------------------------------------------

def _intelligence_result_to_staging(result: dict, profile: dict) -> list[dict]:
    """Convert a typed intelligence result into staging-entry format."""
    doc_type = result.get("doc_type", "other")
    summary = result.get("summary", "")
    category_map = profile.get("category_map", {})

    content_lines = []
    if summary:
        content_lines.append(summary)
    for pt in result.get("key_points", []):
        content_lines.append(f"• {pt}")
    for d in result.get("important_dates", []):
        content_lines.append(f"📅 {d.get('label', '')}: {d.get('date', '')}")
    for flag in result.get("red_flags", []):
        content_lines.append(f"⚠️ {flag.get('clause', '')}: {flag.get('detail', '')}")
    for item in result.get("action_items", []):
        content_lines.append(f"→ {item}")

    content = "\n".join(content_lines) or result.get("extracted_text", "")

    # Map doc_type to a profile category
    _type_to_category = {
        "receipt": "finances", "credit_card": "finances", "bank_statement": "finances",
        "insurance": "insurance", "medical": "health", "legal": "documents",
        "contract": "documents", "utility": "finances", "tax": "finances",
        "id_document": "documents", "travel": "travel", "hotel": "travel",
        "school": "family", "event": "general", "mortgage": "documents",
    }
    category = _type_to_category.get(doc_type, "general")
    if category not in category_map:
        category = "general"

    entry_summary = (summary[:80] if summary else f"Document stored ({doc_type})")
    return [{"summary": entry_summary, "content": content, "category": category}]


# ---------------------------------------------------------------------------
# Main ingest function
# ---------------------------------------------------------------------------

def ingest_document(
    filepath: Path,
    committed_context: dict[str, str],
    profile: dict,
    parser: str | None = None,
    cm=None,
) -> dict[str, Any]:
    """
    Ingest a document file and return structured extraction results.

    Args:
        filepath:          Path to the document file.
        committed_context: Current committed context dict (for deduplication hints).
        profile:           Profile dict loaded from profile.json.
        parser:            "claude" or "docling". Defaults to SC_DOCUMENT_PARSER env var.
        cm:                Optional ContextManager. When provided and the active extension
                           exposes get_document_schemas(), the full intelligence pipeline
                           runs (typed Phase A+B extraction) instead of the generic prompt.

    Returns:
        {
            "success": bool,
            "extractions": [{"summary": str, "content": str, "category": str}, ...],
            "error": str | None,
            "file": str,
            "format": str,
            "parser": str,
            "doc_type": str | None,    # present when intelligence pipeline ran
        }
    """
    if parser is None:
        parser = os.getenv("SC_DOCUMENT_PARSER", "claude")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    suffix = filepath.suffix.lower()

    supported = {".txt", ".md", ".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
    if suffix not in supported:
        return {
            "success": False,
            "extractions": [],
            "error": f"Unsupported file format: '{suffix}'. Supported: {', '.join(sorted(supported))}",
            "file": str(filepath),
            "format": suffix,
            "parser": parser,
        }

    try:
        # --- Intelligence pipeline path (when extension provides schemas) ---
        if cm is not None:
            try:
                from .ext_loader import get_document_schemas
                from . import intelligence
                schemas = get_document_schemas(cm)
                if schemas is not None:
                    file_bytes = filepath.read_bytes()
                    mime_type = _suffix_to_mime(suffix)
                    force_vision = os.getenv("SC_FORCE_VISION", "").strip() in ("1", "true", "yes")
                    result = intelligence.process_document(
                        file_bytes, filepath.name, mime_type, schemas,
                        user_language="en", force_vision=force_vision,
                    )
                    extractions = _intelligence_result_to_staging(result, profile)
                    return {
                        "success": True,
                        "extractions": extractions,
                        "error": None,
                        "file": str(filepath),
                        "format": suffix,
                        "parser": "intelligence",
                        "doc_type": result.get("doc_type"),
                    }
            except Exception as e:
                log.warning(f"Intelligence pipeline failed, falling back to generic: {e}")
                # fall through to generic path below

        # --- Generic path ---
        text_content = _parse_document_to_text(filepath, api_key, parser)

        if text_content is not None:
            # Text path — works for Docling (all formats) and Claude (text PDFs, .txt, .md)
            if not text_content.strip():
                return _empty("No text content extracted", filepath, suffix, parser)
            prompt = _build_extraction_prompt(text_content, profile, committed_context)
            raw = _call_text_prompt(prompt, api_key)
            extractions = _parse_response(raw)
        else:
            # Vision path — Claude only (image files and image-based PDFs)
            # Docling always returns text so never reaches here
            vision_hint = "(see attached image)" if suffix != ".pdf" else "(see attached PDF — image-based)"
            prompt = _build_extraction_prompt(vision_hint, profile, committed_context)
            raw = _call_claude_vision(filepath, api_key, prompt)
            extractions = _parse_response(raw)

        return {
            "success": True,
            "extractions": extractions,
            "error": None,
            "file": str(filepath),
            "format": suffix,
            "parser": parser,
        }

    except Exception as e:
        log.exception(f"ingest_document failed for {filepath}")
        return {
            "success": False,
            "extractions": [],
            "error": str(e),
            "file": str(filepath),
            "format": suffix,
            "parser": parser,
        }


def _empty(reason: str, filepath: Path, suffix: str, parser: str = "claude") -> dict[str, Any]:
    return {
        "success": True,
        "extractions": [],
        "error": None,
        "file": str(filepath),
        "format": suffix,
        "parser": parser,
        "note": reason,
    }
