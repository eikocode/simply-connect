"""
Tests for relay.py document upload handling.

Covers: photo routing, document routing, mime-type rejection,
download_file, staging entry creation, empty extraction, temp file cleanup.

No live HTTP calls — requests and ingest_document are fully mocked.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Ensure the relay module is imported so patch() can find it by dotted name
import simply_connect.relay  # noqa: F401


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def relay(tmp_path, monkeypatch):
    """TelegramRelay with a fake token; runtime is mocked out."""
    monkeypatch.setenv("SC_TELEGRAM_ALLOWED_USERS", "")
    monkeypatch.setenv("SC_CLAUDE_RUNTIME", "sdk")
    with patch("simply_connect.relay.get_runtime") as mock_rt:
        mock_rt.return_value = MagicMock()
        from simply_connect.relay import TelegramRelay, config
        config.reload()
        r = TelegramRelay("fake-token", role_name="operator")
    return r


def _photo_message(file_id="photo-file-id"):
    return {
        "update_id": 1,
        "message": {
            "chat": {"id": 100},
            "from": {"id": 42},
            "photo": [
                {"file_id": "small-id", "width": 90, "height": 90},
                {"file_id": file_id, "width": 800, "height": 600},
            ],
        },
    }


def _document_message(file_id="doc-file-id", mime_type="application/pdf", file_name="contract.pdf"):
    return {
        "update_id": 2,
        "message": {
            "chat": {"id": 100},
            "from": {"id": 42},
            "document": {
                "file_id": file_id,
                "mime_type": mime_type,
                "file_name": file_name,
            },
        },
    }


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------

class TestHandleMessageRouting:
    def test_photo_message_routes_to_handle_document(self, relay):
        """A message with 'photo' key calls handle_document, not runtime."""
        with patch.object(relay, "handle_document") as mock_hd, \
             patch.object(relay, "send_message"):
            relay.handle_message(_photo_message())

        mock_hd.assert_called_once()
        args = mock_hd.call_args[0]
        assert args[0] == 100   # chat_id
        assert args[1] == 42    # user_id

    def test_document_message_routes_to_handle_document(self, relay):
        """A message with 'document' key calls handle_document."""
        with patch.object(relay, "handle_document") as mock_hd, \
             patch.object(relay, "send_message"):
            relay.handle_message(_document_message())

        mock_hd.assert_called_once()

    def test_text_message_does_not_route_to_handle_document(self, relay):
        """Plain text messages go to runtime.call, not handle_document."""
        update = {
            "update_id": 3,
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "text": "Hello",
            },
        }
        relay.runtime.call.return_value = "Hi there"

        with patch.object(relay, "handle_document") as mock_hd, \
             patch.object(relay, "send_message"):
            relay.handle_message(update)

        mock_hd.assert_not_called()
        relay.runtime.call.assert_called_once()


# ---------------------------------------------------------------------------
# Mime-type rejection
# ---------------------------------------------------------------------------

class TestUnsupportedMimeType:
    def test_unsupported_mime_type_sends_friendly_error(self, relay):
        """Documents with unsupported mime types get a clear error message."""
        msg = _document_message(mime_type="text/csv", file_name="data.csv")
        sent = []

        with patch.object(relay, "send_message", side_effect=lambda cid, txt, **kw: sent.append(txt)), \
             patch.object(relay, "download_file"):
            relay.handle_document(100, 42, msg["message"])

        assert any("Unsupported" in t or "unsupported" in t for t in sent)
        assert any("text/csv" in t for t in sent)

    def test_unsupported_mime_does_not_call_download(self, relay):
        """No download attempt for unsupported mime types."""
        msg = _document_message(mime_type="application/zip")

        with patch.object(relay, "send_message"), \
             patch.object(relay, "download_file") as mock_dl:
            relay.handle_document(100, 42, msg["message"])

        mock_dl.assert_not_called()


# ---------------------------------------------------------------------------
# download_file called with correct file_id
# ---------------------------------------------------------------------------

class TestDownloadFile:
    def test_photo_uses_largest_size_file_id(self, relay):
        """handle_document passes the last (largest) photo file_id to download_file."""
        message = _photo_message(file_id="large-photo-id")["message"]

        ingest_result = {
            "success": True,
            "extractions": [],
            "error": None,
            "parser": "claude",
        }

        with patch.object(relay, "send_message"), \
             patch.object(relay, "send_typing"), \
             patch.object(relay, "download_file", return_value=b"fake-bytes") as mock_dl, \
             patch("simply_connect.ingestion.ingest_document", return_value=ingest_result), \
             patch("simply_connect.context_manager.ContextManager"):
            relay.handle_document(100, 42, message)

        mock_dl.assert_called_once_with("large-photo-id")

    def test_document_uses_document_file_id(self, relay):
        """handle_document passes the document's file_id to download_file."""
        message = _document_message(file_id="pdf-file-xyz")["message"]

        ingest_result = {
            "success": True,
            "extractions": [],
            "error": None,
            "parser": "claude",
        }

        with patch.object(relay, "send_message"), \
             patch.object(relay, "send_typing"), \
             patch.object(relay, "download_file", return_value=b"pdf-bytes") as mock_dl, \
             patch("simply_connect.ingestion.ingest_document", return_value=ingest_result), \
             patch("simply_connect.context_manager.ContextManager"):
            relay.handle_document(100, 42, message)

        mock_dl.assert_called_once_with("pdf-file-xyz")


# ---------------------------------------------------------------------------
# Staging entry creation
# ---------------------------------------------------------------------------

