"""
Phase 1 (EYES) extraction tests — PyMuPDF and Docling on real Amex HK PDF.

No Claude, no API key, no OAuth required.
These tests call the EYES layer directly and prove:
  - PyMuPDF extracts substantial, structured text from the two-column layout
  - Coordinate-aware row reconstruction puts date + merchant + amount on one line
  - FX currency keywords (USD, DOLLAR, etc.) are present — explaining the vision trigger
  - Docling extracts substantial text as an alternative path
  - eyes.extract_text() correctly selects "pymupdf" for a text-based PDF
  - _needs_vision() returns True and why (FX keyword count ≥ 2)

Run with:
    SC_DATA_DIR=/Users/eiko/Dev/deployments/save-my-brain \\
    pytest tests/test_amex_phase1_extraction.py -v -s

All tests skip automatically if the Amex PDF is not present.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import types
from pathlib import Path

import pytest

AMEX_PDF = Path("/Users/eiko/Downloads/2026-04-02.pdf")
SC_DATA_DIR = Path(os.getenv("SC_DATA_DIR", "/Users/eiko/Dev/deployments/save-my-brain"))

pytestmark = pytest.mark.skipif(
    not AMEX_PDF.exists(),
    reason=f"Amex PDF not found at {AMEX_PDF}",
)

# ---------------------------------------------------------------------------
# Module loading
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
    _setup_pkg()
    return importlib.import_module(full_name)


_eyes = _load_direct("simply_connect.eyes")

# Load extension (for _needs_vision, _extract_docling_tables, _format_table_transactions)
if str(SC_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(SC_DATA_DIR))
_ext_intel = importlib.import_module("extension.intelligence")

# Import _MONTH_RE from extension for table row date assertions
_MONTH_RE = _ext_intel._MONTH_RE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})*\.\d{2}")
_WORD_RE = re.compile(r"[A-Za-z]{3,}")


def _count_structured_lines(text: str) -> int:
    """Count lines that contain both alphabetic words and a monetary amount.

    A 'structured' line is one where coordinate-aware reconstruction has
    placed the merchant name and the amount on the same row — exactly what
    the coordinate-aware PyMuPDF extractor is designed to produce.
    """
    count = 0
    for line in text.splitlines():
        if _AMOUNT_RE.search(line) and _WORD_RE.search(line):
            count += 1
    return count


def _docling_available() -> bool:
    try:
        import docling  # noqa
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def amex_bytes():
    return AMEX_PDF.read_bytes()


# ---------------------------------------------------------------------------
# PyMuPDF direct extraction
# ---------------------------------------------------------------------------

class TestPyMuPDFExtraction:
    """Call _extract_pdf_pymupdf() directly — no Claude, no docling."""

    @pytest.fixture(scope="class")
    def pymupdf_result(self, amex_bytes):
        text, page_count = _eyes._extract_pdf_pymupdf(amex_bytes)
        print(f"\n[pymupdf] page_count={page_count}  text_len={len(text)}")
        return text, page_count

    def test_extracts_substantial_text(self, pymupdf_result):
        text, _ = pymupdf_result
        assert len(text.strip()) > 1_000, (
            f"PyMuPDF returned too little text ({len(text)} chars) — "
            "may indicate extraction failure"
        )

    def test_page_count_positive(self, pymupdf_result):
        _, page_count = pymupdf_result
        assert page_count > 0, "PyMuPDF reported 0 pages"

    def test_contains_hkd_amounts(self, pymupdf_result):
        """Text must contain decimal amounts (e.g. 1,234.56)."""
        text, _ = pymupdf_result
        amounts = _AMOUNT_RE.findall(text)
        assert len(amounts) >= 5, (
            f"Expected ≥5 amount patterns, found {len(amounts)}: {amounts[:10]}"
        )

    def test_coordinate_aware_row_reconstruction(self, pymupdf_result):
        """Key quality check: merchant name and amount appear on the same line.

        Naive column-by-column extraction produces blocks of dates, then all
        merchant names, then all amounts — no line has both. Coordinate-aware
        reconstruction should produce lines that contain both alphabetic words
        and monetary amounts (e.g. 'THINKIFIC.COM VANCOUVER  432.00').
        """
        text, _ = pymupdf_result
        structured = _count_structured_lines(text)
        print(f"\n[pymupdf] structured lines (word+amount on same row): {structured}")
        assert structured >= 3, (
            f"Only {structured} lines contained both a merchant word and an amount — "
            "coordinate-aware reconstruction may not be working correctly.\n"
            f"First 500 chars:\n{text[:500]}"
        )

    def test_fx_keywords_present(self, pymupdf_result):
        """FX currency names must be visible in PyMuPDF output.

        These keywords are why _needs_vision() fires — they appear in the
        foreign currency column of the two-column Amex layout. If they're
        absent, the vision trigger would not activate.
        """
        text, _ = pymupdf_result
        fx_pattern = re.compile(
            r"\b(USD|TWD|JPY|EUR|GBP|AUD|SGD|CNH|CNY|DOLLAR|EURO|POUND|YEN|FRANC)\b",
            re.IGNORECASE,
        )
        hits = fx_pattern.findall(text)
        print(f"\n[pymupdf] FX keyword hits: {len(hits)} → {hits[:15]}")
        assert len(hits) >= 2, (
            f"Expected ≥2 FX keywords (which trigger _needs_vision), found {len(hits)}: {hits}"
        )

    def test_sample_output(self, pymupdf_result):
        """Print first 600 chars so the test output shows what phase 1 actually sees."""
        text, _ = pymupdf_result
        print(f"\n[pymupdf] First 600 chars:\n{text[:600]}")
        assert True  # informational only


# ---------------------------------------------------------------------------
# Docling direct extraction
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _docling_available(), reason="docling not installed")
class TestDoclingExtraction:
    """Call _extract_with_docling() directly — no Claude."""

    @pytest.fixture(scope="class")
    def docling_result(self, amex_bytes):
        text, page_count = _eyes._extract_with_docling(amex_bytes, ".pdf")
        print(f"\n[docling] page_count={page_count}  text_len={len(text)}")
        return text, page_count

    def test_extracts_substantial_text(self, docling_result):
        text, _ = docling_result
        assert len(text.strip()) > 500, (
            f"Docling returned too little text ({len(text)} chars)"
        )

    def test_contains_amounts(self, docling_result):
        text, _ = docling_result
        amounts = _AMOUNT_RE.findall(text)
        assert len(amounts) >= 3, (
            f"Expected ≥3 amount patterns in docling output, found {len(amounts)}"
        )

    def test_sample_output(self, docling_result):
        """Print first 600 chars so we can compare docling vs pymupdf layout."""
        text, _ = docling_result
        print(f"\n[docling] First 600 chars:\n{text[:600]}")
        assert True  # informational only


# ---------------------------------------------------------------------------
# eyes.extract_text() — public API
# ---------------------------------------------------------------------------

class TestEyesPublicAPI:
    """Call eyes.extract_text() — the same entry point the pipeline uses."""

    @pytest.fixture(scope="class")
    def eyes_result(self, amex_bytes):
        result = _eyes.extract_text(amex_bytes, "application/pdf", "2026-04-02.pdf")
        print(
            f"\n[eyes] method={result.method}  page_count={result.page_count}  "
            f"is_scanned={result.is_scanned}  text_len={len(result.text)}"
        )
        return result

    def test_method_is_pymupdf(self, eyes_result):
        """For a text-based PDF, PyMuPDF should be selected (not docling or failed)."""
        assert eyes_result.method == "pymupdf", (
            f"Expected method='pymupdf' for a text-based PDF, got '{eyes_result.method}'"
        )

    def test_text_is_substantial(self, eyes_result):
        assert len(eyes_result.text.strip()) > 1_000, (
            f"eyes.extract_text() returned only {len(eyes_result.text)} chars"
        )

    def test_page_count_set(self, eyes_result):
        assert eyes_result.page_count and eyes_result.page_count > 0, (
            f"page_count not set or zero: {eyes_result.page_count}"
        )

    def test_not_scanned(self, eyes_result):
        """Amex HK PDF is a digital statement — should NOT be flagged as scanned."""
        assert not eyes_result.is_scanned, (
            "eyes flagged this as a scanned/OCR PDF — text extraction may have failed"
        )


# ---------------------------------------------------------------------------
# _needs_vision() — vision trigger logic
# ---------------------------------------------------------------------------

class TestNeedsVision:
    """Verify _needs_vision() correctly identifies this PDF as requiring vision."""

    def test_returns_true_for_amex(self, amex_bytes):
        result = _ext_intel._needs_vision(amex_bytes, "application/pdf")
        assert result is True, (
            "Expected _needs_vision()=True for April 2026 Amex HK PDF "
            "(foreign currency entries present)"
        )

    def test_fx_keyword_count_shown(self, amex_bytes):
        """Informational: print the exact FX keyword count that triggers the flag.

        _needs_vision() returns True when ≥2 foreign currency names are found
        in the first 5 pages via PyMuPDF. This test reproduces that logic and
        prints the count so we can see WHY vision fires on this document.
        """
        try:
            import fitz
        except ImportError:
            pytest.skip("PyMuPDF not installed")

        fx_pattern = re.compile(
            r"\b(DOLLAR|EURO|POUND|YEN|FRANC|YUAN|RENMINBI|WON|BAHT|RUPEE|"
            r"RINGGIT|PESO|KRONA|KRONE|DIRHAM|RIYAL|LIRA|FORINT|ZLOTY)\b",
            re.IGNORECASE,
        )
        doc = fitz.open(stream=amex_bytes, filetype="pdf")
        full_text = "".join(doc[i].get_text() for i in range(min(len(doc), 5)))
        doc.close()

        matches = fx_pattern.findall(full_text)
        print(
            f"\n[_needs_vision] FX keyword matches: {len(matches)}\n"
            f"  Keywords found: {sorted(set(m.upper() for m in matches))}\n"
            f"  Threshold: ≥2 → result=True"
        )
        assert len(matches) >= 2, (
            f"Expected ≥2 FX keyword hits to explain why _needs_vision=True, "
            f"got {len(matches)}: {matches}"
        )

    def test_returns_false_for_plain_pdf(self):
        """Sanity check: a simple PDF with no FX keywords must NOT trigger vision."""
        # Minimal hand-crafted PDF with no currency names
        content = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
            b"   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
            b"4 0 obj\n<< /Length 55 >>\nstream\n"
            b"BT /F1 12 Tf 100 700 Td (Coffee Shop HKD 45.00) Tj ET\n"
            b"endstream\nendobj\n"
            b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
            b"xref\n0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000266 00000 n \n"
            b"0000000373 00000 n \n"
            b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
            b"startxref\n436\n%%EOF"
        )
        result = _ext_intel._needs_vision(content, "application/pdf")
        assert result is False, (
            "A simple receipt PDF with no FX keywords should NOT trigger vision"
        )


# ---------------------------------------------------------------------------
# Docling table extraction (new path)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _docling_available(), reason="docling not installed")
class TestDoclingTableExtraction:
    """Test _extract_docling_tables() and _format_table_transactions() directly.

    These functions implement the new table-extraction path that lets CLI-only
    mode (no API key) process Amex HK PDFs with foreign currency entries.
    No Claude, no API key required.
    """

    @pytest.fixture(scope="class")
    def extraction(self, amex_bytes):
        """Run _extract_docling_tables() and return (transactions, metadata) tuple."""
        rows, meta = _ext_intel._extract_docling_tables(amex_bytes)
        print(f"\n[docling_tables] rows extracted: {len(rows)}")
        for r in rows:
            print(f"  {r['date']:12} | {r['description'][:40]:40} | HKD {r['hkd_amount']:>10,.2f}")
        print(f"\n[docling_tables] metadata: {meta}")
        return rows, meta

    @pytest.fixture(scope="class")
    def table_rows(self, extraction):
        return extraction[0]

    @pytest.fixture(scope="class")
    def table_meta(self, extraction):
        return extraction[1]

    def test_extracts_multiple_transactions(self, table_rows):
        # April 2026 AE statement has 12 charge transactions; we expect at least 9
        # (some FX rows with compressed dates like "March3" may vary by Docling version)
        assert len(table_rows) >= 9, (
            f"Expected ≥9 transaction rows, got {len(table_rows)}"
        )

    def test_no_autopay_row(self, table_rows):
        """The autopay CR payment row must be excluded."""
        for row in table_rows:
            assert 'AUTOPAY' not in row['description'].upper(), (
                f"Autopay CR row was not filtered: {row}"
            )

    def test_no_cr_row(self, table_rows):
        """No negative or zero HKD amounts — CR rows must be filtered."""
        for row in table_rows:
            assert row['hkd_amount'] > 0, (
                f"Non-positive HKD amount found (CR row not filtered?): {row}"
            )

    def test_hkd_amounts_are_reasonable(self, table_rows):
        """All HKD amounts should be in per-transaction scale (> 1, < 10,000)."""
        for row in table_rows:
            assert 1 < row['hkd_amount'] < 10_000, (
                f"Amount out of reasonable HKD transaction range: {row}"
            )

    def test_all_rows_have_date(self, table_rows):
        for row in table_rows:
            assert _MONTH_RE.search(row['date']), (
                f"Row missing a valid month-name date: {row}"
            )

    def test_all_rows_have_description(self, table_rows):
        for row in table_rows:
            assert len(row['description']) >= 3, (
                f"Description too short or empty: {row}"
            )

    def test_known_transactions_present(self, table_rows):
        """Spot-check merchants confirmed present in the April 2026 AE statement."""
        descs = [r['description'].upper() for r in table_rows]
        # These merchants appear in rows with normal date formatting — reliably extracted
        assert any('NETFLIX' in d for d in descs), "NETFLIX transaction not found"
        assert any('FIREFLIES' in d for d in descs), "FIREFLIES.AI transaction not found"
        assert any('LOOM' in d for d in descs), "LOOM transaction not found"

    def test_no_ctiu_sequences_in_descriptions(self, table_rows):
        """Docling /CTIUxxxx escape sequences must be cleaned from descriptions."""
        for row in table_rows:
            assert '/CTIU' not in row['description'], (
                f"Raw /CTIU sequence found in description: {row}"
            )

    # ── Metadata tests ────────────────────────────────────────────────────────

    def test_metadata_cardholder_extracted(self, table_meta):
        """Cardholder name must be extracted from the xxxx-xxxxxx-NNNNN row."""
        cardholder = table_meta.get("cardholder")
        assert cardholder, f"cardholder not extracted: {table_meta}"
        assert "EIKO" in cardholder.upper(), (
            f"Expected cardholder to contain 'EIKO', got: {cardholder!r}"
        )

    def test_metadata_account_suffix_extracted(self, table_meta):
        """Account suffix (last 5 digits) must be extracted."""
        suffix = table_meta.get("account_suffix")
        assert suffix, f"account_suffix not extracted: {table_meta}"
        assert suffix.isdigit(), f"account_suffix should be numeric digits: {suffix!r}"

    def test_metadata_statement_total_extracted(self, table_meta):
        """Statement total due must be extracted from the footer row."""
        total = table_meta.get("statement_total")
        assert total is not None, f"statement_total not extracted: {table_meta}"
        # April 2026 AE statement total is 5,258.61 HKD
        assert abs(total - 5258.61) < 1.0, (
            f"statement_total={total} differs from expected ~5258.61"
        )

    def test_metadata_autopay_amount_extracted(self, table_meta):
        """Autopay payment amount must be extracted from the AUTOPAY CR row."""
        autopay = table_meta.get("autopay_amount")
        assert autopay is not None, f"autopay_amount not extracted: {table_meta}"
        # April 2026 AE autopay amount is 42,453.91 HKD
        assert abs(autopay - 42453.91) < 1.0, (
            f"autopay_amount={autopay} differs from expected ~42453.91"
        )

    def test_formatted_text_is_clean(self, table_rows, table_meta):
        text = _ext_intel._format_table_transactions(table_rows, table_meta)
        print(f"\n[docling_tables] formatted text:\n{text}")
        assert "HKD Amount" in text, "Missing HKD Amount column header"
        assert "| Date |" in text, "Missing Date column header"
        assert len(text) < 8_000, (
            f"Formatted text ({len(text)} chars) exceeds CLI 8K limit"
        )
        assert '/CTIU' not in text, "Raw /CTIU sequences in formatted output"
        assert '7.980' not in text, "Raw FX exchange rate leaked into formatted output"

    def test_formatted_text_includes_metadata(self, table_rows, table_meta):
        """Formatted preamble must include cardholder, statement total, autopay."""
        text = _ext_intel._format_table_transactions(table_rows, table_meta)
        assert "Cardholder:" in text, "Cardholder line missing from formatted output"
        assert "Statement total due:" in text, "Statement total missing from formatted output"
        assert "autopay" in text.lower(), "Autopay amount missing from formatted output"
