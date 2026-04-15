"""
Live end-to-end tests for the simply-connect intelligence pipeline.

Uses real PDFs from the fixture directory — no mocks, no stubbed responses.
Every test makes real API calls and validates the actual pipeline output.

Run with:
    pytest tests/e2e/ --e2e -v

    # Or point to an existing PDF directory (e.g. Downloads):
    SC_E2E_FIXTURES_DIR=~/Downloads pytest tests/e2e/ --e2e -v

Fixture directory (checked in order):
    tests/e2e/fixtures/      — default local path (gitignored)
    SC_E2E_FIXTURES_DIR      — env var override

HSBC statements are expected to produce:
  - doc_type: "bank_statement" or "credit_card"
  - currency: "HKD" (or at least not null for a real statement)
  - _extraction_method: "text" (EYES should extract text cleanly from HSBC PDFs)
  - transactions: list (may be empty if schema has no transactions field)
  - summary: non-empty string
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.e2e.conftest import all_pdfs, hsbc_statements, receipt_pdfs


# ---------------------------------------------------------------------------
# Schemas — realistic but minimal; no save-my-brain import needed
# ---------------------------------------------------------------------------

CLASSIFY_SCHEMA = """{
  "doc_type": "receipt|bank_statement|credit_card|insurance|medical|legal|contract|utility|id_document|tax|travel|hotel|event|school|other",
  "detected_names": [],
  "document_language": "en|zh|ja|other",
  "complexity": "simple|complex",
  "brief_description": "One-line description",
  "currency": "HKD|USD|GBP|JPY|EUR|null"
}
Return ONLY the JSON, no markdown fences, no explanation."""

BANK_STATEMENT_SCHEMA = """{
  "summary": "2-3 sentence summary of the statement period and account",
  "key_points": ["point 1", "point 2"],
  "important_dates": [
    {"label": "Statement period", "date": "YYYY-MM-DD", "days_until": -1}
  ],
  "red_flags": [],
  "action_items": [],
  "transactions": [
    {"date": "YYYY-MM-DD", "description": "merchant name", "amount": 0.0,
     "currency": "HKD", "type": "debit|credit", "category": "category"}
  ],
  "opening_balance": null,
  "closing_balance": null
}"""

RECEIPT_SCHEMA = """{
  "summary": "One sentence describing the receipt",
  "key_points": [],
  "important_dates": [],
  "red_flags": [],
  "action_items": [],
  "transactions": [
    {"date": "YYYY-MM-DD", "description": "item or merchant", "amount": 0.0, "currency": "HKD"}
  ]
}"""

GENERIC_SCHEMA = """{
  "summary": "2-3 sentence summary",
  "key_points": ["point 1", "point 2"],
  "important_dates": [],
  "red_flags": [],
  "action_items": []
}"""

E2E_SCHEMAS = {
    "classify_schema":           CLASSIFY_SCHEMA,
    "extraction_schemas":        {
        "bank_statement": BANK_STATEMENT_SCHEMA,
        "credit_card":    BANK_STATEMENT_SCHEMA,
        "receipt":        RECEIPT_SCHEMA,
    },
    "default_extraction_schema": GENERIC_SCHEMA,
    "complex_doc_types":         {"bank_statement", "credit_card"},
    "haiku_model":               "claude-haiku-4-5",
    "sonnet_model":              "claude-sonnet-4-5",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {
    "doc_type", "summary", "key_points", "important_dates", "red_flags",
    "action_items", "detected_names", "currency", "document_language",
    "classification", "extracted_text", "_extraction_method",
    "_eyes_method", "_claude_access",
}


def _run(intelligence_module, anthropic_backend, pdf_path: Path) -> dict:
    """Run the full pipeline on a single PDF and return the result dict."""
    file_bytes = pdf_path.read_bytes()
    start = time.time()
    result = intelligence_module.process_document(
        file_bytes,
        pdf_path.name,
        "application/pdf",
        E2E_SCHEMAS,
        user_language="en",
        backend=anthropic_backend,
    )
    elapsed = time.time() - start
    print(f"\n  [{pdf_path.name}] {elapsed:.1f}s | "
          f"doc_type={result.get('doc_type')} | "
          f"method={result.get('_extraction_method')} | "
          f"eyes={result.get('_eyes_method')} | "
          f"access={result.get('_claude_access')}")
    return result


# ---------------------------------------------------------------------------
# HSBC statements
# ---------------------------------------------------------------------------

_hsbc = hsbc_statements()


@pytest.mark.e2e
@pytest.mark.skipif(not _hsbc, reason="No HSBC statement PDFs found in fixture directory")
@pytest.mark.parametrize("pdf_path", _hsbc, ids=[p.name for p in _hsbc])
def test_hsbc_statement_classification(pdf_path, intelligence_module, anthropic_backend):
    """HSBC statements must classify as bank_statement or credit_card."""
    result = _run(intelligence_module, anthropic_backend, pdf_path)

    assert result["doc_type"] in ("bank_statement", "credit_card"), (
        f"Expected bank_statement or credit_card, got {result['doc_type']!r}\n"
        f"Summary: {result.get('summary', '')[:200]}"
    )


@pytest.mark.e2e
@pytest.mark.skipif(not _hsbc, reason="No HSBC statement PDFs found in fixture directory")
@pytest.mark.parametrize("pdf_path", _hsbc, ids=[p.name for p in _hsbc])
def test_hsbc_statement_currency(pdf_path, intelligence_module, anthropic_backend):
    """HSBC HK statements must detect HKD currency."""
    result = _run(intelligence_module, anthropic_backend, pdf_path)

    assert result["currency"] == "HKD", (
        f"Expected HKD, got {result['currency']!r}"
    )


@pytest.mark.e2e
@pytest.mark.skipif(not _hsbc, reason="No HSBC statement PDFs found in fixture directory")
@pytest.mark.parametrize("pdf_path", _hsbc, ids=[p.name for p in _hsbc])
def test_hsbc_statement_text_extraction(pdf_path, intelligence_module, anthropic_backend):
    """EYES should extract meaningful text from HSBC PDFs (not fall back to vision)."""
    result = _run(intelligence_module, anthropic_backend, pdf_path)

    assert result["_extraction_method"] == "text", (
        f"Expected text mode (EYES should handle HSBC PDFs), "
        f"got {result['_extraction_method']!r} — "
        f"eyes_method={result.get('_eyes_method')!r}, "
        f"extracted_text length={len(result.get('extracted_text', ''))}"
    )
    assert len(result["extracted_text"]) > 100, (
        f"Very little text extracted: {len(result['extracted_text'])} chars"
    )


@pytest.mark.e2e
@pytest.mark.skipif(not _hsbc, reason="No HSBC statement PDFs found in fixture directory")
@pytest.mark.parametrize("pdf_path", _hsbc, ids=[p.name for p in _hsbc])
def test_hsbc_statement_has_transactions(pdf_path, intelligence_module, anthropic_backend):
    """HSBC statements must produce at least one transaction."""
    result = _run(intelligence_module, anthropic_backend, pdf_path)
    transactions = result.get("transactions", [])

    assert isinstance(transactions, list), (
        f"transactions field is not a list: {type(transactions)}"
    )
    assert len(transactions) > 0, (
        f"No transactions extracted from {pdf_path.name}\n"
        f"Summary: {result.get('summary', '')[:200]}\n"
        f"Extracted text (first 500 chars): {result.get('extracted_text', '')[:500]}"
    )


@pytest.mark.e2e
@pytest.mark.skipif(not _hsbc, reason="No HSBC statement PDFs found in fixture directory")
@pytest.mark.parametrize("pdf_path", _hsbc, ids=[p.name for p in _hsbc])
def test_hsbc_statement_transaction_shape(pdf_path, intelligence_module, anthropic_backend):
    """Each transaction must have required fields with sensible types."""
    result = _run(intelligence_module, anthropic_backend, pdf_path)
    transactions = result.get("transactions", [])

    if not transactions:
        pytest.skip("No transactions to validate (covered by test_hsbc_statement_has_transactions)")

    for i, txn in enumerate(transactions):
        assert isinstance(txn, dict), f"Transaction {i} is not a dict: {txn!r}"
        assert "description" in txn or "merchant" in txn, (
            f"Transaction {i} has no description/merchant: {txn}"
        )
        amount = txn.get("amount")
        assert amount is None or isinstance(amount, (int, float)), (
            f"Transaction {i} amount is not numeric: {amount!r}"
        )


@pytest.mark.e2e
@pytest.mark.skipif(not _hsbc, reason="No HSBC statement PDFs found in fixture directory")
@pytest.mark.parametrize("pdf_path", _hsbc, ids=[p.name for p in _hsbc])
def test_hsbc_statement_no_false_red_flags(pdf_path, intelligence_module, anthropic_backend):
    """Old statements must NOT generate false 'payment overdue' red flags."""
    result = _run(intelligence_module, anthropic_backend, pdf_path)
    red_flags = result.get("red_flags", [])

    overdue_flags = [
        f for f in red_flags
        if any(kw in str(f).lower() for kw in ("overdue", "past due", "missed payment", "late"))
    ]
    assert not overdue_flags, (
        f"False overdue red flags on historical statement {pdf_path.name}:\n"
        + "\n".join(f"  - {f}" for f in overdue_flags)
    )


@pytest.mark.e2e
@pytest.mark.skipif(not _hsbc, reason="No HSBC statement PDFs found in fixture directory")
@pytest.mark.parametrize("pdf_path", _hsbc, ids=[p.name for p in _hsbc])
def test_hsbc_statement_result_shape(pdf_path, intelligence_module, anthropic_backend):
    """Result dict must contain all required keys."""
    result = _run(intelligence_module, anthropic_backend, pdf_path)

    missing = REQUIRED_KEYS - result.keys()
    assert not missing, f"Missing keys in result: {missing}"
    assert result["summary"] != "", "Summary is empty"
    assert isinstance(result["key_points"], list), "key_points is not a list"


# ---------------------------------------------------------------------------
# Receipt PDFs
# ---------------------------------------------------------------------------

_receipts = receipt_pdfs()


@pytest.mark.e2e
@pytest.mark.skipif(not _receipts, reason="No receipt PDFs found in fixture directory")
@pytest.mark.parametrize("pdf_path", _receipts, ids=[p.name for p in _receipts])
def test_receipt_classification(pdf_path, intelligence_module, anthropic_backend):
    """Receipt PDFs should classify as receipt (or close equivalent)."""
    result = _run(intelligence_module, anthropic_backend, pdf_path)

    assert result["doc_type"] in ("receipt", "bank_statement", "other"), (
        f"Unexpected doc_type for receipt {pdf_path.name}: {result['doc_type']!r}"
    )
    assert result["summary"] != "", "Summary is empty"


# ---------------------------------------------------------------------------
# All PDFs — result shape (smoke test for any fixture file)
# ---------------------------------------------------------------------------

_all = all_pdfs()


@pytest.mark.e2e
@pytest.mark.skipif(not _all, reason="No PDFs found in fixture directory")
@pytest.mark.parametrize("pdf_path", _all, ids=[p.name for p in _all])
def test_any_pdf_result_shape(pdf_path, intelligence_module, anthropic_backend):
    """Any PDF in the fixture dir must produce a valid result dict without crashing."""
    result = _run(intelligence_module, anthropic_backend, pdf_path)

    assert isinstance(result, dict), "process_document must return a dict"
    missing = REQUIRED_KEYS - result.keys()
    assert not missing, f"Missing keys: {missing}"
    assert result["doc_type"] != "", "doc_type is empty"
    assert result["_extraction_method"] in ("text", "vision", "local_eyes_only"), (
        f"Unexpected _extraction_method: {result['_extraction_method']!r}"
    )
