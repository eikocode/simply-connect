"""
Tests for AnthropicBackend.complete_vision() content block encoding.

Root cause of the 500 bug:
  PDFs were being sent as { "type": "image", "media_type": "image/jpeg" }
  with PDF bytes — the Anthropic API rejected this with a 500 Internal
  Server Error. The fix uses { "type": "document", "media_type":
  "application/pdf" } for PDFs, matching the Anthropic API spec.

Coverage:
  1. PDF → document content block (the fix)
  2. JPEG/PNG/GIF/WEBP → image content block (existing behaviour preserved)
  3. Unknown mime type → image/jpeg fallback (existing behaviour preserved)
  4. No API key → RuntimeError (not a silent failure)
  5. Full round-trip: PDF bytes reach the SDK with correct structure
  6. Full round-trip: image bytes reach the SDK with correct structure
  7. Result text is returned correctly from SDK response
"""

from __future__ import annotations

import base64
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Load backends directly (bypass __init__.py Python 3.9 issue)
# ---------------------------------------------------------------------------

def _setup_pkg() -> None:
    if "simply_connect" not in sys.modules or not hasattr(
        sys.modules["simply_connect"], "__path__"
    ):
        from pathlib import Path
        pkg = types.ModuleType("simply_connect")
        pkg.__path__ = [str(Path(__file__).parent.parent / "simply_connect")]
        pkg.__package__ = "simply_connect"
        sys.modules["simply_connect"] = pkg


def _load_backends():
    _setup_pkg()
    name = "simply_connect.backends"
    if name in sys.modules:
        del sys.modules[name]  # force fresh load so edits are picked up
    from pathlib import Path
    path = Path(__file__).parent.parent / "simply_connect" / "backends.py"
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_bk = _load_backends()


FAKE_PDF_BYTES  = b"%PDF-1.4 fake pdf content"
FAKE_JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 64
FAKE_PNG_BYTES  = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

EXPECTED_RESPONSE_TEXT = "Extracted: CANVA HKD 1196.19"


def _make_sdk_response(text: str) -> MagicMock:
    """Fake anthropic SDK response object."""
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    return resp


def _backend_with_key() -> _bk.AnthropicBackend:
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-key"}):
        return _bk.AnthropicBackend()


# ---------------------------------------------------------------------------
# 1–3. Content block encoding (no live API call — inspect what gets built)
# ---------------------------------------------------------------------------

class TestCompleteVisionContentBlock:
    """Verify the correct content block type is built for each mime type."""

    def _capture_content(self, file_bytes: bytes, mime_type: str) -> list:
        """Call complete_vision and capture the content list passed to the SDK."""
        captured = {}

        def fake_create(**kwargs):
            captured["messages"] = kwargs["messages"]
            return _make_sdk_response(EXPECTED_RESPONSE_TEXT)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_create

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
             patch("anthropic.Anthropic", return_value=mock_client):
            backend = _bk.AnthropicBackend()
            backend.complete_vision(
                system="You are a doc classifier.",
                file_bytes=file_bytes,
                mime_type=mime_type,
                prompt="Classify this.",
                model="claude-haiku-4-5",
                max_tokens=512,
            )

        return captured["messages"][0]["content"]

    def test_pdf_uses_document_content_block(self):
        """PDFs must use type=document with media_type=application/pdf."""
        content = self._capture_content(FAKE_PDF_BYTES, "application/pdf")
        file_block = content[0]

        assert file_block["type"] == "document", (
            f"PDF must use 'document' block, got '{file_block['type']}'. "
            "Sending PDF bytes as 'image' causes Anthropic API 500."
        )
        assert file_block["source"]["media_type"] == "application/pdf"
        assert file_block["source"]["type"] == "base64"
        assert file_block["source"]["data"] == base64.standard_b64encode(FAKE_PDF_BYTES).decode()

    def test_pdf_does_not_use_image_block(self):
        """PDFs must NOT be sent as image/jpeg — that causes a 500."""
        content = self._capture_content(FAKE_PDF_BYTES, "application/pdf")
        file_block = content[0]

        assert file_block["type"] != "image", (
            "PDF was sent as image block — this is the bug that caused 500 errors."
        )
        assert file_block["source"].get("media_type") != "image/jpeg", (
            "PDF media_type must not be image/jpeg."
        )

    def test_jpeg_uses_image_content_block(self):
        """JPEG images must use type=image with media_type=image/jpeg."""
        content = self._capture_content(FAKE_JPEG_BYTES, "image/jpeg")
        file_block = content[0]

        assert file_block["type"] == "image"
        assert file_block["source"]["media_type"] == "image/jpeg"
        assert file_block["source"]["data"] == base64.standard_b64encode(FAKE_JPEG_BYTES).decode()

    def test_png_uses_image_content_block(self):
        content = self._capture_content(FAKE_PNG_BYTES, "image/png")
        assert content[0]["type"] == "image"
        assert content[0]["source"]["media_type"] == "image/png"

    def test_gif_uses_image_content_block(self):
        content = self._capture_content(b"GIF89a", "image/gif")
        assert content[0]["type"] == "image"
        assert content[0]["source"]["media_type"] == "image/gif"

    def test_webp_uses_image_content_block(self):
        content = self._capture_content(b"RIFF....WEBP", "image/webp")
        assert content[0]["type"] == "image"
        assert content[0]["source"]["media_type"] == "image/webp"

    def test_unknown_mime_type_falls_back_to_jpeg(self):
        """Unknown mime type (e.g. application/octet-stream) defaults to image/jpeg."""
        content = self._capture_content(b"unknown bytes", "application/octet-stream")
        assert content[0]["type"] == "image"
        assert content[0]["source"]["media_type"] == "image/jpeg"

    def test_prompt_is_second_content_block(self):
        """The text prompt must be the second content block for all file types."""
        for mime, data in [
            ("application/pdf",  FAKE_PDF_BYTES),
            ("image/jpeg",       FAKE_JPEG_BYTES),
        ]:
            content = self._capture_content(data, mime)
            assert len(content) == 2, f"Expected 2 content blocks for {mime}"
            assert content[1]["type"] == "text"
            assert content[1]["text"] == "Classify this."


