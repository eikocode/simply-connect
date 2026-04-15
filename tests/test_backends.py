"""
Tests for simply_connect/backends.py

Covers:
  AnthropicBackend — availability, vision support, SDK path, CLI path, error paths
  OpenAIBackend    — availability, vision support, SDK path, codex CLI path,
                     model resolution, error paths
  get_backend()    — factory routing via SC_LLM_BACKEND env var

No live API calls — anthropic, openai clients and subprocess are fully mocked.
"""

from __future__ import annotations

import json
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

# Load backends directly to avoid __init__.py / brain.py Python 3.9 issue
import importlib.util
import sys
import types


def _load_backend_module():
    if "simply_connect.backends" in sys.modules:
        return sys.modules["simply_connect.backends"]
    pkg = types.ModuleType("simply_connect")
    pkg.__path__ = [str(__import__("pathlib").Path(__file__).parent.parent / "simply_connect")]
    pkg.__package__ = "simply_connect"
    sys.modules.setdefault("simply_connect", pkg)
    spec = importlib.util.spec_from_file_location(
        "simply_connect.backends",
        str(__import__("pathlib").Path(__file__).parent.parent / "simply_connect" / "backends.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["simply_connect.backends"] = mod
    spec.loader.exec_module(mod)
    return mod


_backends = _load_backend_module()
AnthropicBackend = _backends.AnthropicBackend
OpenAIBackend = _backends.OpenAIBackend
LLMBackend = _backends.LLMBackend
get_backend = _backends.get_backend


# ---------------------------------------------------------------------------
# AnthropicBackend — availability
# ---------------------------------------------------------------------------

class TestAnthropicAvailability:
    def test_available_with_api_key(self):
        b = AnthropicBackend()
        with patch.object(b, "_has_api_key", return_value=True):
            assert b.is_available() is True

    def test_available_with_cli_only(self):
        b = AnthropicBackend()
        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True):
            assert b.is_available() is True

    def test_unavailable_without_either(self):
        b = AnthropicBackend()
        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=False):
            assert b.is_available() is False

    def test_vision_only_with_api_key(self):
        b = AnthropicBackend()
        with patch.object(b, "_has_api_key", return_value=True):
            assert b.supports_vision() is True

    def test_no_vision_without_api_key(self):
        """CLI path cannot send image bytes."""
        b = AnthropicBackend()
        with patch.object(b, "_has_api_key", return_value=False):
            assert b.supports_vision() is False

    def test_name(self):
        assert AnthropicBackend().name() == "anthropic"

    def test_isinstance_protocol(self):
        assert isinstance(AnthropicBackend(), LLMBackend)


# ---------------------------------------------------------------------------
# AnthropicBackend — complete() SDK path
# ---------------------------------------------------------------------------

class TestAnthropicCompleteSDK:
    def _make_mock_response(self, text: str):
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        return resp

    def test_sdk_complete_calls_anthropic_client(self):
        b = AnthropicBackend()
        mock_response = self._make_mock_response('{"doc_type": "receipt"}')

        with patch.object(b, "_has_api_key", return_value=True), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            import anthropic as _anthropic
            with patch.object(_anthropic, "Anthropic") as MockAnthropic:
                mock_client = MagicMock()
                MockAnthropic.return_value = mock_client
                mock_client.messages.create.return_value = mock_response

                result = b.complete("system prompt", "user text", model="claude-haiku-4-5")

        assert result == '{"doc_type": "receipt"}'
        mock_client.messages.create.assert_called_once()

    def test_sdk_complete_passes_model(self):
        b = AnthropicBackend()
        mock_response = self._make_mock_response("answer")

        with patch.object(b, "_has_api_key", return_value=True), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            import anthropic as _anthropic
            with patch.object(_anthropic, "Anthropic") as MockAnthropic:
                mock_client = MagicMock()
                MockAnthropic.return_value = mock_client
                mock_client.messages.create.return_value = mock_response

                b.complete("sys", "user", model="claude-sonnet-4-5", max_tokens=2048)
                call_kwargs = mock_client.messages.create.call_args
                assert call_kwargs.kwargs["model"] == "claude-sonnet-4-5"
                assert call_kwargs.kwargs["max_tokens"] == 2048

    def test_sdk_complete_raises_without_credentials(self):
        b = AnthropicBackend()
        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=False):
            with pytest.raises(RuntimeError, match="no ANTHROPIC_API_KEY"):
                b.complete("sys", "user", model="claude-haiku-4-5")


# ---------------------------------------------------------------------------
# AnthropicBackend — complete() CLI path
# ---------------------------------------------------------------------------

