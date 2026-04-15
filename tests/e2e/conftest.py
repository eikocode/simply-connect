"""
E2E conftest — fixture discovery and backend setup for live pipeline tests.

Fixture PDF locations (checked in order):
  1. tests/e2e/fixtures/           — drop files here for local dev
  2. SC_E2E_FIXTURES_DIR env var   — point to any directory (e.g. ~/Downloads)

The test runner auto-discovers PDFs by glob pattern. Tests that need a specific
fixture are skipped if the file is not found — no hard failures for missing files.

To run:
    # Put HSBC statements in tests/e2e/fixtures/ OR set env var:
    export SC_E2E_FIXTURES_DIR=~/Downloads

    pytest tests/e2e/ --e2e -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

def _fixtures_dir() -> Path:
    """Return the fixture directory. Checks env var first, falls back to local."""
    env = os.getenv("SC_E2E_FIXTURES_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).parent / "fixtures"


def _find_pdfs(pattern: str) -> list[Path]:
    """Return all PDFs in the fixture dir matching a glob pattern."""
    d = _fixtures_dir()
    if not d.exists():
        return []
    return sorted(d.glob(pattern))


# ---------------------------------------------------------------------------
# Fixture discovery helpers (used by parametrize in test file)
# ---------------------------------------------------------------------------

def hsbc_statements() -> list[Path]:
    """Return all HSBC statement PDFs found in the fixture directory."""
    return _find_pdfs("*Statement*.pdf") or _find_pdfs("*statement*.pdf")


def receipt_pdfs() -> list[Path]:
    """Return all receipt PDFs found in the fixture directory."""
    return _find_pdfs("*receipt*.pdf") or _find_pdfs("*Receipt*.pdf") or _find_pdfs("*_R.pdf")


def all_pdfs() -> list[Path]:
    """Return every PDF in the fixture directory."""
    return _find_pdfs("*.pdf")


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    d = _fixtures_dir()
    if not d.exists():
        pytest.skip(
            f"Fixture directory not found: {d}\n"
            "Put PDFs in tests/e2e/fixtures/ or set SC_E2E_FIXTURES_DIR"
        )
    return d


@pytest.fixture(scope="session")
def anthropic_backend():
    """Real AnthropicBackend — skips if ANTHROPIC_API_KEY not set."""
    import sys
    import importlib.util
    import types

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set — skipping live Anthropic e2e test")

    # Load backends without triggering __init__.py (Python 3.9 compat)
    if "simply_connect.backends" not in sys.modules:
        pkg = types.ModuleType("simply_connect")
        pkg.__path__ = [
            str(Path(__file__).parent.parent.parent / "simply_connect")
        ]
        pkg.__package__ = "simply_connect"
        sys.modules.setdefault("simply_connect", pkg)
        spec = importlib.util.spec_from_file_location(
            "simply_connect.backends",
            str(Path(__file__).parent.parent.parent / "simply_connect" / "backends.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["simply_connect.backends"] = mod
        spec.loader.exec_module(mod)

    from simply_connect.backends import AnthropicBackend
    return AnthropicBackend()


@pytest.fixture(scope="session")
def intelligence_module():
    """Load simply_connect.intelligence directly (bypasses __init__.py)."""
    import sys
    import importlib.util
    import types

    # Ensure backends loaded first
    if "simply_connect.backends" not in sys.modules:
        pkg = types.ModuleType("simply_connect")
        pkg.__path__ = [
            str(Path(__file__).parent.parent.parent / "simply_connect")
        ]
        pkg.__package__ = "simply_connect"
        sys.modules.setdefault("simply_connect", pkg)

    sc_dir = Path(__file__).parent.parent.parent / "simply_connect"

    for name, filename in [
        ("simply_connect.backends", "backends.py"),
        ("simply_connect.intelligence", "intelligence.py"),
    ]:
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, str(sc_dir / filename))
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)

    return sys.modules["simply_connect.intelligence"]