# ---------------------------------------------------------------------------
# 4. No API key raises RuntimeError
# ---------------------------------------------------------------------------

class TestCompleteVisionNoApiKey:
    def test_raises_runtime_error_without_api_key(self):
        """Missing API key must raise RuntimeError, not silently fail."""
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            backend = _bk.AnthropicBackend()
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                backend.complete_vision(
                    system="system",
                    file_bytes=FAKE_PDF_BYTES,
                    mime_type="application/pdf",
                    prompt="classify",
                    model="claude-haiku-4-5",
                )

    def test_empty_api_key_raises_runtime_error(self):
        """Empty string API key must also raise RuntimeError."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            backend = _bk.AnthropicBackend()
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                backend.complete_vision(
                    system="system",
                    file_bytes=FAKE_PDF_BYTES,
                    mime_type="application/pdf",
                    prompt="classify",
                    model="claude-haiku-4-5",
                )


# ---------------------------------------------------------------------------
# 5–7. Full round-trip: correct SDK call and result returned
# ---------------------------------------------------------------------------

class TestCompleteVisionRoundTrip:
    """Verify the full call: correct SDK args, correct result returned."""

    def _call_vision(self, file_bytes, mime_type, mock_response_text=EXPECTED_RESPONSE_TEXT):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_sdk_response(mock_response_text)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
             patch("anthropic.Anthropic", return_value=mock_client):
            backend = _bk.AnthropicBackend()
            result = backend.complete_vision(
                system="You are a document intelligence AI.",
                file_bytes=file_bytes,
                mime_type=mime_type,
                prompt="Extract transactions.",
                model="claude-sonnet-4-5",
                max_tokens=8192,
            )

        return result, mock_client.messages.create.call_args

    def test_pdf_round_trip_returns_correct_text(self):
        """PDF vision call returns the text from the SDK response."""
        result, _ = self._call_vision(FAKE_PDF_BYTES, "application/pdf")
        assert result == EXPECTED_RESPONSE_TEXT

    def test_image_round_trip_returns_correct_text(self):
        """Image vision call returns the text from the SDK response."""
        result, _ = self._call_vision(FAKE_JPEG_BYTES, "image/jpeg")
        assert result == EXPECTED_RESPONSE_TEXT

    def test_pdf_round_trip_passes_correct_model(self):
        """Model name is passed through to the SDK create call."""
        _, kwargs = self._call_vision(FAKE_PDF_BYTES, "application/pdf")
        assert kwargs.kwargs["model"] == "claude-sonnet-4-5"

    def test_pdf_round_trip_passes_correct_max_tokens(self):
        _, kwargs = self._call_vision(FAKE_PDF_BYTES, "application/pdf")
        assert kwargs.kwargs["max_tokens"] == 8192

    def test_pdf_round_trip_passes_correct_system(self):
        _, kwargs = self._call_vision(FAKE_PDF_BYTES, "application/pdf")
        assert kwargs.kwargs["system"] == "You are a document intelligence AI."

    def test_pdf_round_trip_single_user_message(self):
        """SDK must receive exactly one user message."""
        _, kwargs = self._call_vision(FAKE_PDF_BYTES, "application/pdf")
        messages = kwargs.kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_amex_pdf_classification_reaches_sdk_as_document(self):
        """End-to-end: Amex PDF bytes → SDK receives document block, not image block."""
        amex_pdf_bytes = b"%PDF-1.4 American Express statement fake"
        _, kwargs = self._call_vision(amex_pdf_bytes, "application/pdf", "credit_card")

        content = kwargs.kwargs["messages"][0]["content"]
        file_block = content[0]

        assert file_block["type"] == "document", (
            "Amex PDF must reach the SDK as a document block. "
            "Sending as image causes 500 Internal Server Error."
        )
        assert file_block["source"]["media_type"] == "application/pdf"
        # Verify the bytes are correctly base64-encoded
        decoded = base64.standard_b64decode(file_block["source"]["data"])
        assert decoded == amex_pdf_bytes
