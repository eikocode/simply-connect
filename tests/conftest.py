"""
Root conftest.py — pytest configuration for simply-connect test suite.

Registers the `e2e` marker and adds the `--e2e` CLI flag.
E2E tests are skipped unless `--e2e` is passed:

    pytest tests/                     # unit tests only (fast, no API calls)
    pytest tests/e2e/ --e2e           # live e2e tests (real API, real PDFs)
    pytest tests/ --e2e               # everything
"""

import os
import pytest
from dotenv import dotenv_values
from pathlib import Path

# Load .env so ANTHROPIC_API_KEY and SC_* vars are available to e2e tests.
# Probe several candidate locations in priority order.
# Uses dotenv_values() + manual os.environ assignment so that:
#   - Empty shell vars (ANTHROPIC_API_KEY="") get filled from .env files
#   - Non-empty shell vars are never overwritten
_ENV_CANDIDATES = [
    # simply-connect project root (for standalone deployments)
    Path(__file__).parent.parent / ".env",
    # save-my-brain app inside aios-starter-kit (primary key location)
    Path(__file__).parent.parent.parent / "aios-starter-kit" / "apps" / "save-my-brain" / ".env",
    # aios-starter-kit root
    Path(__file__).parent.parent.parent / "aios-starter-kit" / ".env",
    # deployments path (alternate layout)
    Path(__file__).parent.parent.parent / "deployments" / "save-my-brain" / ".env",
    # generic parent-dir fallback
    Path(__file__).parent.parent.parent / ".env",
]
for _p in _ENV_CANDIDATES:
    if _p.exists():
        for _k, _v in dotenv_values(_p).items():
            # Only set if not already present with a non-empty value
            if _v and not os.environ.get(_k):
                os.environ[_k] = _v


def pytest_addoption(parser):
    parser.addoption(
        "--e2e",
        action="store_true",
        default=False,
        help="Run live end-to-end tests (requires API key and fixture PDFs)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "e2e: live end-to-end test — requires ANTHROPIC_API_KEY and fixture PDFs",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--e2e"):
        skip_e2e = pytest.mark.skip(reason="pass --e2e to run live end-to-end tests")
        for item in items:
            if "e2e" in item.keywords:
                item.add_marker(skip_e2e)
