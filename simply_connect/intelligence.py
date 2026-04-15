"""
simply-connect Document Intelligence — generic pipeline runner.

Schema-agnostic classify + extract engine. Domain schemas are injected by the
caller (via the get_document_schemas extension hook or directly). No save-my-brain
imports — this module has zero domain knowledge.

Pipeline:
  1. EYES: PyMuPDF (coordinate-aware) → Docling fallback (scanned PDFs / images)
  2. Phase A: Classify — doc_type, language, detected_names, currency
  3. Phase B: Extract structured JSON per doc_type schema

LLM backend (cheapest path first, default Anthropic):
  SC_LLM_BACKEND=anthropic  — Claude SDK (ANTHROPIC_API_KEY) or CLI subprocess
  SC_LLM_BACKEND=openai     — GPT-4o (OPENAI_API_KEY) [future]
  SC_LLM_BACKEND=gemini     — Gemini (GOOGLE_API_KEY) [future]

Within the Anthropic backend:
  ANTHROPIC_API_KEY set → SDK direct (~$0.0003/doc for Haiku)
  claude CLI in PATH    → OAuth subscription, no API key needed
  Neither               → local-only fallback, text stored, no AI analysis

The schemas dict passed to process_document() must contain:
  classify_schema:           str   — Phase A JSON schema template (with doc_type taxonomy)
  extraction_schemas:        dict  — Phase B: doc_type → JSON schema template string
  default_extraction_schema: str   — fallback schema for unknown doc types
  complex_doc_types:         set   — these types get sonnet_model instead of haiku_model
  haiku_model:               str   — fast/cheap model identifier
  sonnet_model:              str   — reasoning model identifier
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from .backends import LLMBackend, get_backend

log = logging.getLogger(__name__)

# Minimal fallback schema for deployments that don't provide one
DEFAULT_GENERIC_SCHEMA = """{
  "summary": "2-3 sentence summary",
  "key_points": ["point 1", "point 2"],
  "important_dates": [],
  "red_flags": [],
  "action_items": []
}"""

DEFAULT_CLASSIFY_SCHEMA = """{
  "doc_type": "receipt|bank_statement|credit_card|insurance|medical|legal|contract|utility|id_document|tax|travel|hotel|event|school|other",
  "detected_names": [],
  "document_language": "en|zh|ja|other",
  "complexity": "simple|complex",
  "brief_description": "One-line description",
  "currency": "HKD|USD|GBP|JPY|EUR|null"
}
Return ONLY the JSON, no markdown fences, no explanation."""


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _parse_json(raw: str) -> dict:
    try:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        log.warning(f"Could not parse LLM JSON response. Raw: {raw[:300]}")
        return {}


# ---------------------------------------------------------------------------
# Phase A — Classify
# ---------------------------------------------------------------------------

def classify_text(text: str, classify_schema: str, backend: LLMBackend) -> dict:
    """Classify a document from its extracted text."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    system = f"""You are a document classifier.
