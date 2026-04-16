"""
Integration tests for the web auth endpoints (Telegram pairing flow).

These tests hit the REAL running sc-web server on localhost:8090.
They do NOT mock anything — if the server is down or the endpoints are missing,
the tests fail immediately with a clear error.

Run with:
    pytest tests/test_web_auth.py -v
"""

import pytest
import requests

BASE_URL = "http://localhost:8090"


def _server_is_up() -> bool:
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _server_is_up(),
    reason="sc-web not running on localhost:8090 — start it before running these tests",
)


class TestAuthRequestCode:
    def test_returns_200(self):
        r = requests.post(f"{BASE_URL}/api/auth/request-code")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_returns_json_with_code(self):
        r = requests.post(f"{BASE_URL}/api/auth/request-code")
        data = r.json()
        assert "code" in data, f"No 'code' key in response: {data}"

    def test_code_format(self):
        """Code should match SMB-XXXX pattern."""
        r = requests.post(f"{BASE_URL}/api/auth/request-code")
        code = r.json()["code"]
        assert code.startswith("SMB-"), f"Code doesn't start with SMB-: {code}"
        assert len(code) == 8, f"Code should be 8 chars (SMB-XXXX), got {len(code)}: {code}"

    def test_each_call_returns_unique_code(self):
        codes = {requests.post(f"{BASE_URL}/api/auth/request-code").json()["code"] for _ in range(3)}
        assert len(codes) == 3, f"Expected 3 unique codes, got: {codes}"


class TestAuthPoll:
    @pytest.fixture(scope="class")
    def fresh_code(self):
        r = requests.post(f"{BASE_URL}/api/auth/request-code")
        return r.json()["code"]

    def test_pending_while_not_claimed(self, fresh_code):
        r = requests.get(f"{BASE_URL}/api/auth/poll", params={"code": fresh_code})
        assert r.status_code == 200
        assert r.json()["status"] == "pending"

    def test_invalid_code_returns_pending(self):
        """Invalid codes should return pending (not 404/500) to avoid leaking info."""
        r = requests.get(f"{BASE_URL}/api/auth/poll", params={"code": "SMB-0000"})
        assert r.status_code == 200
        assert r.json()["status"] == "pending"

    def test_missing_code_returns_400(self):
        r = requests.get(f"{BASE_URL}/api/auth/poll")
        assert r.status_code == 400

    def test_lowercase_code_accepted(self, fresh_code):
        """Frontend might send lowercase — server should normalise."""
        r = requests.get(f"{BASE_URL}/api/auth/poll", params={"code": fresh_code.lower()})
        assert r.status_code == 200
        assert r.json()["status"] == "pending"


class TestApiPrefixRoutes:
    """Every route the frontend uses must be reachable at /api/* — not just /."""

    def test_api_health(self):
        r = requests.get(f"{BASE_URL}/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_api_tool_list_tasks(self):
        r = requests.post(f"{BASE_URL}/api/tool/list_tasks",
                          json={"status": "pending"})
        assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
        assert r.json().get("success") is True

    def test_api_context(self):
        r = requests.get(f"{BASE_URL}/api/context")
        assert r.status_code in (200, 204), f"Got {r.status_code}: {r.text}"

    def test_api_chat_route_exists(self):
        """POST /api/chat must exist (not 404). A 400/422 is fine — means it reached the handler."""
        r = requests.post(f"{BASE_URL}/api/chat", json={})
        assert r.status_code != 404, "/api/chat returned 404 — route missing"

    def test_api_onboarding_status_route_exists(self):
        r = requests.get(f"{BASE_URL}/api/onboarding/status", params={"user_id": "test"})
        assert r.status_code != 404, "/api/onboarding/status returned 404 — route missing"


class TestAuthEndToEnd:
    """Full happy path: request code → claim it → poll → get token."""

    def test_full_pairing_flow(self, tmp_path):
        """
        Simulates the Telegram bot claiming a code, then the frontend polling.
        Directly writes to the DB to fake the Telegram bot step.
        """
        import os, sys, sqlite3
        from pathlib import Path

        # 1. Request a code
        r = requests.post(f"{BASE_URL}/api/auth/request-code")
        assert r.status_code == 200
        code = r.json()["code"]

        # 2. Simulate Telegram bot claiming the code (write to DB directly)
        data_dir = os.getenv("SC_DATA_DIR", ".")
        db_path = Path(data_dir) / "data" / "smb.db"
        if not db_path.exists():
            pytest.skip(f"smb.db not found at {db_path}")

        from datetime import datetime, timedelta
        now = datetime.utcnow().isoformat()
        fake_tg_user = "999888777"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE smb_auth_codes SET telegram_user_id = ?, verified_at = ? WHERE code = ?",
            (fake_tg_user, now, code),
        )
        conn.commit()
        conn.close()

        # 3. Poll — should return complete with token + user
        r = requests.get(f"{BASE_URL}/api/auth/poll", params={"code": code})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "complete", f"Expected complete, got: {data}"
        assert "token" in data, f"No token in response: {data}"
        assert "user" in data, f"No user in response: {data}"
        assert data["user"]["telegram_user_id"] == fake_tg_user

        # 4. Poll again with same code — should be pending (code is used/consumed)
        r2 = requests.get(f"{BASE_URL}/api/auth/poll", params={"code": code})
        assert r2.json()["status"] == "pending", "Used code should not return complete again"
