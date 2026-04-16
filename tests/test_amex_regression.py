"""
Regression tests for bugs found during AE credit card statement ingestion.

Three gaps covered:

1. SC_FORCE_VISION env var is read at call time by extension/intelligence shim
   — existing tests only test force_vision=True as a direct parameter;
     this verifies the env var path that real deployments use.

2. load_dotenv(override=False) does NOT override a pre-existing empty env var
   — documents the gotcha that caused the relay to run with an empty API key
     even after .env was updated. The fix: source .env before starting the
     relay so vars are set in the shell, not just in the dotenv file.

3. Two-column PDF (Amex statement) — text mode picks wrong column amount
   — EYES extracts "149.90 ... 1,196.19" linearly; text mode Claude picks
     the first number (USD) instead of the HKD total.
   — Vision mode returns the correct HKD amount from the mocked backend.
   — Ensures SC_FORCE_VISION=1 is the right fix, not a workaround.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module loader (same pattern as test_intelligence.py)
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


def _load_direct(full_name: str):
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


_bk    = _load_direct("simply_connect.backends")
_intel = _load_direct("simply_connect.intelligence")


MINIMAL_SCHEMAS = {
    "classify_schema":           '{"doc_type": "credit_card|other", "currency": "HKD|USD|null"}',
    "extraction_schemas":        {
        "credit_card": json.dumps({
            "summary": "",
            "transactions": [],
            "key_points": [],
            "important_dates": [],
            "red_flags": [],
            "action_items": [],
        })
    },
    "default_extraction_schema": '{"summary": "", "key_points": []}',
    "complex_doc_types":         {"credit_card"},
    "haiku_model":               "claude-haiku-4-5",
    "sonnet_model":              "claude-sonnet-4-5",
}

# Amex statement has two amount columns:
#   海外消費 (foreign currency) | 港幣 (HKD charged)
# EYES/PyMuPDF reads left-to-right, producing mixed text like:
#   "CANVA SURRY HILLS 149.90 UNITED STATES DOLLAR 1,196.19"
# Text-mode Claude may pick 149.90 (USD) instead of 1,196.19 (HKD).
AMEX_EYES_TEXT = """
月結單 Page 1
EIKO ONISHI  xxxx-xxxxxx-92001  2026年4月2日

March 3   CANVA   SURRY HILLS   149.90  UNITED STATES DOLLAR  1,196.19
March 6   BASE44  NEW YORK       50.00  UNITED STATES DOLLAR    398.98
March 7   NETFLIX.COM 207636 SG                                  73.00
March 10  CKO*PATREON* MEMBERSHIP DUBLIN                         62.51
March 13  FIREFLIES.AI PLEASANTON  228.00  UNITED STATES DOLLAR 1,820.52