Today's date is {today}.
Classify this document and detect any person names mentioned.
Return ONLY this JSON:
{classify_schema}"""

    try:
        raw = backend.complete(system, f"Classify this document:\n\n{text[:3000]}",
                               model="claude-haiku-4-5")
        result = _parse_json(raw)
    except Exception as e:
        log.exception(f"classify_text failed: {e}")
        return _fallback_classification(text)

    return _fill_classification_defaults(result)


def classify_image(
    file_bytes: bytes,
    mime_type: str,
    classify_schema: str,
    backend: LLMBackend,
    text_hint: str = "",
) -> dict:
    """Classify from the raw image.

    Vision path: backend.complete_vision() — best results.
    Text-hint fallback: when backend.supports_vision() is False, classifies
    from filename/caption only.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    system = f"""You are a document classifier.
Today's date is {today}.
Classify this document and detect any person names mentioned.
Return ONLY this JSON:
{classify_schema}"""

    if backend.supports_vision():
        try:
            raw = backend.complete_vision(
                system, file_bytes, mime_type,
                "Classify this document image. Detect any person names.",
                model="claude-haiku-4-5",
                max_tokens=512,
            )
            result = _parse_json(raw)
        except Exception as e:
            log.exception(f"classify_image (vision) failed: {e}")
            return _fallback_classification(text_hint)
        return _fill_classification_defaults(result)

    else:
        # No vision — use text hint (filename / caption)
        hint = (text_hint or "").strip() or "unknown document"
        log.info(f"classify_image: backend {backend.name()!r} has no vision, using text hint")
        try:
            raw = backend.complete(
                system,
                f"Classify this document based on its filename/caption: {hint}\n"
                f"(Image content unavailable — classify from filename only.)",
                model="claude-haiku-4-5",
            )
            result = _parse_json(raw)
        except Exception as e:
            log.exception(f"classify_image (text hint) failed: {e}")
            return _fallback_classification(text_hint)
        return _fill_classification_defaults(result)


def _fill_classification_defaults(result: dict) -> dict:
    result.setdefault("doc_type", "other")
    result.setdefault("detected_names", [])
    result.setdefault("document_language", "en")
    result.setdefault("complexity", "simple")
    result.setdefault("brief_description", "")
    result.setdefault("currency", None)
    return result


def _fallback_classification(text: str) -> dict:
    """Keyword heuristic fallback when no LLM access."""
    text_lower = (text or "").lower()
    doc_type = "other"
    if any(kw in text_lower for kw in ("receipt", "收據", "total", "subtotal")):
        doc_type = "receipt"
    elif any(kw in text_lower for kw in ("insurance", "policy", "premium", "保單")):
        doc_type = "insurance"
    elif any(kw in text_lower for kw in ("statement", "balance", "帳戶")):
        doc_type = "bank_statement"
    elif any(kw in text_lower for kw in ("clinic", "doctor", "diagnosis", "醫生", "dental")):
        doc_type = "medical"
    return _fill_classification_defaults({"doc_type": doc_type})


# ---------------------------------------------------------------------------
# Phase B — Extract
# ---------------------------------------------------------------------------

def extract_text_mode(
    text: str,
    doc_type: str,
    extraction_schema: str,
    model: str,
    backend: LLMBackend,
    user_language: str = "en",
) -> dict:
    """Extract structured data from text using a domain-provided schema."""
    today = datetime.utcnow().strftime("%Y-%m-%d")

    lang_instruction = {
        "en":    "Write summary, key_points, and action_items in English.",
        "zh-tw": "請以繁體中文撰寫 summary、key_points 和 action_items。",
        "zh":    "請以繁體中文撰寫 summary、key_points 和 action_items。",
        "ja":    "summary、key_points、action_items は日本語で記述してください。",
    }.get(user_language, "Write in English.")

    system = f"""You are a document intelligence AI.
Today's date is {today}.
{lang_instruction}

This document has been classified as: {doc_type}

Extract structured information. Return ONLY this JSON (no markdown, no explanation):
{extraction_schema}

IMPORTANT DATES — calculate days_until from today ({today}). Use -1 if past date.
SUMMARY — plain text only, no markdown, no headers, no tables, no bullet points.
NOTE: The text may be poorly extracted from PDF (columns scrambled, numbers separated from labels). Do your best to reconstruct transactions from the numbers present. If a merchant name is missing, use "Unknown".
HISTORICAL STATEMENTS — users often upload old statements for record-keeping. Do NOT flag past due dates as overdue emergencies. If the payment due date is in the past, assume it was already paid. Only flag genuinely suspicious items.
CATEGORISATION — base category strictly on the merchant name, not assumptions."""

    is_large = doc_type in ("bank_statement", "credit_card")
    max_tokens = 8192 if is_large else 4096
    # Larger context window when SDK available (AnthropicBackend with API key)
    max_chars = 150_000 if backend.supports_vision() else 8_000
    user_content = f"Document content:\n\n{(text or '')[:max_chars]}"

    try:
        raw = backend.complete(system, user_content, model=model, max_tokens=max_tokens)
        result = _parse_json(raw)
    except Exception as e:
        log.exception(f"extract_text_mode failed: {e}")
        char_count = len(text or "")
        return {
            "summary": f"Large document stored ({char_count:,} chars). Ask me to summarize it.",
            "key_points": ["Full text stored — ask any question about this document."],
            "important_dates": [],
            "red_flags": [],
            "action_items": [],
        }

    return _fill_extraction_defaults(result)


