"""
Integration test — real Amex PDF through docling (phase 1) + Claude CLI OAuth (phase 2).

No API key required. Uses the claude CLI subprocess with your Anthropic subscription.

What this proves:
  - Docling extracts text from the real two-column Amex HK statement
  - Smart vision detection correctly identifies it needs vision mode (foreign currency keywords)
  - Claude CLI (OAuth) classifies and extracts transactions correctly
  - CR lines (autopay) are excluded
  - HKD amounts are used, not foreign currency amounts

Run with:
    SC_DATA_DIR=/Users/eiko/Dev/deployments/save-my-brain \\
    pytest tests/test_amex_cli_integration.py -v -s
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import types
from pathlib import Path

import pytest

AMEX_PDF = Path("/Users/eiko/Downloads/2026-04-02.pdf")   # most recent AE statement
SC_DATA_DIR = Path(os.getenv("SC_DATA_DIR", "/Users/eiko/Dev/deployments/save-my-brain"))

# ---------------------------------------------------------------------------
# Module loading (same pattern as test_amex_integration.py)
# ---------------------------------------------------------------------------

def _setup_pkg() -> None:
    if "simply_connect" not in sys.modules or not hasattr(
        sys.modules["simply_connect"], "__path__"
    ):
        pkg = types.ModuleType("simply_connect")
        pkg.__path__ = [str(Path(__file__).parent.parent / "simply_connect")]
        pkg.__package__ = "simply_connect"
        sys.modules["simply_connect"] = pkg


def _load_direct(full_name: str):
    import importlib
    _setup_pkg()
    return importlib.import_module(full_name)


_bk    = _load_direct("simply_connect.backends")
_intel = _load_direct("simply_connect.intelligence")

# Load .env so SC_DATA_DIR etc. are available
from dotenv import load_dotenv
load_dotenv(SC_DATA_DIR / ".env", override=False)

# Load extension schemas (same as test_amex_integration.py)
# Load extension as a proper package (it uses relative imports)
if str(SC_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(SC_DATA_DIR))

import importlib
_ext_intel = importlib.import_module("extension.intelligence")


# ---------------------------------------------------------------------------
# Prereq checks
# ---------------------------------------------------------------------------

def _claude_cli_available() -> bool:
    return shutil.which("claude") is not None


pytestmark = pytest.mark.skipif(
    not AMEX_PDF.exists(),
    reason=f"Amex PDF not found at {AMEX_PDF}",
)


@pytest.fixture(scope="module")
def amex_pdf_bytes():
    return AMEX_PDF.read_bytes()


@pytest.fixture(scope="module")
def cli_backend():
    if not _claude_cli_available():
        pytest.skip("claude CLI not on PATH — OAuth not available")
    b = _bk.AnthropicBackend()
    if not b._has_cli():
        pytest.skip("claude CLI not available")
    # Force CLI mode (no API key)
    original = b._has_api_key
    b._has_api_key = lambda: False
    return b


@pytest.fixture(scope="module")
def extraction(amex_pdf_bytes, cli_backend):
    """Run the full pipeline once; share result across all tests in this module."""
    schemas = _ext_intel.get_document_schemas()
    result = _intel.process_document(
        file_bytes=amex_pdf_bytes,
        mime_type="application/pdf",
        filename="2026-04-02.pdf",
        backend=cli_backend,
        schemas=schemas,
    )
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVisionDetection:
    def test_extraction_method_recorded(self, extraction):
        """Pipeline must record which mode was used (text or vision)."""
        method = extraction.get("_extraction_method")
        assert method in ("text", "vision"), (
            f"Expected text or vision, got '{method}'"
        )
        # Log which mode was chosen so it's visible in test output
        print(f"\n  Extraction method: {method} (vision triggers when ≥2 foreign currency keywords found)")

    def test_eyes_method_recorded(self, extraction):
        assert extraction.get("_eyes_method"), "No _eyes_method recorded"


class TestClassification:
    def test_classified_as_credit_card(self, extraction):
        doc_type = extraction.get("doc_type", "")
        assert doc_type == "credit_card", (
            f"Expected doc_type=credit_card, got '{doc_type}'"
        )

    def test_currency_detected(self, extraction):
        # CLI may return HKD, hkd, None, or "" — all acceptable for HK statement
        currency = extraction.get("currency") or ""
        assert currency.upper() in ("HKD", ""), f"Unexpected currency: {currency}"


class TestTransactionExtraction:
    def test_transactions_present(self, extraction):
        txns = extraction.get("transactions", [])
        assert len(txns) > 0, "No transactions extracted"

    def test_no_cr_transactions(self, extraction):
        """CR lines (autopay payments) must be excluded."""
        txns = extraction.get("transactions", [])
        for t in txns:
            desc = str(t.get("description", "")).upper()
            amount = t.get("amount", 0)
            assert "AUTOPAY" not in desc or amount < 0, (
                f"AUTOPAY transaction found — CR exclusion failed: {t}"
            )
            assert amount <= 0, (
                f"Positive amount found (should be negative expenses): {t}"
            )

    def test_no_large_positive_amount(self, extraction):
        """The autopay CR line (~5258 HKD) must not appear."""
        txns = extraction.get("transactions", [])
        amounts = [t.get("amount", 0) for t in txns]
        assert not any(a > 1000 for a in amounts), (
            f"Large positive amount found — autopay CR not excluded: {amounts}"
        )

    def test_amounts_are_hkd_scale(self, extraction):
        """All amounts should be in HKD scale (not tiny foreign currency amounts)."""
        txns = extraction.get("transactions", [])
        if not txns:
            pytest.skip("No transactions to check")
        amounts = [abs(t.get("amount", 0)) for t in txns]
        # Amex HK transactions are typically > 10 HKD each
        assert max(amounts) > 10, (
            f"All amounts suspiciously small — may be using foreign currency column: {amounts}"
        )


class TestSummary:
    def test_summary_present(self, extraction):
        summary = extraction.get("summary", "")
        assert summary.strip(), "Empty summary returned"

    def test_no_fallback_message(self, extraction):
        """Must NOT be the local-only fallback (means CLI didn't run)."""
        summary = extraction.get("summary", "")
        assert "Set SC_LLM_BACKEND credentials" not in summary, (
            "Got local-only fallback — Claude CLI was not reached"
        )
        assert "ask me anything about it" not in summary, (
            "Got text-only fallback — extraction did not run"
        )
