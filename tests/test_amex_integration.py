"""
Integration test — real Anthropic API call with actual Amex PDF.

Requires: ANTHROPIC_API_KEY set in environment (or .env in deployment dir).

This test makes a LIVE API call to verify the credit_card extraction schema
actually causes Claude to:
  1. Exclude the CR (autopay) transaction
  2. Return HKD amounts (not USD)
  3. Produce a total close to the statement balance of HKD 5,258.61

Skip with: pytest -m "not integration"
Run with:  pytest tests/test_amex_integration.py -v -s
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load modules
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
    import importlib.util
    if full_name in sys.modules:
        return sys.modules[full_name]
    _setup_pkg()
    filename = full_name.split(".")[-1] + ".py"
    path = Path(__file__).parent.parent / "simply_connect" / filename
    spec = importlib.util.spec_from_file_location(full_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


_bk = _load_direct("simply_connect.backends")

_SMB_EXT_DIR = Path(__file__).parent.parent.parent / "deployments" / "save-my-brain" / "extension"


def _load_smb(name: str):
    import importlib.util
    full_name = f"smb_ext.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    path = _SMB_EXT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(full_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


_schemas = _load_smb("schemas")

# ---------------------------------------------------------------------------
# Load .env so ANTHROPIC_API_KEY is available when running standalone
# ---------------------------------------------------------------------------

def _load_env():
    env_path = Path(__file__).parent.parent.parent / "deployments" / "save-my-brain" / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=str(env_path), override=True)
        except ImportError:
            pass


_load_env()

AMEX_PDF_PATH = Path("/Users/eiko/Downloads/2026-04-02.pdf")
STATEMENT_TOTAL = 5258.61  # 新簽賬項總額 printed on the statement


# ---------------------------------------------------------------------------
# Integration tests — real API calls
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestAmexRealAPIExtraction:
    """Live API tests. Each test makes one real Anthropic API call.

    These tests verify that the credit_card schema instructions are followed
    by the actual model — mocked tests cannot catch this.
    """

    @pytest.fixture(autouse=True)
    def require_api_key_and_pdf(self):
        if not os.getenv("ANTHROPIC_API_KEY", "").strip():
            pytest.skip("ANTHROPIC_API_KEY not set")
        if not AMEX_PDF_PATH.exists():
            pytest.skip(f"Amex PDF not found at {AMEX_PDF_PATH}")

    @pytest.fixture(scope="class")
    def extracted(self):
        """Run the extraction once and share across all tests in this class."""
        _load_env()
        backend = _bk.AnthropicBackend()
        pdf_bytes = AMEX_PDF_PATH.read_bytes()
        schema = _schemas.EXTRACTION_SCHEMAS["credit_card"]

        system = (
            "You are a document intelligence AI. Extract structured data from the "
            "provided document exactly as instructed by the schema. Return ONLY valid "
            "JSON with no markdown fences."
        )
        prompt = f"Extract transactions from this credit card statement using this schema:\n{schema}"

        raw = backend.complete_vision(
            system=system,
            file_bytes=pdf_bytes,
            mime_type="application/pdf",
            prompt=prompt,
            model=_schemas.SONNET,
            max_tokens=8192,
        )

        print(f"\n--- Raw API response (first 500 chars) ---\n{raw[:500]}\n---")

        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        return json.loads(text)

    def test_response_is_valid_json(self, extracted):
        """API response must be valid JSON."""
        assert isinstance(extracted, dict), f"Expected dict, got {type(extracted)}"

    def test_transactions_field_present(self, extracted):
        """Extracted result must contain a transactions list."""
        assert "transactions" in extracted, f"No 'transactions' key in: {list(extracted.keys())}"
        assert isinstance(extracted["transactions"], list)

    def test_cr_transaction_excluded(self, extracted):
        """PAYMENT RECEIVED THROUGH AUTOPAY (CR) must NOT be in transactions."""
        transactions = extracted["transactions"]
        autopay = [
            t for t in transactions
            if "AUTOPAY" in t.get("merchant", "").upper()
            or "PAYMENT RECEIVED" in t.get("merchant", "").upper()
            or "PAYMENT RECEIVED" in t.get("description", "").upper()
        ]
        assert len(autopay) == 0, (
            f"CR autopay transaction must be excluded from results.\n"
            f"Found: {autopay}\n"
            f"Schema instruction is not being followed by the model."
        )

    def test_no_large_positive_amount(self, extracted):
        """No transaction should have amount > 10,000 (would indicate CR was included)."""
        transactions = extracted["transactions"]
        large = [t for t in transactions if float(t.get("amount", 0)) > 10000]
        assert len(large) == 0, (
            f"Large positive amount found — likely CR autopay (42,453) was included:\n{large}"
        )

    def test_canva_is_hkd_not_usd(self, extracted):
        """Canva charge must be HKD 1,196.19, not USD 149.90 (two-column bug)."""
        transactions = extracted["transactions"]
        canva = next(
            (t for t in transactions if "CANVA" in t.get("merchant", "").upper()), None
        )
        assert canva is not None, "CANVA transaction not found in extracted list"
        amount = abs(float(canva.get("amount", 0)))
        assert amount > 500, (
            f"Canva amount {amount} looks like USD (149.90) not HKD (1,196.19). "
            "Vision mode should return the HKD column."
        )
        print(f"\nCanva amount extracted: {amount} (expected ~1196.19 HKD)")

    def test_total_close_to_statement_balance(self, extracted):
        """Sum of absolute transaction amounts must be within 10% of statement total 5,258.61."""
        transactions = extracted["transactions"]
        assert len(transactions) > 0, "No transactions extracted"
        total = sum(abs(float(t.get("amount", 0))) for t in transactions)
        tolerance = STATEMENT_TOTAL * 0.10  # 10% tolerance for partial/rounding
        assert abs(total - STATEMENT_TOTAL) <= tolerance, (
            f"Total {total:.2f} is not within 10% of statement balance {STATEMENT_TOTAL}.\n"
            f"If total >> {STATEMENT_TOTAL}: CR transaction was included.\n"
            f"If total << {STATEMENT_TOTAL}: some charges were missed.\n"
            f"Transactions: {[(t.get('merchant','?'), t.get('amount',0)) for t in transactions]}"
        )
        print(f"\nExtracted total: {total:.2f} HKD (statement: {STATEMENT_TOTAL})")

    def test_transaction_count_reasonable(self, extracted):
        """Should have between 10 and 15 transactions (statement has 12 charges)."""
        transactions = extracted["transactions"]
        count = len(transactions)
        assert 10 <= count <= 15, (
            f"Expected ~12 transactions, got {count}.\n"
            f"If 13: CR autopay was included. If <10: charges were missed.\n"
            f"Merchants: {[t.get('merchant', '?') for t in transactions]}"
        )
        print(f"\nTransaction count: {count} (expected 12)")

    def test_all_amounts_are_negative_or_small_positive(self, extracted):
        """All spending should be negative. Any large positive = CR included."""
        transactions = extracted["transactions"]
        for t in transactions:
            amount = float(t.get("amount", 0))
            assert amount < 1000, (
                f"Unexpected large positive amount {amount} for '{t.get('merchant', '?')}'. "
                "Spending should be negative; CR transactions should be excluded."
            )
