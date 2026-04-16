"""
Live integration tests: docling (phase 1) + Claude CLI (phase 2).

These tests make REAL calls — real docling text extraction, real claude CLI subprocess.
No mocks. If PATH is wrong, OAuth expired, or docling missing, these fail with a clear message.

Run with:
    pytest tests/test_cli_docling_integration.py -v -s
"""

import io
import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest
import requests

BASE_URL = "http://localhost:8090"
SC_DATA_DIR = os.getenv("SC_DATA_DIR", "/Users/eiko/Dev/deployments/save-my-brain")


# ---------------------------------------------------------------------------
# Prereq checks — skip entire module if environment isn't ready
# ---------------------------------------------------------------------------

def _server_is_up() -> bool:
    try:
        return requests.get(f"{BASE_URL}/health", timeout=3).status_code == 200
    except Exception:
        return False


def _claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def _docling_available() -> bool:
    try:
        import docling  # noqa
        return True
    except ImportError:
        return False


def _make_minimal_pdf() -> bytes:
    """
    Build a minimal valid PDF with one line of text using only stdlib.
    No external library needed — hand-crafted PDF bytes.
    """
    content = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
        b"   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length 60 >>\nstream\n"
        b"BT /F1 12 Tf 100 700 Td (Coffee Shop Receipt HKD 45.00) Tj ET\n"
        b"endstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000266 00000 n \n"
        b"0000000378 00000 n \n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
        b"startxref\n441\n%%EOF"
    )
    return content


# ---------------------------------------------------------------------------
# Phase 1: Claude CLI reachable and authenticated
# ---------------------------------------------------------------------------

class TestClaudeCLI:
    """Verify the claude CLI is on PATH and OAuth session is valid."""

    def test_claude_on_path(self):
        path = shutil.which("claude")
        assert path is not None, (
            "claude CLI not found on PATH.\n"
            f"Current PATH: {os.environ.get('PATH','(not set)')}\n"
            "Fix: add /Users/eiko/.local/bin to PATH in com.aios.scweb.plist"
        )

    def test_claude_version(self):
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"claude --version failed: {result.stderr}"
        assert result.stdout.strip(), "claude --version returned empty output"

    def test_claude_cli_responds(self):
        """Real OAuth call — proves subscription session is active."""
        result = subprocess.run(
            ["claude", "--print", "--output-format", "json",
             "--model", "claude-haiku-4-5", "--dangerously-skip-permissions"],
            input="Reply with exactly the word: PONG",
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            f"claude CLI failed (rc={result.returncode}).\n"
            f"stderr: {result.stderr[:300]}\n"
            "Fix: run 'claude' manually to re-authenticate OAuth session"
        )
        assert "PONG" in result.stdout.upper(), (
            f"Unexpected CLI output: {result.stdout[:200]}"
        )


# ---------------------------------------------------------------------------
# Phase 2: Docling text extraction
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _docling_available(), reason="docling not installed")
class TestDoclingExtraction:
    """Verify docling can extract text from a real PDF."""

    def test_docling_imports(self):
        from docling.document_converter import DocumentConverter  # noqa
        assert DocumentConverter is not None

    def test_extract_text_from_pdf_bytes(self, tmp_path):
        """End-to-end: PDF bytes → docling → extracted text string."""
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(_make_minimal_pdf())

        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        text = result.document.export_to_markdown()

        assert text.strip(), "Docling returned empty text from PDF"
        # The PDF contains "Coffee Shop Receipt HKD 45.00"
        assert "45" in text or "Coffee" in text, (
            f"Expected receipt content in extracted text, got: {text[:200]}"
        )


# ---------------------------------------------------------------------------
# Phase 3: Full pipeline via sc-web (docling + claude CLI)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _server_is_up(), reason="sc-web not running on localhost:8090")
class TestFullPipelineViaSCWeb:
    """
    Upload a real PDF through the sc-web /upload endpoint.
    Verifies: sc-web alive → docling extracts → claude CLI classifies → DB stores.
    No mocks anywhere in this path.
    """

    def test_runtime_is_cli(self):
        """sc-web must report runtime=cli, not sdk."""
        data = requests.get(f"{BASE_URL}/health").json()
        assert data["runtime"] == "cli", (
            f"Expected runtime=cli, got {data['runtime']}.\n"
            "Fix: set SC_CLAUDE_RUNTIME=cli in .env or plist"
        )

    def test_parser_is_docling(self):
        """sc-web must report document_parser=docling."""
        data = requests.get(f"{BASE_URL}/health").json()
        assert data["document_parser"] == "docling", (
            f"Expected document_parser=docling, got {data['document_parser']}.\n"
            "Fix: set SC_DOCUMENT_PARSER=docling in .env or plist"
        )

    def test_upload_pdf_gets_summary(self, tmp_path):
        """
        POST a real PDF → expect a non-empty summary back.
        Proves the full docling → claude CLI pipeline ran.
        """
        pdf_bytes = _make_minimal_pdf()
        files = {"file": ("receipt_test.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
        data = {
            "filename": "receipt_test.pdf",
            "mime_type": "application/pdf",
            "user_id": "test_pipeline",
        }
        resp = requests.post(f"{BASE_URL}/upload", files=files, data=data, timeout=120)
        assert resp.status_code == 200, f"Upload failed ({resp.status_code}): {resp.text}"

        result = resp.json()
        assert result.get("success") is True, f"Upload not successful: {result}"

        # The reply must be a non-empty string — proves claude CLI ran and returned something
        reply = result.get("reply", "")
        assert reply.strip(), "Upload returned empty reply — claude CLI may have failed"

        # Must NOT be the local-only fallback message
        assert "Set SC_LLM_BACKEND credentials" not in reply, (
            "Got local-only fallback — claude CLI was not reached.\n"
            f"Reply: {reply[:300]}"
        )
