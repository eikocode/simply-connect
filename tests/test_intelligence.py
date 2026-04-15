"""
Tests for simply_connect/intelligence.py

Covers:
  _parse_json()           — clean JSON, fenced JSON, malformed
  classify_text()         — successful classification, fallback on error
  classify_image()        — vision path, text-hint fallback (no vision backend)
  extract_text_mode()     — successful extraction, large-doc fallback on error
  extract_vision_mode()   — vision path, no-vision fallback
  process_document()      — text mode, vision mode (force_vision), local-only
                            fallback, injected backend, result shape

No live API calls — backend is a MagicMock in all tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module loader (bypass Python 3.9 incompatible __init__.py)
# ---------------------------------------------------------------------------

def _setup_pkg() -> None:
    """Register a stub simply_connect package so relative imports work."""
    if "simply_connect" not in sys.modules or not hasattr(
        sys.modules["simply_connect"], "__path__"
    ):
        from pathlib import Path
        pkg = types.ModuleType("simply_connect")
        pkg.__path__ = [str(Path(__file__).parent.parent / "simply_connect")]
        pkg.__package__ = "simply_connect"
        sys.modules["simply_connect"] = pkg


def _load_direct(full_name: str):
    """Load a simply_connect sub-module directly from its .py file.

    No recursion — caller is responsible for loading dependencies first.
    """
    if full_name in sys.modules:
        return sys.modules[full_name]
    _setup_pkg()
    from pathlib import Path
    filename = full_name.split(".")[-1] + ".py"
    path = Path(__file__).parent.parent / "simply_connect" / filename
    spec = importlib.util.spec_from_file_location(full_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load in dependency order: backends first, then intelligence
_bk    = _load_direct("simply_connect.backends")
_intel = _load_direct("simply_connect.intelligence")

classify_text      = _intel.classify_text
classify_image     = _intel.classify_image
extract_text_mode  = _intel.extract_text_mode
extract_vision_mode = _intel.extract_vision_mode
process_document   = _intel.process_document
_parse_json        = _intel._parse_json


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_SCHEMAS = {
    "classify_schema":           '{"doc_type": "receipt|other"}',
    "extraction_schemas":        {"receipt": '{"summary": "", "transactions": []}'},
    "default_extraction_schema": '{"summary": "", "key_points": []}',
    "complex_doc_types":         {"bank_statement", "credit_card"},
    "haiku_model":               "claude-haiku-4-5",
    "sonnet_model":              "claude-sonnet-4-5",
}

CLASSIFY_RESULT = json.dumps({
    "doc_type": "receipt",
    "detected_names": ["Alice"],
    "document_language": "en",
    "complexity": "simple",
    "brief_description": "Coffee shop receipt",
    "currency": "HKD",
})

EXTRACT_RESULT = json.dumps({
    "summary": "Coffee at Starbucks HKD 45",
    "key_points": ["Paid HKD 45"],
    "important_dates": [],
    "red_flags": [],
    "action_items": [],
    "transactions": [{"merchant": "Starbucks", "amount": 45, "currency": "HKD"}],
})


def make_backend(*, has_vision: bool = True, is_available: bool = True) -> MagicMock:
    b = MagicMock(spec=_bk.LLMBackend)
    b.name.return_value = "mock"
    b.is_available.return_value = is_available
    b.supports_vision.return_value = has_vision
    b.complete.return_value = CLASSIFY_RESULT
    b.complete_vision.return_value = CLASSIFY_RESULT
    return b


# ---------------------------------------------------------------------------
# _parse_json
# ---------------------------------------------------------------------------

class TestParseJson:
    def test_clean_json(self):
        result = _parse_json('{"doc_type": "receipt"}')
        assert result == {"doc_type": "receipt"}

    def test_fenced_json(self):
        result = _parse_json("```json\n{\"doc_type\": \"receipt\"}\n```")
        assert result == {"doc_type": "receipt"}

    def test_fenced_json_no_language(self):
        result = _parse_json("```\n{\"key\": \"value\"}\n```")
        assert result == {"key": "value"}

    def test_malformed_returns_empty_dict(self):
        result = _parse_json("not valid json at all")
        assert result == {}

    def test_empty_string_returns_empty_dict(self):
        result = _parse_json("")
        assert result == {}


# ---------------------------------------------------------------------------
# classify_text
# ---------------------------------------------------------------------------

class TestClassifyText:
    def test_returns_classification_dict(self):
        b = make_backend()
        b.complete.return_value = CLASSIFY_RESULT
        result = classify_text("This is a Starbucks receipt.", '{"doc_type": "receipt|other"}', b)
        assert result["doc_type"] == "receipt"
        assert result["detected_names"] == ["Alice"]
        assert result["currency"] == "HKD"

    def test_calls_backend_complete(self):
        b = make_backend()
        b.complete.return_value = CLASSIFY_RESULT
        classify_text("some text", '{"doc_type": "other"}', b)
        b.complete.assert_called_once()

    def test_truncates_long_text_to_3000_chars(self):
        b = make_backend()
        b.complete.return_value = CLASSIFY_RESULT
        long_text = "x" * 10_000
        classify_text(long_text, '{"doc_type": "other"}', b)
        user_arg = b.complete.call_args[0][1]  # second positional arg = user_text
        assert len(user_arg) < 4000

    def test_falls_back_on_backend_error(self):
        b = make_backend()
        b.complete.side_effect = RuntimeError("API down")
        result = classify_text("insurance policy document", '{"doc_type": "other"}', b)
        # Fallback classification should still return a dict with doc_type
        assert "doc_type" in result
        assert result["doc_type"] == "insurance"

    def test_fills_defaults_on_partial_response(self):
        b = make_backend()
        b.complete.return_value = json.dumps({"doc_type": "medical"})
        result = classify_text("doctor visit receipt", '{}', b)
        assert result["detected_names"] == []
        assert result["document_language"] == "en"
        assert result["complexity"] == "simple"


# ---------------------------------------------------------------------------
# classify_image
# ---------------------------------------------------------------------------

class TestClassifyImage:
    def test_vision_path_calls_complete_vision(self):
        b = make_backend(has_vision=True)
        b.complete_vision.return_value = CLASSIFY_RESULT
        result = classify_image(b"img-bytes", "image/jpeg", '{"doc_type": "other"}', b)
        b.complete_vision.assert_called_once()
        b.complete.assert_not_called()
        assert result["doc_type"] == "receipt"

    def test_no_vision_falls_back_to_text_hint(self):
        b = make_backend(has_vision=False)
        b.complete.return_value = CLASSIFY_RESULT
        result = classify_image(
            b"img-bytes", "image/jpeg", '{"doc_type": "other"}', b,
            text_hint="starbucks_receipt.jpg"
        )
        b.complete.assert_called_once()
        b.complete_vision.assert_not_called()
        # Text hint should appear in the user prompt
        user_arg = b.complete.call_args[0][1]
        assert "starbucks_receipt.jpg" in user_arg

    def test_vision_error_falls_back_to_heuristic(self):
        b = make_backend(has_vision=True)
        b.complete_vision.side_effect = RuntimeError("vision failed")
        result = classify_image(b"img-bytes", "image/jpeg", '{"doc_type": "other"}', b,
                                text_hint="bank statement")
        assert result["doc_type"] == "bank_statement"

    def test_no_vision_no_text_hint_uses_unknown(self):
        b = make_backend(has_vision=False)
        b.complete.return_value = json.dumps({"doc_type": "other"})
        classify_image(b"img-bytes", "image/jpeg", '{"doc_type": "other"}', b, text_hint="")
        user_arg = b.complete.call_args[0][1]
        assert "unknown document" in user_arg


# ---------------------------------------------------------------------------
# extract_text_mode
# ---------------------------------------------------------------------------

class TestExtractTextMode:
    def test_returns_extraction_dict(self):
        b = make_backend()
        b.complete.return_value = EXTRACT_RESULT
        result = extract_text_mode(
            "Starbucks HKD 45", "receipt", '{"summary": ""}', "claude-haiku-4-5", b
        )
        assert result["summary"] == "Coffee at Starbucks HKD 45"
        assert result["transactions"][0]["merchant"] == "Starbucks"

    def test_calls_backend_complete_with_model(self):
        b = make_backend()
        b.complete.return_value = EXTRACT_RESULT
        extract_text_mode("text", "receipt", '{}', "claude-sonnet-4-5", b)
        call_kwargs = b.complete.call_args
        assert call_kwargs.kwargs.get("model") == "claude-sonnet-4-5" or \
               call_kwargs[1].get("model") == "claude-sonnet-4-5" or \
               "claude-sonnet-4-5" in str(call_kwargs)

    def test_large_doc_fallback_on_error(self):
        b = make_backend()
        b.complete.side_effect = RuntimeError("timeout")
        result = extract_text_mode("x" * 500, "receipt", '{}', "claude-haiku-4-5", b)
        assert "stored" in result["summary"].lower() or "ask" in result["summary"].lower()
        assert result["key_points"] != []

    def test_fills_defaults_on_partial_response(self):
        b = make_backend()
        b.complete.return_value = json.dumps({"summary": "A document"})
        result = extract_text_mode("text", "other", '{}', "claude-haiku-4-5", b)
        assert result["key_points"] == []
        assert result["important_dates"] == []
        assert result["red_flags"] == []
        assert result["action_items"] == []

    def test_lang_instruction_zh_tw(self):
        b = make_backend()
        b.complete.return_value = EXTRACT_RESULT
        extract_text_mode("text", "receipt", '{}', "claude-haiku-4-5", b, user_language="zh-tw")
        system_arg = b.complete.call_args[0][0]
        assert "繁體中文" in system_arg


# ---------------------------------------------------------------------------
# extract_vision_mode
# ---------------------------------------------------------------------------

class TestExtractVisionMode:
    def test_vision_path_calls_complete_vision(self):
        b = make_backend(has_vision=True)
        b.complete_vision.return_value = EXTRACT_RESULT
        result = extract_vision_mode(
            b"img-bytes", "image/jpeg", "receipt", '{"summary": ""}', "claude-haiku-4-5", b
        )
        b.complete_vision.assert_called_once()
        assert result["summary"] == "Coffee at Starbucks HKD 45"

    def test_no_vision_returns_empty_extraction(self):
        b = make_backend(has_vision=False)
        result = extract_vision_mode(
            b"img-bytes", "image/jpeg", "receipt", '{}', "claude-haiku-4-5", b
        )
        b.complete_vision.assert_not_called()
        assert result["summary"] == ""
        assert result["key_points"] == []

    def test_vision_error_returns_empty_extraction(self):
        b = make_backend(has_vision=True)
        b.complete_vision.side_effect = RuntimeError("vision error")
        result = extract_vision_mode(
            b"img-bytes", "image/jpeg", "receipt", '{}', "claude-haiku-4-5", b
        )
        assert result["summary"] == ""

    def test_large_doc_gets_8192_max_tokens(self):
        b = make_backend(has_vision=True)
        b.complete_vision.return_value = EXTRACT_RESULT
        extract_vision_mode(b"bytes", "image/jpeg", "bank_statement", '{}', "model", b)
        call_kwargs = b.complete_vision.call_args
        max_tok = call_kwargs.kwargs.get("max_tokens") or call_kwargs[1].get("max_tokens")
        assert max_tok == 8192


# ---------------------------------------------------------------------------
# process_document — full pipeline
# ---------------------------------------------------------------------------

class TestProcessDocument:
    def _make_eyes_result(self, text: str = "Starbucks HKD 45", method: str = "pymupdf",
                          is_scanned: bool = False):
        r = MagicMock()
        r.text = text
        r.method = method
        r.is_scanned = is_scanned
        return r

    def test_text_mode_pipeline(self):
        """EYES extracts enough text → text mode classify + extract."""
        b = make_backend(has_vision=True)
        # classify call returns classification JSON, extract call returns extraction JSON
        b.complete.side_effect = [CLASSIFY_RESULT, EXTRACT_RESULT]

        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = self._make_eyes_result("Starbucks HKD 45")
        eyes_mod.has_enough_text.return_value = True

        with patch.object(_intel, "_eyes_module", eyes_mod):
            result = process_document(
                b"fake-pdf", "doc.pdf", "application/pdf",
                MINIMAL_SCHEMAS, backend=b
            )

        assert result["doc_type"] == "receipt"
        assert result["_extraction_method"] == "text"
        assert result["extracted_text"] == "Starbucks HKD 45"

    def test_vision_mode_when_force_vision(self):
        """force_vision=True skips EYES text path, goes straight to vision."""
        b = make_backend(has_vision=True)
        b.complete_vision.side_effect = [CLASSIFY_RESULT, EXTRACT_RESULT]

        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = self._make_eyes_result("some text")
        eyes_mod.has_enough_text.return_value = True  # would normally pick text mode

        with patch.object(_intel, "_eyes_module", eyes_mod):
            result = process_document(
                b"fake-img", "photo.jpg", "image/jpeg",
                MINIMAL_SCHEMAS, force_vision=True, backend=b
            )

        assert result["_extraction_method"] == "vision"

    def test_local_only_fallback_when_backend_unavailable(self):
        """Backend not available → EYES-only, no AI analysis."""
        b = make_backend(is_available=False, has_vision=False)

        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = self._make_eyes_result("some extracted text")

        with patch.object(_intel, "_eyes_module", eyes_mod):
            result = process_document(
                b"fake-pdf", "doc.pdf", "application/pdf",
                MINIMAL_SCHEMAS, backend=b
            )

        b.complete.assert_not_called()
        b.complete_vision.assert_not_called()
        assert result["doc_type"] == "other"
        assert result["_extraction_method"] == "local_eyes_only"
        assert result["_claude_access"] == "none"
        assert "extracted_text" in result

    def test_local_only_fallback_no_text_extracted(self):
        b = make_backend(is_available=False)

        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = self._make_eyes_result("")  # no text

        with patch.object(_intel, "_eyes_module", eyes_mod):
            result = process_document(
                b"scanned.pdf", "scan.pdf", "application/pdf",
                MINIMAL_SCHEMAS, backend=b
            )

        assert "scanned" in result["summary"].lower() or "no text" in result["summary"].lower()

    def test_result_shape_text_mode(self):
        b = make_backend(has_vision=True)
        b.complete.side_effect = [CLASSIFY_RESULT, EXTRACT_RESULT]

        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = self._make_eyes_result("text content")
        eyes_mod.has_enough_text.return_value = True

        with patch.object(_intel, "_eyes_module", eyes_mod):
            result = process_document(
                b"bytes", "doc.pdf", "application/pdf",
                MINIMAL_SCHEMAS, backend=b
            )

        required_keys = {
            "doc_type", "summary", "key_points", "important_dates", "red_flags",
            "action_items", "detected_names", "currency", "document_language",
            "classification", "extracted_text", "_extraction_method",
            "_eyes_method", "_claude_access",
        }
        assert required_keys.issubset(result.keys())

    def test_injected_backend_overrides_env(self):
        """Backend passed directly to process_document is used, not the env-default."""
        custom_backend = make_backend(has_vision=True)
        custom_backend.complete.side_effect = [CLASSIFY_RESULT, EXTRACT_RESULT]

        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = self._make_eyes_result("text")
        eyes_mod.has_enough_text.return_value = True

        with patch("simply_connect.backends.get_backend") as mock_factory, \
             patch.object(_intel, "_eyes_module", eyes_mod):
            process_document(b"bytes", "doc.pdf", "application/pdf", MINIMAL_SCHEMAS,
                             backend=custom_backend)
            mock_factory.assert_not_called()

    def test_complex_doc_type_uses_sonnet_model(self):
        """bank_statement is in complex_doc_types → sonnet model passed to extraction."""
        b = make_backend(has_vision=True)
        classify_resp = json.dumps({
            "doc_type": "bank_statement",
            "detected_names": [],
            "document_language": "en",
            "complexity": "complex",
            "brief_description": "Bank statement",
            "currency": "HKD",
        })
        b.complete.side_effect = [classify_resp, EXTRACT_RESULT]

        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = self._make_eyes_result("statement text")
        eyes_mod.has_enough_text.return_value = True

        with patch.object(_intel, "_eyes_module", eyes_mod):
            process_document(b"bytes", "statement.pdf", "application/pdf",
                             MINIMAL_SCHEMAS, backend=b)

        # Second complete() call is Phase B extraction
        extract_call = b.complete.call_args_list[1]
        model_used = (
            extract_call.kwargs.get("model") or
            (extract_call[1].get("model") if extract_call[1] else None)
        )
        assert model_used == "claude-sonnet-4-5"

    def test_default_backend_used_when_none_passed(self):
        """When backend=None, process_document calls get_backend()."""
        mock_backend = make_backend(has_vision=True)
        mock_backend.complete.side_effect = [CLASSIFY_RESULT, EXTRACT_RESULT]

        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = self._make_eyes_result("text")
        eyes_mod.has_enough_text.return_value = True

        with patch.object(_intel, "_eyes_module", eyes_mod), \
             patch("simply_connect.intelligence.get_backend", return_value=mock_backend) as mock_factory:
            process_document(b"bytes", "doc.pdf", "application/pdf", MINIMAL_SCHEMAS)
            mock_factory.assert_called_once()