def extract_vision_mode(
    file_bytes: bytes,
    mime_type: str,
    doc_type: str,
    extraction_schema: str,
    model: str,
    backend: LLMBackend,
    user_language: str = "en",
) -> dict:
    """Extract structured data from raw image via backend vision.

    Falls back to empty extraction if backend does not support vision.
    """
    if not backend.supports_vision():
        log.info(f"extract_vision_mode: backend {backend.name()!r} has no vision, skipping")
        return _empty_extraction()

    today = datetime.utcnow().strftime("%Y-%m-%d")

    lang_instruction = {
        "en":    "Write summary, key_points, and action_items in English.",
        "zh-tw": "請以繁體中文撰寫 summary、key_points 和 action_items。",
        "zh":    "請以繁體中文撰寫 summary、key_points 和 action_items。",
        "ja":    "summary、key_points、action_items は日本語で記述してください。",
    }.get(user_language, "Write in English.")

    system = f"""You are a document intelligence AI.
Today's date is {today}.
{lang_instruction}

This document has been classified as: {doc_type}

Extract structured information. Return ONLY this JSON (no markdown, no explanation):
{extraction_schema}

IMPORTANT DATES — calculate days_until from today ({today}). Use -1 if past date.
SUMMARY — plain text only, no markdown, no headers, no tables, no bullet points.
HISTORICAL STATEMENTS — do NOT flag past due dates as overdue emergencies.
CATEGORISATION — base category strictly on the merchant name."""

    max_tokens = 8192 if doc_type in ("bank_statement", "credit_card") else 4096

    try:
        raw = backend.complete_vision(
            system, file_bytes, mime_type,
            "Extract structured information from this document.",
            model=model,
            max_tokens=max_tokens,
        )
        result = _parse_json(raw)
    except Exception as e:
        log.exception(f"extract_vision_mode failed: {e}")
        return _empty_extraction()

    return _fill_extraction_defaults(result)


def _fill_extraction_defaults(result: dict) -> dict:
    result.setdefault("summary", "")
    result.setdefault("key_points", [])
    result.setdefault("important_dates", [])
    result.setdefault("red_flags", [])
    result.setdefault("action_items", [])
    return result