class TestAnthropicCompleteCLI:
    def test_cli_complete_calls_subprocess(self):
        b = AnthropicBackend()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"result": '{"doc_type": "bank_statement"}'})

        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = b.complete("system", "classify this", model="claude-haiku-4-5")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "--dangerously-skip-permissions" in cmd

    def test_cli_complete_returns_result_field(self):
        b = AnthropicBackend()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"result": "hello from claude"})

        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True), \
             patch("subprocess.run", return_value=mock_proc):
            result = b._cli_complete("sys", "user")
        assert result == "hello from claude"

    def test_cli_complete_falls_back_to_raw_stdout(self):
        """When stdout is not JSON, return it as-is."""
        b = AnthropicBackend()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "raw text response"

        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True), \
             patch("subprocess.run", return_value=mock_proc):
            result = b._cli_complete("sys", "user")
        assert result == "raw text response"

    def test_cli_complete_raises_on_nonzero_rc(self):
        b = AnthropicBackend()
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "something went wrong"

        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True), \
             patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="claude CLI failed"):
                b._cli_complete("sys", "user")

    def test_cli_complete_raises_on_empty_stdout(self):
        b = AnthropicBackend()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "  "

        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True), \
             patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="empty output"):
                b._cli_complete("sys", "user")


# ---------------------------------------------------------------------------
# AnthropicBackend — complete_vision()
# ---------------------------------------------------------------------------

class TestAnthropicCompleteVision:
    def _make_mock_response(self, text: str):
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        return resp

    def test_vision_raises_without_api_key(self):
        b = AnthropicBackend()
        with patch.object(b, "_has_api_key", return_value=False):
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY required"):
                b.complete_vision("sys", b"bytes", "image/jpeg", "prompt", "claude-haiku-4-5")

    def test_vision_sends_base64_image(self):
        b = AnthropicBackend()
        img_bytes = b"fake-image-data"
        mock_response = self._make_mock_response('{"doc_type": "receipt"}')

        with patch.object(b, "_has_api_key", return_value=True), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            import anthropic as _anthropic
            with patch.object(_anthropic, "Anthropic") as MockAnthropic:
                mock_client = MagicMock()
                MockAnthropic.return_value = mock_client
                mock_client.messages.create.return_value = mock_response

                result = b.complete_vision(
                    "sys", img_bytes, "image/jpeg", "classify this", "claude-haiku-4-5"
                )

        assert result == '{"doc_type": "receipt"}'
        call_kwargs = mock_client.messages.create.call_args.kwargs
        messages = call_kwargs["messages"]
        content = messages[0]["content"]
        # First content block must be an image
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["media_type"] == "image/jpeg"

    def test_vision_normalises_unknown_mime_to_jpeg(self):
        """Non-image MIME types (e.g. PDF) fall back to image/jpeg for the media_type."""
        b = AnthropicBackend()
        mock_response = self._make_mock_response("{}")

        with patch.object(b, "_has_api_key", return_value=True), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            import anthropic as _anthropic
            with patch.object(_anthropic, "Anthropic") as MockAnthropic:
                mock_client = MagicMock()
                MockAnthropic.return_value = mock_client
                mock_client.messages.create.return_value = mock_response

                b.complete_vision("sys", b"pdf-bytes", "application/pdf", "prompt", "claude-haiku-4-5")
                call_kwargs = mock_client.messages.create.call_args.kwargs
                media_type = call_kwargs["messages"][0]["content"][0]["source"]["media_type"]
                assert media_type == "image/jpeg"


# ---------------------------------------------------------------------------
# OpenAIBackend — availability and model resolution
# ---------------------------------------------------------------------------

class TestOpenAIAvailability:
    def test_available_with_api_key(self):
        b = OpenAIBackend()
        with patch.object(b, "_has_api_key", return_value=True):
            assert b.is_available() is True

    def test_available_with_cli_only(self):
        b = OpenAIBackend()
        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True):
            assert b.is_available() is True

    def test_unavailable_without_either(self):
        b = OpenAIBackend()
        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=False):
            assert b.is_available() is False

    def test_vision_with_api_key(self):
        b = OpenAIBackend()
        with patch.object(b, "_has_api_key", return_value=True):
            assert b.supports_vision() is True

    def test_vision_with_cli_only(self):
        """Codex CLI supports -i flag for images — vision works without API key."""
        b = OpenAIBackend()
        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True):
            assert b.supports_vision() is True

    def test_no_vision_without_either(self):
        b = OpenAIBackend()
        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=False):
            assert b.supports_vision() is False

    def test_name(self):
        assert OpenAIBackend().name() == "openai"

    def test_isinstance_protocol(self):
        assert isinstance(OpenAIBackend(), LLMBackend)