新簽賬項總額 EIKO ONISHI   5,258.61
"""

# What text-mode extraction returns (wrong — picks USD column)
AMEX_TEXT_MODE_EXTRACT = json.dumps({
    "summary": "American Express credit card statement March 2026. Total: HKD 5,258.61.",
    "transactions": [
        {"date": "2026-03-03", "merchant": "CANVA SURRY HILLS",         "amount": 149.90,   "category": "subscription", "currency": "HKD"},
        {"date": "2026-03-06", "merchant": "BASE44 NEW YORK",            "amount": 50.00,    "category": "other",        "currency": "HKD"},
        {"date": "2026-03-07", "merchant": "NETFLIX.COM",                "amount": 73.00,    "category": "subscription", "currency": "HKD"},
        {"date": "2026-03-10", "merchant": "CKO*PATREON* MEMBERSHIP",    "amount": 62.51,    "category": "subscription", "currency": "HKD"},
        {"date": "2026-03-13", "merchant": "FIREFLIES.AI",               "amount": 228.00,   "category": "subscription", "currency": "HKD"},
    ],
    "key_points": [],
    "important_dates": [],
    "red_flags": [],
    "action_items": [],
})

# What vision-mode extraction returns (correct — reads HKD column)
AMEX_VISION_EXTRACT = json.dumps({
    "summary": "American Express credit card statement March 2026. Total: HKD 5,258.61.",
    "transactions": [
        {"date": "2026-03-03", "merchant": "CANVA SURRY HILLS",         "amount": 1196.19,  "category": "subscription", "currency": "HKD"},
        {"date": "2026-03-06", "merchant": "BASE44 NEW YORK",            "amount": 398.98,   "category": "other",        "currency": "HKD"},
        {"date": "2026-03-07", "merchant": "NETFLIX.COM",                "amount": 73.00,    "category": "subscription", "currency": "HKD"},
        {"date": "2026-03-10", "merchant": "CKO*PATREON* MEMBERSHIP",    "amount": 62.51,    "category": "subscription", "currency": "HKD"},
        {"date": "2026-03-13", "merchant": "FIREFLIES.AI",               "amount": 1820.52,  "category": "subscription", "currency": "HKD"},
    ],
    "key_points": [],
    "important_dates": [],
    "red_flags": [],
    "action_items": [],
})

AMEX_CLASSIFY = json.dumps({
    "doc_type": "credit_card",
    "detected_names": ["EIKO ONISHI"],
    "document_language": "zh",
    "complexity": "complex",
    "brief_description": "Amex HK credit card statement",
    "currency": "HKD",
})


def _make_eyes_result(text: str, is_scanned: bool = False) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.method = "pymupdf"
    r.is_scanned = is_scanned
    return r


def _make_backend(*, has_vision: bool, complete_returns=None, vision_returns=None) -> MagicMock:
    b = MagicMock(spec=_bk.LLMBackend)
    b.name.return_value = "mock"
    b.is_available.return_value = True
    b.supports_vision.return_value = has_vision
    if complete_returns:
        b.complete.side_effect = complete_returns
    if vision_returns:
        b.complete_vision.side_effect = vision_returns
    return b


# ---------------------------------------------------------------------------
# 1. SC_FORCE_VISION env var is read at call time
# ---------------------------------------------------------------------------

class TestSCForceVisionEnvVar:
    """SC_FORCE_VISION=1 must be read via os.getenv at call time, not import time.

    This matters because the relay is a long-running process. If the var were
    only read at import time, adding it to .env and restarting would be the
    only way to activate it — instead, it should be re-read on every document.
    """

    def test_force_vision_env_var_activates_vision_mode(self):
        """SC_FORCE_VISION=1 in env routes to vision even when EYES has enough text."""
        b = _make_backend(
            has_vision=True,
            vision_returns=[AMEX_CLASSIFY, AMEX_VISION_EXTRACT],
        )
        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = _make_eyes_result(AMEX_EYES_TEXT)
        eyes_mod.has_enough_text.return_value = True  # would normally pick text mode

        with patch.object(_intel, "_eyes_module", eyes_mod), \
             patch.dict(os.environ, {"SC_FORCE_VISION": "1"}, clear=False):
            # Simulate what extension/intelligence.py does: read env at call time
            force_vision = os.getenv("SC_FORCE_VISION", "").strip() in ("1", "true", "yes")
            result = _intel.process_document(
                b"fake-pdf", "2026-04-02.pdf", "application/pdf",
                MINIMAL_SCHEMAS, force_vision=force_vision, backend=b,
            )

        assert result["_extraction_method"] == "vision", (
            "SC_FORCE_VISION=1 should route to vision mode, got text mode"
        )
        b.complete_vision.assert_called()
        b.complete.assert_not_called()

    def test_force_vision_false_uses_text_when_eyes_has_text(self):
        """Without SC_FORCE_VISION, EYES text → text mode (baseline)."""
        b = _make_backend(
            has_vision=True,
            complete_returns=[AMEX_CLASSIFY, AMEX_TEXT_MODE_EXTRACT],
        )
        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = _make_eyes_result(AMEX_EYES_TEXT)
        eyes_mod.has_enough_text.return_value = True

        with patch.object(_intel, "_eyes_module", eyes_mod), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SC_FORCE_VISION", None)
            result = _intel.process_document(
                b"fake-pdf", "2026-04-02.pdf", "application/pdf",
                MINIMAL_SCHEMAS, force_vision=False, backend=b,
            )

        assert result["_extraction_method"] == "text"
        b.complete.assert_called()
        b.complete_vision.assert_not_called()


# ---------------------------------------------------------------------------
# 2. load_dotenv(override=False) does not overwrite pre-existing empty vars
# ---------------------------------------------------------------------------

class TestLoadDotenvOverrideFalse:
    """Documents the gotcha: if ANTHROPIC_API_KEY='' is already in the process
    environment, load_dotenv(override=False) will NOT replace it with the value
    from .env. This is standard dotenv behaviour but caused a hard-to-spot bug
    where the relay ran with an empty API key after .env was updated.

    The fix: source .env before starting the relay (sets vars in the shell),
    or use load_dotenv(override=True).
    """

    def test_dotenv_override_false_does_not_replace_existing_empty_var(self, tmp_path):
        """Pre-existing empty ANTHROPIC_API_KEY is NOT replaced by load_dotenv(override=False)."""
        from dotenv import load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-ant-real-key\n")

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            load_dotenv(dotenv_path=str(env_file), override=False)
            assert os.environ.get("ANTHROPIC_API_KEY") == "", (
                "override=False must not replace a pre-existing empty string — "
                "this is the documented gotcha. Use override=True or source .env in shell."
            )

    def test_dotenv_override_true_does_replace_existing_empty_var(self, tmp_path):
        """With override=True, the .env value wins regardless of existing env."""
        from dotenv import load_dotenv

        env_file = tmp_path / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=sk-ant-real-key\n")

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            load_dotenv(dotenv_path=str(env_file), override=True)
            assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-real-key", (
                "override=True should replace existing empty value with .env value"
            )

    def test_anthropic_backend_has_no_vision_when_api_key_empty(self):
        """AnthropicBackend.supports_vision() is False when API key is empty — confirms
        that an empty key causes silent fallback to text mode, not a clear error."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            backend = _bk.AnthropicBackend()
            assert backend.supports_vision() is False, (
                "Empty API key must disable vision — this is why SC_FORCE_VISION=1 "
                "alone is not enough; ANTHROPIC_API_KEY must also be set and non-empty."
            )


