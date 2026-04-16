"""
Tests for CR (credit) transaction exclusion in credit_card statement processing.

Root cause of the inflated total bug:
  "PAYMENT RECEIVED THROUGH AUTOPAY 42,453.91 CR" was extracted as a regular
  transaction and stored in smb_transactions. This inflated the total spend
  from ~5,258.61 to ~47,712.52.

Three layers of coverage:
  1. Schema text — verify the extraction schema instructs Claude to EXCLUDE CR lines
  2. Pipeline behaviour — with CR excluded, sum matches statement total; with CR
     included (old bug), sum is inflated
  3. Database layer — insert_transactions sets is_income correctly for positive/negative
     amounts, and transaction count is as expected when CR is excluded

Background:
  Amex HK statements show "CR" suffix on payments/credits:
    "March 12  PAYMENT RECEIVED THROUGH AUTOPAY  42,453.91 CR"
  These are NOT expenses — they are payments clearing the previous balance.
  They must not appear in the transactions array sent to the DB.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Load simply_connect.intelligence directly (bypass __init__.py Python 3.9)
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


_bk    = _load_direct("simply_connect.backends")
_intel = _load_direct("simply_connect.intelligence")


# ---------------------------------------------------------------------------
# Load SMB extension modules directly from deployment path
# ---------------------------------------------------------------------------

_SMB_EXT_DIR = Path(__file__).parent.parent.parent / "deployments" / "save-my-brain" / "extension"


def _load_smb(name: str):
    full_name = f"smb_ext.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    path = _SMB_EXT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(full_name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


_schemas  = _load_smb("schemas")
_database = _load_smb("database")


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

# Realistic Amex HK statement — 12 charge lines + 1 CR (autopay) line
# Amounts: HKD column (right) from a two-column statement layout
AMEX_TRANSACTIONS_WITH_CR = [
    # This is the autopay credit — must be EXCLUDED
    {"date": "2026-03-12", "amount": 42453.91,  "merchant": "PAYMENT RECEIVED THROUGH AUTOPAY", "category": "transfer",     "description": "PAYMENT RECEIVED THROUGH AUTOPAY 42,453.91 CR"},
    # These are the actual charges — must be included
    {"date": "2026-03-03", "amount": -1196.19,  "merchant": "CANVA SURRY HILLS",                "category": "subscription", "description": "CANVA SURRY HILLS 1,196.19"},
    {"date": "2026-03-06", "amount": -398.98,   "merchant": "BASE44 NEW YORK",                  "category": "other",        "description": "BASE44 NEW YORK 398.98"},
    {"date": "2026-03-07", "amount": -73.00,    "merchant": "NETFLIX.COM",                      "category": "subscription", "description": "NETFLIX.COM 73.00"},
    {"date": "2026-03-10", "amount": -62.51,    "merchant": "CKO*PATREON* MEMBERSHIP",          "category": "subscription", "description": "CKO*PATREON* 62.51"},
    {"date": "2026-03-13", "amount": -1820.52,  "merchant": "FIREFLIES.AI",                     "category": "subscription", "description": "FIREFLIES.AI 1,820.52"},
    {"date": "2026-03-14", "amount": -39.92,    "merchant": "FIREFLIES.AI",                     "category": "subscription", "description": "FIREFLIES.AI 39.92"},
    {"date": "2026-03-17", "amount": -124.22,   "merchant": "CANVA SURRY HILLS",                "category": "subscription", "description": "CANVA SURRY HILLS 124.22"},
    {"date": "2026-03-17", "amount": -47.18,    "merchant": "OPENAI *CHATGPT",                  "category": "subscription", "description": "OPENAI *CHATGPT 47.18"},
    {"date": "2026-03-18", "amount": -220.74,   "merchant": "ANTHROPIC",                        "category": "subscription", "description": "ANTHROPIC 220.74"},
    {"date": "2026-03-20", "amount": -376.53,   "merchant": "AMAZON WEB SERVICES",              "category": "subscription", "description": "AMAZON WEB SERVICES 376.53"},
    {"date": "2026-03-21", "amount": -399.00,   "merchant": "ADOBE SYSTEMS",                    "category": "subscription", "description": "ADOBE SYSTEMS 399.00"},
    {"date": "2026-03-22", "amount": -499.82,   "merchant": "CURSOR AI",                        "category": "subscription", "description": "CURSOR AI 499.82"},
]

# Expected: only charges (no CR), summed as positive expenses
AMEX_CHARGES_ONLY = [t for t in AMEX_TRANSACTIONS_WITH_CR if t["amount"] < 0]
AMEX_STATEMENT_TOTAL = 5258.61  # printed on the statement as 新簽賬項總額

# What the old (broken) LLM extraction returned — CR included as an expense
OLD_EXTRACTION_RESPONSE = json.dumps({
    "summary": "March 2026 statement. Total new charges: HKD 5,258.61.",
    "transactions": AMEX_TRANSACTIONS_WITH_CR,
    "key_points": [],
    "important_dates": [],
    "red_flags": [],
    "action_items": [],
    "statement_total": -5258.61,
})

# What a correctly-instructed LLM returns — CR excluded
CORRECT_EXTRACTION_RESPONSE = json.dumps({
    "summary": "March 2026 statement. Total new charges: HKD 5,258.61.",
    "transactions": AMEX_CHARGES_ONLY,
    "key_points": [],
    "important_dates": [],
    "red_flags": [],
    "action_items": [],
    "statement_total": -5258.61,
})

AMEX_CLASSIFY = json.dumps({
    "doc_type": "credit_card",
    "detected_names": ["EIKO ONISHI"],
    "document_language": "zh",
    "complexity": "complex",
    "brief_description": "Amex HK credit card statement March 2026",
    "currency": "HKD",
})

MINIMAL_SCHEMAS = {
    "classify_schema":           '{"doc_type": "credit_card|other", "currency": "HKD|USD|null"}',
    "extraction_schemas":        {"credit_card": _schemas.EXTRACTION_SCHEMAS["credit_card"]},
    "default_extraction_schema": '{"summary": "", "key_points": []}',
    "complex_doc_types":         {"credit_card"},
    "haiku_model":               "claude-haiku-4-5",
    "sonnet_model":              "claude-sonnet-4-5",
}


def _make_eyes_result(text: str = "fake text") -> MagicMock:
    r = MagicMock()
    r.text = text
    r.method = "pymupdf"
    r.is_scanned = False
    return r


def _make_backend(vision_returns=None, complete_returns=None) -> MagicMock:
    b = MagicMock(spec=_bk.LLMBackend)
    b.name.return_value = "mock"
    b.is_available.return_value = True
    b.supports_vision.return_value = True
    if vision_returns:
        b.complete_vision.side_effect = vision_returns
    if complete_returns:
        b.complete.side_effect = complete_returns
    return b


# ---------------------------------------------------------------------------
# 1. Schema text: CR exclusion and HKD preference instructions are present
# ---------------------------------------------------------------------------

class TestCreditCardSchemaInstructions:
    """The credit_card extraction schema must instruct Claude to:
      a) exclude CR (credit) transactions
      b) prefer HKD amounts in two-column statements
    """

    def _schema(self) -> str:
        return _schemas.EXTRACTION_SCHEMAS["credit_card"]

    def test_schema_excludes_cr_transactions(self):
        """Schema must explicitly tell Claude to exclude CR-marked lines."""
        schema = self._schema()
        assert "CR" in schema, (
            "credit_card schema must mention CR to instruct Claude to exclude credit lines"
        )
        assert "EXCLUDE" in schema.upper() or "exclude" in schema.lower(), (
            "credit_card schema must explicitly say to EXCLUDE CR transactions"
        )

    def test_schema_mentions_payment_received(self):
        """Schema should reference payment/credit lines so Claude recognises them."""
        schema = self._schema()
        assert "payment" in schema.lower() or "credit" in schema.lower(), (
            "Schema should mention 'payment' or 'credit' to help Claude identify CR lines"
        )

    def test_schema_instructs_hkd_preference(self):
        """Schema must instruct Claude to use HKD (local) amount, not foreign currency."""
        schema = self._schema()
        assert "HKD" in schema or "local currency" in schema.lower(), (
            "credit_card schema must instruct Claude to use the HKD (local) column amount"
        )

    def test_schema_does_not_say_extract_all_transactions(self):
        """The old 'Extract ALL transactions' instruction included CR lines — must be removed."""
        schema = self._schema()
        assert "Extract ALL transactions" not in schema, (
            "Old instruction 'Extract ALL transactions' caused CR to be included. "
            "It was replaced by the CR exclusion rule."
        )

    def test_spending_amounts_are_negative(self):
        """Schema must still instruct Claude to use NEGATIVE amounts for spending."""
        schema = self._schema()
        assert "NEGATIVE" in schema or "negative" in schema, (
            "Schema must instruct Claude to use NEGATIVE amounts for spending transactions"
        )


# ---------------------------------------------------------------------------
# 2. Pipeline behaviour: CR excluded → sum matches statement; CR included → inflated
# ---------------------------------------------------------------------------

class TestCRTransactionExclusionPipeline:
    """End-to-end pipeline tests verifying the CR exclusion fix.

    Tests use vision mode (SC_FORCE_VISION=1) because that's the deployment config
    for the Amex statement (two-column PDF). The LLM response is mocked.
    """

    def _run_pipeline(self, extraction_response: str) -> dict:
        b = _make_backend(vision_returns=[AMEX_CLASSIFY, extraction_response])
        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = _make_eyes_result()
        eyes_mod.has_enough_text.return_value = True

        with patch.object(_intel, "_eyes_module", eyes_mod):
            return _intel.process_document(
                b"fake-pdf", "2026-04-02.pdf", "application/pdf",
                MINIMAL_SCHEMAS, force_vision=True, backend=b,
            )

    def test_cr_excluded_sum_matches_statement_total(self):
        """When CR is excluded, |sum of transactions| ≈ statement total 5,258.61."""
        result = self._run_pipeline(CORRECT_EXTRACTION_RESPONSE)
        transactions = result.get("transactions", [])
        total = sum(abs(t["amount"]) for t in transactions)
        assert abs(total - AMEX_STATEMENT_TOTAL) < 1.0, (
            f"Sum of charges should be ~{AMEX_STATEMENT_TOTAL}, got {total:.2f}. "
            "CR transaction may still be included."
        )

    def test_cr_included_sum_is_inflated(self):
        """Documents old bug: with CR included, sum is ~47,712 instead of ~5,258."""
        result = self._run_pipeline(OLD_EXTRACTION_RESPONSE)
        transactions = result.get("transactions", [])
        total = sum(abs(t["amount"]) for t in transactions)
        assert total > 40000, (
            f"With CR included, total should be inflated (>40,000), got {total:.2f}"
        )

    def test_cr_excluded_transaction_count_is_twelve(self):
        """With CR excluded, exactly 12 charge transactions should be extracted."""
        result = self._run_pipeline(CORRECT_EXTRACTION_RESPONSE)
        transactions = result.get("transactions", [])
        assert len(transactions) == 12, (
            f"Expected 12 charge transactions (CR excluded), got {len(transactions)}"
        )

    def test_cr_included_transaction_count_is_thirteen(self):
        """Documents old bug: with CR included, 13 transactions (12 charges + 1 CR payment)."""
        result = self._run_pipeline(OLD_EXTRACTION_RESPONSE)
        transactions = result.get("transactions", [])
        assert len(transactions) == 13, (
            f"With CR included, expected 13 transactions, got {len(transactions)}"
        )

    def test_cr_excluded_no_autopay_transaction(self):
        """With CR excluded, no 'PAYMENT RECEIVED THROUGH AUTOPAY' in transactions."""
        result = self._run_pipeline(CORRECT_EXTRACTION_RESPONSE)
        transactions = result.get("transactions", [])
        autopay = [t for t in transactions if "AUTOPAY" in t.get("merchant", "").upper()]
        assert len(autopay) == 0, (
            f"AUTOPAY CR transaction must not be in extracted list, found: {autopay}"
        )

    def test_cr_excluded_no_large_positive_amount(self):
        """With CR excluded, no transaction should have a large positive amount (42k+)."""
        result = self._run_pipeline(CORRECT_EXTRACTION_RESPONSE)
        transactions = result.get("transactions", [])
        large_positive = [t for t in transactions if t.get("amount", 0) > 10000]
        assert len(large_positive) == 0, (
            f"No transaction should have amount >10,000 after CR exclusion. Found: {large_positive}"
        )


# ---------------------------------------------------------------------------
# 3. Database layer: insert_transactions is_income flag and count
# ---------------------------------------------------------------------------

class TestInsertTransactionsIsIncomeFlag:
    """insert_transactions() in extension/database.py must correctly set is_income:
      - Negative amounts (expenses) → is_income = 0
      - Positive amounts (income/credits) → is_income = 1
      - CR transaction at 42,453.91 should never reach here (schema fix),
        but if it did, it would be stored as is_income=1 (positive amount)
        AND would massively inflate the stored total.
    """

    def _make_connection_manager(self, tmp_path: Path) -> object:
        """Minimal connection manager pointing at a temp SQLite database."""
        db_path = tmp_path / "test.db"
        cm = MagicMock()
        cm.db_path = str(db_path)
        return cm

    def _init_db(self, cm) -> None:
        conn = sqlite3.connect(cm.db_path)
        conn.executescript(_database.SCHEMA)
        conn.commit()
        conn.close()

    def _read_transactions(self, cm, doc_id: int) -> list[dict]:
        conn = sqlite3.connect(cm.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM smb_transactions WHERE document_id = ?", (doc_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _insert_doc(self, cm) -> int:
        conn = sqlite3.connect(cm.db_path)
        cur = conn.execute(
            "INSERT INTO smb_documents (filename, doc_type, summary) VALUES (?, ?, ?)",
            ("test.pdf", "credit_card", "test")
        )
        doc_id = cur.lastrowid
        conn.commit()
        conn.close()
        return doc_id

    # Patch get_connection to use our temp db path
    def _patched_get_connection(self, cm):
        return sqlite3.connect(cm.db_path)

    def test_negative_amounts_stored_as_expenses(self, tmp_path):
        """Negative amounts (charges) must be stored as abs(amount), is_income=0."""
        cm = self._make_connection_manager(tmp_path)
        self._init_db(cm)
        doc_id = self._insert_doc(cm)

        transactions = [
            {"date": "2026-03-03", "amount": -1196.19, "merchant": "CANVA",    "category": "subscription"},
            {"date": "2026-03-07", "amount": -73.00,   "merchant": "NETFLIX",  "category": "subscription"},
        ]

        with patch.object(_database, "get_connection", self._patched_get_connection):
            count = _database.insert_transactions(cm, doc_id, transactions)

        assert count == 2
        rows = self._read_transactions(cm, doc_id)
        for row in rows:
            assert row["is_income"] == 0, f"Expense must have is_income=0, got {row}"
            assert row["amount"] > 0, f"Amount stored as abs(), must be positive, got {row}"

    def test_positive_amount_stored_as_income(self, tmp_path):
        """Positive amounts are flagged as income (is_income=1)."""
        cm = self._make_connection_manager(tmp_path)
        self._init_db(cm)
        doc_id = self._insert_doc(cm)

        transactions = [
            {"date": "2026-03-12", "amount": 42453.91, "merchant": "PAYMENT RECEIVED", "category": "transfer"},
        ]

        with patch.object(_database, "get_connection", self._patched_get_connection):
            count = _database.insert_transactions(cm, doc_id, transactions)

        assert count == 1
        rows = self._read_transactions(cm, doc_id)
        assert rows[0]["is_income"] == 1, (
            f"Positive amount (autopay CR) stored as is_income=1, got {rows[0]}"
        )

    def test_cr_transaction_inflates_total_if_stored(self, tmp_path):
        """Documents the bug: if CR is stored, sum(amount) = 47,712 not 5,258."""
        cm = self._make_connection_manager(tmp_path)
        self._init_db(cm)
        doc_id = self._insert_doc(cm)

        all_transactions_including_cr = [
            {"date": "2026-03-12", "amount": 42453.91,  "merchant": "PAYMENT RECEIVED THROUGH AUTOPAY", "category": "transfer"},
            {"date": "2026-03-03", "amount": -1196.19,  "merchant": "CANVA",     "category": "subscription"},
            {"date": "2026-03-06", "amount": -398.98,   "merchant": "BASE44",    "category": "other"},
            {"date": "2026-03-07", "amount": -73.00,    "merchant": "NETFLIX",   "category": "subscription"},
            {"date": "2026-03-10", "amount": -62.51,    "merchant": "PATREON",   "category": "subscription"},
            {"date": "2026-03-13", "amount": -1820.52,  "merchant": "FIREFLIES", "category": "subscription"},
        ]

        with patch.object(_database, "get_connection", self._patched_get_connection):
            _database.insert_transactions(cm, doc_id, all_transactions_including_cr)

        rows = self._read_transactions(cm, doc_id)
        stored_total = sum(r["amount"] for r in rows)
        assert stored_total > 40000, (
            f"When CR is included, stored total is inflated to {stored_total:.2f}. "
            "This is the bug the schema fix prevents."
        )

    def test_charges_only_total_matches_statement(self, tmp_path):
        """With CR excluded (schema fix in effect), stored total ≈ statement total."""
        cm = self._make_connection_manager(tmp_path)
        self._init_db(cm)
        doc_id = self._insert_doc(cm)

        charges_only = [
            {"date": "2026-03-03", "amount": -1196.19,  "merchant": "CANVA",     "category": "subscription"},
            {"date": "2026-03-06", "amount": -398.98,   "merchant": "BASE44",    "category": "other"},
            {"date": "2026-03-07", "amount": -73.00,    "merchant": "NETFLIX",   "category": "subscription"},
            {"date": "2026-03-10", "amount": -62.51,    "merchant": "PATREON",   "category": "subscription"},
            {"date": "2026-03-13", "amount": -1820.52,  "merchant": "FIREFLIES", "category": "subscription"},
            {"date": "2026-03-14", "amount": -39.92,    "merchant": "FIREFLIES", "category": "subscription"},
            {"date": "2026-03-17", "amount": -124.22,   "merchant": "CANVA",     "category": "subscription"},
            {"date": "2026-03-17", "amount": -47.18,    "merchant": "OPENAI",    "category": "subscription"},
            {"date": "2026-03-18", "amount": -220.74,   "merchant": "ANTHROPIC", "category": "subscription"},
            {"date": "2026-03-20", "amount": -376.53,   "merchant": "AWS",       "category": "subscription"},
            {"date": "2026-03-21", "amount": -399.00,   "merchant": "ADOBE",     "category": "subscription"},
            {"date": "2026-03-22", "amount": -499.82,   "merchant": "CURSOR AI", "category": "subscription"},
        ]

        with patch.object(_database, "get_connection", self._patched_get_connection):
            count = _database.insert_transactions(cm, doc_id, charges_only)

        assert count == 12
        rows = self._read_transactions(cm, doc_id)
        stored_total = sum(r["amount"] for r in rows)
        assert abs(stored_total - AMEX_STATEMENT_TOTAL) < 1.0, (
            f"Stored total should be ~{AMEX_STATEMENT_TOTAL} (statement balance), "
            f"got {stored_total:.2f}"
        )

    def test_all_stored_transactions_are_expenses(self, tmp_path):
        """After schema fix, all stored transactions should be expenses (is_income=0)."""
        cm = self._make_connection_manager(tmp_path)
        self._init_db(cm)
        doc_id = self._insert_doc(cm)

        charges_only = [
            {"date": "2026-03-03", "amount": -1196.19, "merchant": "CANVA",     "category": "subscription"},
            {"date": "2026-03-07", "amount": -73.00,   "merchant": "NETFLIX",   "category": "subscription"},
            {"date": "2026-03-13", "amount": -1820.52, "merchant": "FIREFLIES", "category": "subscription"},
        ]

        with patch.object(_database, "get_connection", self._patched_get_connection):
            _database.insert_transactions(cm, doc_id, charges_only)

        rows = self._read_transactions(cm, doc_id)
        income_rows = [r for r in rows if r["is_income"] == 1]
        assert len(income_rows) == 0, (
            f"No rows should be flagged is_income=1 for charge-only data, found: {income_rows}"
        )