class TestOpenAIModelResolution:
    def test_maps_haiku_to_gpt4o_mini(self):
        assert OpenAIBackend()._resolve_model("claude-haiku-4-5") == "gpt-4o-mini"

    def test_maps_sonnet_to_gpt4o(self):
        assert OpenAIBackend()._resolve_model("claude-sonnet-4-5") == "gpt-4o"

    def test_passes_through_openai_model(self):
        assert OpenAIBackend()._resolve_model("gpt-4o-mini") == "gpt-4o-mini"
        assert OpenAIBackend()._resolve_model("o4-mini") == "o4-mini"

    def test_unknown_model_defaults_to_fast(self):
        result = OpenAIBackend()._resolve_model("unknown-model-xyz")
        assert result == OpenAIBackend._DEFAULT_FAST


# ---------------------------------------------------------------------------
# OpenAIBackend — complete() SDK path
# ---------------------------------------------------------------------------

def _make_openai_module(response_text: str):
    """Create a fake openai module for injection into sys.modules."""
    choice = MagicMock()
    choice.message.content = response_text
    mock_resp = MagicMock()
    mock_resp.choices = [choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    mock_openai = MagicMock()
    mock_openai.OpenAI.return_value = mock_client
    return mock_openai, mock_client


class TestOpenAICompleteSDK:
    def test_sdk_complete_calls_openai_client(self):
        b = OpenAIBackend()
        mock_openai, mock_client = _make_openai_module('{"doc_type": "receipt"}')

        with patch.object(b, "_has_api_key", return_value=True), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
             patch.dict(sys.modules, {"openai": mock_openai}):
            result = b.complete("sys", "user", model="claude-haiku-4-5")

        assert result == '{"doc_type": "receipt"}'
        mock_client.chat.completions.create.assert_called_once()

    def test_sdk_complete_maps_model_name(self):
        b = OpenAIBackend()
        mock_openai, mock_client = _make_openai_module("answer")

        with patch.object(b, "_has_api_key", return_value=True), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
             patch.dict(sys.modules, {"openai": mock_openai}):
            b.complete("sys", "user", model="claude-haiku-4-5")
            call_kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert call_kwargs["model"] == "gpt-4o-mini"

    def test_sdk_complete_sends_system_message(self):
        b = OpenAIBackend()
        mock_openai, mock_client = _make_openai_module("ok")

        with patch.object(b, "_has_api_key", return_value=True), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
             patch.dict(sys.modules, {"openai": mock_openai}):
            b.complete("my system prompt", "my user text", model="gpt-4o-mini")
            messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
            assert messages[0] == {"role": "system", "content": "my system prompt"}
            assert messages[1] == {"role": "user", "content": "my user text"}

    def test_sdk_complete_raises_without_credentials(self):
        b = OpenAIBackend()
        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=False):
            with pytest.raises(RuntimeError, match="no OPENAI_API_KEY"):
                b.complete("sys", "user", model="gpt-4o-mini")


# ---------------------------------------------------------------------------
# OpenAIBackend — complete_vision() SDK path
# ---------------------------------------------------------------------------

class TestOpenAIVisionSDK:
    def test_sdk_vision_sends_image_url_content(self):
        b = OpenAIBackend()
        img_bytes = b"fake-image-data"
        mock_openai, mock_client = _make_openai_module('{"doc_type": "receipt"}')

        with patch.object(b, "_has_api_key", return_value=True), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
             patch.dict(sys.modules, {"openai": mock_openai}):
            result = b.complete_vision(
                "sys", img_bytes, "image/jpeg", "classify this", "claude-haiku-4-5"
            )

        assert result == '{"doc_type": "receipt"}'
        messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
        # System message first
        assert messages[0]["role"] == "system"
        # User message with image_url content
        user_content = messages[1]["content"]
        image_block = next(blk for blk in user_content if blk.get("type") == "image_url")
        assert "data:image/jpeg;base64," in image_block["image_url"]["url"]

    def test_sdk_vision_normalises_pdf_to_jpeg(self):
        b = OpenAIBackend()
        mock_openai, mock_client = _make_openai_module("{}")

        with patch.object(b, "_has_api_key", return_value=True), \
             patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
             patch.dict(sys.modules, {"openai": mock_openai}):
            b.complete_vision("sys", b"pdf", "application/pdf", "prompt", "claude-haiku-4-5")
            messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
            image_block = next(
                blk for blk in messages[1]["content"] if blk.get("type") == "image_url"
            )
            assert image_block["image_url"]["url"].startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------------------