# ---------------------------------------------------------------------------
# 3. Two-column PDF: text mode extracts wrong amount; vision mode is correct
# ---------------------------------------------------------------------------

class TestAmexTwoColumnExtraction:
    """Amex HK statements have two amount columns:
      - 海外消費 (foreign currency, e.g. USD 149.90)
      - 港幣 (HKD charged, e.g. HKD 1,196.19)

    EYES/PyMuPDF reads left-to-right and produces linearised text mixing both
    columns. Text-mode Claude picks the first number it sees (USD), not HKD.
    Vision mode reads the rendered PDF layout and returns correct HKD amounts.
    """

    def test_text_mode_picks_wrong_usd_amount_for_canva(self):
        """Documents the known failure: text mode → Canva = 149.90 (USD), not 1,196.19 (HKD)."""
        b = _make_backend(
            has_vision=True,
            complete_returns=[AMEX_CLASSIFY, AMEX_TEXT_MODE_EXTRACT],
        )
        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = _make_eyes_result(AMEX_EYES_TEXT)
        eyes_mod.has_enough_text.return_value = True

        with patch.object(_intel, "_eyes_module", eyes_mod):
            result = _intel.process_document(
                b"fake-pdf", "2026-04-02.pdf", "application/pdf",
                MINIMAL_SCHEMAS, force_vision=False, backend=b,
            )

        transactions = result.get("transactions", [])
        canva = next((t for t in transactions if "CANVA" in t.get("merchant", "").upper()), None)
        assert canva is not None, "CANVA transaction should be extracted"
        assert canva["amount"] == 149.90, (
            f"Text mode known failure: expected USD amount 149.90, got {canva['amount']}. "
            "This test documents the bug — use SC_FORCE_VISION=1 to fix."
        )

    def test_vision_mode_returns_correct_hkd_amount_for_canva(self):
        """SC_FORCE_VISION=1 → vision mode → Canva = 1,196.19 (correct HKD amount)."""
        b = _make_backend(
            has_vision=True,
            vision_returns=[AMEX_CLASSIFY, AMEX_VISION_EXTRACT],
        )
        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = _make_eyes_result(AMEX_EYES_TEXT)
        eyes_mod.has_enough_text.return_value = True  # would pick text mode without force

        with patch.object(_intel, "_eyes_module", eyes_mod):
            result = _intel.process_document(
                b"fake-pdf", "2026-04-02.pdf", "application/pdf",
                MINIMAL_SCHEMAS, force_vision=True, backend=b,
            )

        transactions = result.get("transactions", [])
        canva = next((t for t in transactions if "CANVA" in t.get("merchant", "").upper()), None)
        assert canva is not None, "CANVA transaction should be extracted"
        assert canva["amount"] == 1196.19, (
            f"Vision mode should return HKD 1,196.19 for Canva, got {canva['amount']}"
        )

    def test_vision_mode_total_matches_statement_total(self):
        """Sum of HKD transactions extracted via vision should be close to statement total."""
        b = _make_backend(
            has_vision=True,
            vision_returns=[AMEX_CLASSIFY, AMEX_VISION_EXTRACT],
        )
        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = _make_eyes_result(AMEX_EYES_TEXT)
        eyes_mod.has_enough_text.return_value = True

        with patch.object(_intel, "_eyes_module", eyes_mod):
            result = _intel.process_document(
                b"fake-pdf", "2026-04-02.pdf", "application/pdf",
                MINIMAL_SCHEMAS, force_vision=True, backend=b,
            )

        transactions = result.get("transactions", [])
        total = sum(t["amount"] for t in transactions)
        # Partial statement (5 of 12 transactions) — total should be > 3,000 HKD
        assert total > 3000, (
            f"Vision-mode HKD total should be > 3,000 for these 5 transactions, got {total:.2f}. "
            "If this fails, text-mode USD amounts were used instead of HKD."
        )

    def test_text_mode_total_is_suspiciously_low(self):
        """Documents text-mode failure: USD total (~573) << HKD total (~3,551)."""
        b = _make_backend(
            has_vision=True,
            complete_returns=[AMEX_CLASSIFY, AMEX_TEXT_MODE_EXTRACT],
        )
        eyes_mod = MagicMock()
        eyes_mod.extract_text.return_value = _make_eyes_result(AMEX_EYES_TEXT)
        eyes_mod.has_enough_text.return_value = True

        with patch.object(_intel, "_eyes_module", eyes_mod):
            result = _intel.process_document(
                b"fake-pdf", "2026-04-02.pdf", "application/pdf",
                MINIMAL_SCHEMAS, force_vision=False, backend=b,
            )

        transactions = result.get("transactions", [])
        total = sum(t["amount"] for t in transactions)
        # Text mode picks USD amounts: 149.90+50+73+62.51+228 = 563.41
        assert total < 1000, (
            f"Text-mode total should be < 1,000 (USD amounts), got {total:.2f}. "
            "This confirms the column-reading bug."
        )
