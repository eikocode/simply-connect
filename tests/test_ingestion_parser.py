"""
Tests for ingestion.py parser routing.

Covers:
  - Claude path: text PDF → _call_text_prompt; image → _call_claude_vision
  - Docling path: always returns text → _call_text_prompt
  - Docling unavailable → clear RuntimeError with install instructions
  - Docling path calls _call_text_prompt with empty api_key (OAuth)
  - Both paths produce same output shape

No live API calls — all Claude calls and Docling are mocked.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FAKE_EXTRACTIONS = [
    {"summary": "Test item", "content": "Some content.", "category": "general"}
]
_FAKE_JSON = json.dumps(_FAKE_EXTRACTIONS)

_MINIMAL_PROFILE = {
    "name": "test-assistant",
    "category_map": {"general": "general.md"},
}
_MINIMAL_COMMITTED: dict = {}


def _txt_file(tmp_path: Path, content: str = "Hello world contract text.") -> Path:
    p = tmp_path / "doc.txt"
    p.write_text(content)
    return p


def _jpg_file(tmp_path: Path) -> Path:
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)  # minimal JPEG header
    return p


def _pdf_file(tmp_path: Path, content: bytes = b"%PDF-1.4 fake-pdf") -> Path:
    p = tmp_path / "contract.pdf"
    p.write_bytes(content)
    return p


# ---------------------------------------------------------------------------
# Claude path — text formats
# ---------------------------------------------------------------------------

class TestClaudePathTextFormats:
    def test_txt_file_uses_text_prompt_not_vision(self, tmp_path):
        """.txt files go through _call_text_prompt, not _call_claude_vision."""
        filepath = _txt_file(tmp_path)

        with patch("simply_connect.ingestion._call_text_prompt", return_value=_FAKE_JSON) as mock_text, \
             patch("simply_connect.ingestion._call_claude_vision") as mock_vision, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            from simply_connect.ingestion import ingest_document
            result = ingest_document(filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="claude")

        mock_text.assert_called_once()
        mock_vision.assert_not_called()
        assert result["success"] is True

    def test_text_pdf_with_pypdf_uses_text_prompt(self, tmp_path):
        """PDFs with extractable text go through _call_text_prompt."""
        filepath = _pdf_file(tmp_path)

        with patch("simply_connect.ingestion._call_text_prompt", return_value=_FAKE_JSON) as mock_text, \
             patch("simply_connect.ingestion._call_claude_vision") as mock_vision, \
             patch("simply_connect.ingestion._read_pdf_text", return_value="Contract text here."), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            from simply_connect.ingestion import ingest_document
            result = ingest_document(filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="claude")

        mock_text.assert_called_once()
        mock_vision.assert_not_called()
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Claude path — vision formats
# ---------------------------------------------------------------------------

class TestClaudePathVisionFormats:
    def test_image_file_uses_vision_not_text_prompt(self, tmp_path):
        """Image files go through _call_claude_vision on the Claude path."""
        filepath = _jpg_file(tmp_path)

        with patch("simply_connect.ingestion._call_claude_vision", return_value=_FAKE_JSON) as mock_vision, \
             patch("simply_connect.ingestion._call_text_prompt") as mock_text, \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            from simply_connect.ingestion import ingest_document
            result = ingest_document(filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="claude")

        mock_vision.assert_called_once()
        mock_text.assert_not_called()
        assert result["success"] is True

    def test_image_pdf_falls_back_to_vision(self, tmp_path):
        """PDFs with no extractable text (image PDF) fall back to vision."""
        filepath = _pdf_file(tmp_path)

        with patch("simply_connect.ingestion._call_claude_vision", return_value=_FAKE_JSON) as mock_vision, \
             patch("simply_connect.ingestion._call_text_prompt") as mock_text, \
             patch("simply_connect.ingestion._read_pdf_text", return_value=""), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            from simply_connect.ingestion import ingest_document
            result = ingest_document(filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="claude")

        mock_vision.assert_called_once()
        mock_text.assert_not_called()
        assert result["success"] is True

    def test_vision_without_api_key_returns_clear_error(self, tmp_path):
        """Vision path without API key returns success=False with a clear error message."""
        filepath = _jpg_file(tmp_path)

        with patch("simply_connect.ingestion._read_pdf_text", return_value=None), \
             patch.dict("os.environ", {}, clear=True):
            # Clear ANTHROPIC_API_KEY
            import os
            env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
            with patch.dict("os.environ", env, clear=True):
                from simply_connect.ingestion import ingest_document
                result = ingest_document(filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="claude")

        assert result["success"] is False
        assert result["error"] is not None
        assert "ANTHROPIC_API_KEY" in result["error"] or "vision" in result["error"].lower()


# ---------------------------------------------------------------------------
# Docling path
# ---------------------------------------------------------------------------

class TestDoclingPath:
    def test_docling_path_calls_text_prompt_not_vision(self, tmp_path):
        """Docling path always calls _call_text_prompt (not vision)."""
        filepath = _jpg_file(tmp_path)

        mock_converter_result = MagicMock()
        mock_converter_result.document.export_to_markdown.return_value = "# Document\n\nExtracted text."
        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_converter_result

        with patch("simply_connect.ingestion._call_text_prompt", return_value=_FAKE_JSON) as mock_text, \
             patch("simply_connect.ingestion._call_claude_vision") as mock_vision, \
             patch.dict("os.environ", {}, clear=False):

            # Patch the import inside _parse_with_docling
            import sys
            mock_docling_module = MagicMock()
            mock_docling_module.DocumentConverter.return_value = mock_converter

            with patch.dict("sys.modules", {"docling": MagicMock(), "docling.document_converter": mock_docling_module}):
                from simply_connect import ingestion
                import importlib
                importlib.reload(ingestion)

                result = ingestion.ingest_document(
                    filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="docling"
                )

        # Vision should not be called
        mock_vision.assert_not_called()

    def test_docling_path_passes_empty_api_key_to_text_prompt(self, tmp_path):
        """Docling categorisation calls _call_text_prompt with empty api_key (OAuth path)."""
        filepath = _txt_file(tmp_path)

        captured_api_keys = []

        def capturing_text_prompt(prompt, api_key):
            captured_api_keys.append(api_key)
            return _FAKE_JSON

        mock_converter_result = MagicMock()
        mock_converter_result.document.export_to_markdown.return_value = "Extracted markdown."
        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_converter_result

        import sys
        mock_docling_module = MagicMock()
        mock_docling_module.DocumentConverter.return_value = mock_converter

        with patch.dict("sys.modules", {"docling": MagicMock(), "docling.document_converter": mock_docling_module}), \
             patch.dict("os.environ", {k: v for k, v in __import__("os").environ.items() if k != "ANTHROPIC_API_KEY"}, clear=True):
            from simply_connect import ingestion
            import importlib
            importlib.reload(ingestion)

            with patch.object(ingestion, "_call_text_prompt", side_effect=capturing_text_prompt):
                result = ingestion.ingest_document(
                    filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="docling"
                )

        assert result["success"] is True
        # _call_text_prompt should have been called with empty api_key
        assert any(k == "" for k in captured_api_keys), \
            f"Expected empty api_key for OAuth path, got: {captured_api_keys}"

    def test_docling_unavailable_returns_clear_error(self, tmp_path):
        """When Docling is not installed, ingest_document returns a helpful error."""
        filepath = _jpg_file(tmp_path)

        # Simulate docling not installed by making the import fail
        import sys
        with patch.dict("sys.modules", {"docling": None, "docling.document_converter": None}):
            from simply_connect import ingestion
            import importlib
            importlib.reload(ingestion)

            result = ingestion.ingest_document(
                filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="docling"
            )

        assert result["success"] is False
        assert result["error"] is not None
        assert "docling" in result["error"].lower() or "install" in result["error"].lower()


# ---------------------------------------------------------------------------
# Output shape consistency
# ---------------------------------------------------------------------------

class TestOutputShape:
    """Both parser paths return the same result dict shape."""

    def _check_shape(self, result: dict) -> None:
        assert isinstance(result, dict)
        assert "success" in result
        assert "extractions" in result
        assert "error" in result
        assert "file" in result
        assert "format" in result
        assert "parser" in result
        assert isinstance(result["extractions"], list)

    def test_claude_path_returns_correct_shape(self, tmp_path):
        filepath = _txt_file(tmp_path)
        with patch("simply_connect.ingestion._call_text_prompt", return_value=_FAKE_JSON), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            from simply_connect.ingestion import ingest_document
            result = ingest_document(filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="claude")
        self._check_shape(result)
        assert result["parser"] == "claude"

    def test_docling_path_returns_correct_shape(self, tmp_path):
        filepath = _txt_file(tmp_path)

        mock_converter_result = MagicMock()
        mock_converter_result.document.export_to_markdown.return_value = "Markdown content."
        mock_converter = MagicMock()
        mock_converter.convert.return_value = mock_converter_result

        import sys
        mock_docling_module = MagicMock()
        mock_docling_module.DocumentConverter.return_value = mock_converter

        with patch.dict("sys.modules", {"docling": MagicMock(), "docling.document_converter": mock_docling_module}):
            from simply_connect import ingestion
            import importlib
            importlib.reload(ingestion)

            with patch.object(ingestion, "_call_text_prompt", return_value=_FAKE_JSON):
                result = ingestion.ingest_document(
                    filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="docling"
                )

        self._check_shape(result)
        assert result["parser"] == "docling"

    def test_unsupported_format_returns_correct_shape(self, tmp_path):
        filepath = tmp_path / "spreadsheet.xlsx"
        filepath.write_bytes(b"fake xlsx")
        from simply_connect.ingestion import ingest_document
        result = ingest_document(filepath, _MINIMAL_COMMITTED, _MINIMAL_PROFILE, parser="claude")
        self._check_shape(result)
        assert result["success"] is False