# OpenAIBackend — codex CLI path
# ---------------------------------------------------------------------------

class TestOpenAICodexCLI:
    def test_cli_complete_calls_codex_exec(self, tmp_path):
        b = OpenAIBackend()

        def fake_run(cmd, **kwargs):
            # Write fake output to the -o file
            o_idx = cmd.index("-o")
            out_file = cmd[o_idx + 1]
            with open(out_file, "w") as f:
                f.write('{"doc_type": "receipt"}')
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True), \
             patch("subprocess.run", side_effect=fake_run) as mock_run:
            result = b.complete("sys", "user", model="claude-haiku-4-5")

        assert result == '{"doc_type": "receipt"}'
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "--ephemeral" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd

    def test_cli_vision_writes_temp_file_and_passes_i_flag(self):
        b = OpenAIBackend()
        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(cmd[:])
            o_idx = cmd.index("-o")
            out_file = cmd[o_idx + 1]
            with open(out_file, "w") as f:
                f.write('{"doc_type": "receipt"}')
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True), \
             patch("subprocess.run", side_effect=fake_run):
            result = b.complete_vision("sys", b"img-bytes", "image/jpeg", "classify", "claude-haiku-4-5")

        assert result == '{"doc_type": "receipt"}'
        cmd = captured_cmds[0]
        assert "-i" in cmd
        img_path = cmd[cmd.index("-i") + 1]
        # Temp file should have been cleaned up
        assert not __import__("os").path.exists(img_path)

    def test_cli_vision_uses_correct_suffix_for_png(self):
        b = OpenAIBackend()
        captured_img_paths = []

        def fake_run(cmd, **kwargs):
            if "-i" in cmd:
                captured_img_paths.append(cmd[cmd.index("-i") + 1])
            o_idx = cmd.index("-o")
            with open(cmd[o_idx + 1], "w") as f:
                f.write("response")
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True), \
             patch("subprocess.run", side_effect=fake_run):
            b.complete_vision("sys", b"png-bytes", "image/png", "prompt", "gpt-4o-mini")

        assert captured_img_paths[0].endswith(".png")

    def test_cli_raises_on_nonzero_rc(self):
        b = OpenAIBackend()

        def fake_run(cmd, **kwargs):
            # Write empty -o file so cleanup doesn't fail
            o_idx = cmd.index("-o")
            with open(cmd[o_idx + 1], "w") as f:
                f.write("")
            m = MagicMock()
            m.returncode = 1
            m.stderr = "codex error message"
            return m

        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=True), \
             patch("subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="codex exec failed"):
                b._cli_complete("sys", "user")

    def test_cli_complete_raises_without_credentials(self):
        b = OpenAIBackend()
        with patch.object(b, "_has_api_key", return_value=False), \
             patch.object(b, "_has_cli", return_value=False):
            with pytest.raises(RuntimeError, match="no OPENAI_API_KEY"):
                b.complete("sys", "user", model="gpt-4o-mini")


# ---------------------------------------------------------------------------
# get_backend() factory
# ---------------------------------------------------------------------------

class TestGetBackend:
    def test_default_is_anthropic(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SC_LLM_BACKEND", None)
            b = get_backend()
        assert isinstance(b, AnthropicBackend)

    def test_env_var_selects_anthropic(self):
        with patch.dict(os.environ, {"SC_LLM_BACKEND": "anthropic"}):
            b = get_backend()
        assert isinstance(b, AnthropicBackend)

    def test_env_var_selects_openai(self):
        with patch.dict(os.environ, {"SC_LLM_BACKEND": "openai"}):
            b = get_backend()
        assert isinstance(b, OpenAIBackend)

    def test_codex_alias_selects_openai(self):
        with patch.dict(os.environ, {"SC_LLM_BACKEND": "codex"}):
            b = get_backend()
        assert isinstance(b, OpenAIBackend)

    def test_claude_alias_selects_anthropic(self):
        with patch.dict(os.environ, {"SC_LLM_BACKEND": "claude"}):
            b = get_backend()
        assert isinstance(b, AnthropicBackend)

    def test_explicit_name_overrides_env(self):
        with patch.dict(os.environ, {"SC_LLM_BACKEND": "anthropic"}):
            b = get_backend("openai")
        assert isinstance(b, OpenAIBackend)

    def test_unknown_name_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown SC_LLM_BACKEND"):
            get_backend("gemini")

    def test_all_backends_satisfy_protocol(self):
        for backend_name in ("anthropic", "openai"):
            b = get_backend(backend_name)
            assert isinstance(b, LLMBackend), f"{backend_name} does not satisfy LLMBackend protocol"