def _empty_extraction() -> dict:
    return _fill_extraction_defaults({})


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_document(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    schemas: dict,
    user_language: str = "en",
    force_vision: bool = False,
    backend: LLMBackend | None = None,
) -> dict[str, Any]:
    """
    Full hybrid document intelligence pipeline.

    Args:
        file_bytes:    Raw bytes of the uploaded file.
        filename:      Original filename (used for mime detection fallback).
        mime_type:     MIME type string (e.g. "application/pdf", "image/jpeg").
        schemas:       Domain schemas dict — must contain:
                         classify_schema, extraction_schemas, default_extraction_schema,
                         complex_doc_types, haiku_model, sonnet_model
        user_language: "en", "zh-tw", "zh", "ja" — controls summary language.
        force_vision:  Skip EYES and go straight to vision path.
        backend:       LLMBackend instance. If None, reads SC_LLM_BACKEND env var
                       (default: AnthropicBackend).

    Returns dict with:
        doc_type, summary, key_points, important_dates, red_flags, action_items,
        detected_names, currency, document_language, classification,
        extracted_text, _extraction_method, _eyes_method, _claude_access
    """
    from . import eyes

    if backend is None:
        backend = get_backend()

    classify_schema = schemas.get("classify_schema", DEFAULT_CLASSIFY_SCHEMA)
    extraction_schemas = schemas.get("extraction_schemas", {})
    default_schema = schemas.get("default_extraction_schema", DEFAULT_GENERIC_SCHEMA)
    complex_types = schemas.get("complex_doc_types", set())
    haiku = schemas.get("haiku_model", "claude-haiku-4-5")
    sonnet = schemas.get("sonnet_model", "claude-haiku-4-5")  # fallback to haiku if not set

    # Local-only fallback: backend not available at all
    if not backend.is_available():
        try:
            eyes_result = eyes.extract_text(file_bytes, mime_type, filename)
            extracted = eyes_result.text.strip()
        except Exception as e:
            extracted = ""
            log.warning(f"EYES fallback failed: {e}")
        char_count = len(extracted)
        if char_count > 0:
            summary = (
                f"Document stored. {char_count:,} characters of text extracted — "
                f"ask me anything about it and I'll read it for you."
            )
            key_points = [
                f"Full text available ({char_count:,} chars) — ready for questions.",
                f"Set SC_LLM_BACKEND credentials to enable automatic classification.",
            ]
        else:
            summary = "Document stored. No text could be extracted — it may be a scanned image."
            key_points = [
                "No text extracted — may be a scanned or image-only document.",
                "Set SC_LLM_BACKEND credentials to enable Vision analysis.",
            ]
        return {
            "doc_type": "other",
            "summary": summary,
            "extracted_text": extracted,
            "key_points": key_points,
            "important_dates": [],
            "red_flags": [],
            "action_items": [],
            "transactions": [],
            "detected_names": [],
            "currency": None,
            "_extraction_method": "local_eyes_only",
            "_eyes_method": "eyes",
            "_claude_access": "none",
        }

    # Step 1: EYES
    eyes_result = eyes.extract_text(file_bytes, mime_type, filename)
    log.info(
        f"EYES: method={eyes_result.method} "
        f"text_len={len(eyes_result.text)} "
        f"scanned={eyes_result.is_scanned}"
    )
    use_text_mode = eyes.has_enough_text(eyes_result) and not force_vision
    extraction_method = "text" if use_text_mode else "vision"
    access = backend.name()
    if hasattr(backend, "_has_api_key"):
        access = f"{backend.name()}:sdk" if backend._has_api_key() else f"{backend.name()}:cli"
    log.info(f"Backend: {access} | text_mode={use_text_mode}")

    # Step 2: Classify
    if use_text_mode:
        classification = classify_text(eyes_result.text, classify_schema, backend)
    else:
        classification = classify_image(
            file_bytes, mime_type, classify_schema, backend, text_hint=filename
        )
    doc_type = classification.get("doc_type", "other")
    log.info(f"Classified as: {doc_type} (method={extraction_method}, backend={access})")

    # Step 3: Extract
    extraction_schema = extraction_schemas.get(doc_type, default_schema)
    model = sonnet if doc_type in complex_types else haiku

    if use_text_mode:
        extraction = extract_text_mode(
            eyes_result.text, doc_type, extraction_schema, model, backend, user_language
        )
    else:
        extraction = extract_vision_mode(
            file_bytes, mime_type, doc_type, extraction_schema, model, backend, user_language
        )

    # Merge classification into extraction result
    extraction["doc_type"] = doc_type
    extraction["detected_names"] = classification.get("detected_names", [])
    extraction["document_language"] = classification.get("document_language", "en")
    extraction["currency"] = classification.get("currency")
    extraction["classification"] = classification
    extraction["extracted_text"] = eyes_result.text
    extraction["_extraction_method"] = extraction_method
    extraction["_eyes_method"] = eyes_result.method
    extraction["_claude_access"] = access

    return extraction