class TestStagingEntries:
    def _run_with_extractions(self, relay, extractions):
        """Helper: run handle_document with given extractions; returns sent messages."""
        message = _document_message()["message"]
        ingest_result = {
            "success": True,
            "extractions": extractions,
            "error": None,
            "parser": "claude",
        }
        mock_cm = MagicMock()
        mock_cm.load_committed.return_value = {}
        mock_cm._profile = {}

        sent = []
        with patch.object(relay, "send_message", side_effect=lambda cid, txt, **kw: sent.append(txt)), \
             patch.object(relay, "send_typing"), \
             patch.object(relay, "download_file", return_value=b"bytes"), \
             patch("simply_connect.ingestion.ingest_document", return_value=ingest_result), \
             patch("simply_connect.context_manager.ContextManager", return_value=mock_cm):
            relay.handle_document(100, 42, message)

        return sent, mock_cm

    def test_staging_entries_created_for_each_extraction(self, relay):
        """One staging entry is created per extraction."""
        extractions = [
            {"summary": "Party A details", "content": "Party A is Acme.", "category": "parties"},
            {"summary": "Payment terms", "content": "Net 30 days.", "category": "contracts"},
        ]
        _, mock_cm = self._run_with_extractions(relay, extractions)

        assert mock_cm.create_staging_entry.call_count == 2

    def test_reply_shows_staged_count(self, relay):
        """Reply message mentions how many items were staged."""
        extractions = [
            {"summary": "Item 1", "content": "Content 1", "category": "contracts"},
        ]
        sent, _ = self._run_with_extractions(relay, extractions)

        assert any("1 item" in t or "staged" in t.lower() for t in sent)

    def test_reply_mentions_sc_admin_review(self, relay):
        """Reply message reminds user to run sc-admin review."""
        extractions = [
            {"summary": "Item", "content": "Content", "category": "general"},
        ]
        sent, _ = self._run_with_extractions(relay, extractions)

        assert any("sc-admin review" in t for t in sent)

    def test_empty_extraction_shows_no_content_message(self, relay):
        """When ingest returns no extractions, a graceful message is sent."""
        sent, mock_cm = self._run_with_extractions(relay, [])

        mock_cm.create_staging_entry.assert_not_called()
        assert any("empty" in t.lower() or "no" in t.lower() or "template" in t.lower() for t in sent)

    def test_ingest_failure_sends_error_message(self, relay):
        """When ingest returns success=False, an error message is sent."""
        message = _document_message()["message"]
        ingest_result = {
            "success": False,
            "extractions": [],
            "error": "PDF parse error",
            "parser": "claude",
        }
        mock_cm = MagicMock()
        mock_cm.load_committed.return_value = {}
        mock_cm._profile = {}

        sent = []
        with patch.object(relay, "send_message", side_effect=lambda cid, txt, **kw: sent.append(txt)), \
             patch.object(relay, "send_typing"), \
             patch.object(relay, "download_file", return_value=b"bytes"), \
             patch("simply_connect.ingestion.ingest_document", return_value=ingest_result), \
             patch("simply_connect.context_manager.ContextManager", return_value=mock_cm):
            relay.handle_document(100, 42, message)

        assert any("PDF parse error" in t or "Could not" in t for t in sent)


# ---------------------------------------------------------------------------
# Temp file cleanup
# ---------------------------------------------------------------------------

class TestTempFileCleanup:
    def test_temp_file_deleted_after_success(self, relay, tmp_path):
        """Temp file is deleted even when ingest succeeds."""
        created_paths = []

        import tempfile as _tempfile
        real_ntf = _tempfile.NamedTemporaryFile

        def capturing_ntf(**kwargs):
            f = real_ntf(**kwargs)
            created_paths.append(Path(f.name))
            return f

        message = _document_message()["message"]
        ingest_result = {
            "success": True,
            "extractions": [],
            "error": None,
            "parser": "claude",
        }
        mock_cm = MagicMock()
        mock_cm.load_committed.return_value = {}
        mock_cm._profile = {}

        with patch.object(relay, "send_message"), \
             patch.object(relay, "send_typing"), \
             patch.object(relay, "download_file", return_value=b"bytes"), \
             patch("simply_connect.ingestion.ingest_document", return_value=ingest_result), \
             patch("simply_connect.context_manager.ContextManager", return_value=mock_cm), \
             patch("tempfile.NamedTemporaryFile", side_effect=capturing_ntf):
            relay.handle_document(100, 42, message)

        for p in created_paths:
            assert not p.exists(), f"Temp file {p} was not cleaned up"

    def test_temp_file_deleted_after_exception(self, relay):
        """Temp file is deleted even when ingest raises an exception."""
        created_paths = []

        import tempfile as _tempfile
        real_ntf = _tempfile.NamedTemporaryFile

        def capturing_ntf(**kwargs):
            f = real_ntf(**kwargs)
            created_paths.append(Path(f.name))
            return f

        message = _document_message()["message"]
        mock_cm = MagicMock()
        mock_cm.load_committed.return_value = {}
        mock_cm._profile = {}

        with patch.object(relay, "send_message"), \
             patch.object(relay, "send_typing"), \
             patch.object(relay, "download_file", return_value=b"bytes"), \
             patch("simply_connect.ingestion.ingest_document", side_effect=RuntimeError("boom")), \
             patch("simply_connect.context_manager.ContextManager", return_value=mock_cm), \
             patch("tempfile.NamedTemporaryFile", side_effect=capturing_ntf):
            relay.handle_document(100, 42, message)

        for p in created_paths:
            assert not p.exists(), f"Temp file {p} was not cleaned up after exception"
