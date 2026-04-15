"""
LLM backend abstraction for simply-connect intelligence pipeline.

Backends provide a uniform interface for text and vision completions,
allowing the pipeline in intelligence.py to be provider-agnostic.

Select backend via SC_LLM_BACKEND env var (default: anthropic):
  anthropic  — Anthropic Claude SDK or CLI subprocess (default)
  openai     — OpenAI GPT-4o (requires OPENAI_API_KEY) [future]
  gemini     — Google Gemini (requires GOOGLE_API_KEY) [future]

Domains can also inject a backend directly via process_document(backend=...).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMBackend(Protocol):
    """Uniform interface for LLM providers used by the intelligence pipeline."""

    def name(self) -> str:
        """Short identifier, e.g. 'anthropic', 'openai'."""
        ...

    def is_available(self) -> bool:
        """True if this backend can make calls (credentials present, CLI found, etc.)."""
        ...

    def supports_vision(self) -> bool:
        """True if complete_vision() will work. False for CLI-only paths."""
        ...

    def complete(
        self,
        system: str,
        user_text: str,
        model: str,
        max_tokens: int = 4096,
    ) -> str:
        """Text-only completion. Returns the assistant's reply as a string."""
        ...

    def complete_vision(
        self,
        system: str,
        file_bytes: bytes,
        mime_type: str,
        prompt: str,
        model: str,
        max_tokens: int = 4096,
    ) -> str:
        """Vision completion — one image + text prompt. Returns reply as string."""
        ...


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

class AnthropicBackend:
    """Anthropic Claude — SDK (API key) with CLI subprocess fallback.

    Vision requires ANTHROPIC_API_KEY; CLI path is text-only.
    """

    def name(self) -> str:
        return "anthropic"

    # ---- availability ----

    def is_available(self) -> bool:
        return self._has_api_key() or self._has_cli()

    def supports_vision(self) -> bool:
        return self._has_api_key()

    def _has_api_key(self) -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())

    def _has_cli(self) -> bool:
        return shutil.which("claude") is not None

    # ---- completions ----

    def complete(
        self,
        system: str,
        user_text: str,
        model: str,
        max_tokens: int = 4096,
        cli_timeout: int = 150,
    ) -> str:
        if self._has_api_key():
            return self._sdk_complete(system, user_text, model, max_tokens)
        elif self._has_cli():
            return self._cli_complete(system, user_text, timeout=cli_timeout)
        raise RuntimeError(
            "AnthropicBackend.complete(): no ANTHROPIC_API_KEY and no claude CLI found"
        )

    def complete_vision(
        self,
        system: str,
        file_bytes: bytes,
        mime_type: str,
        prompt: str,
        model: str,
        max_tokens: int = 4096,
    ) -> str:
        if not self._has_api_key():
            raise RuntimeError(
                "AnthropicBackend.complete_vision(): ANTHROPIC_API_KEY required for vision"
            )
        media_type = mime_type if mime_type in (
            "image/jpeg", "image/png", "image/gif", "image/webp"
        ) else "image/jpeg"
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(file_bytes).decode("utf-8"),
                },
            },
            {"type": "text", "text": prompt},
        ]
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        return resp.content[0].text

    # ---- internal ----

    def _sdk_complete(
        self, system: str, user_text: str, model: str, max_tokens: int
    ) -> str:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        return resp.content[0].text

    def _sanitise(self, text: str) -> str:
        """Strip non-printable / binary chars that break CLI arg passing."""
        import unicodedata
        return "".join(
            ch for ch in text
            if ch == "\n" or ch == "\t" or (not unicodedata.category(ch).startswith("C"))
        )

    def _cli_complete(self, system: str, user_content: str, timeout: int = 150) -> str:
        cmd = [
            "claude",
            "--print",
            "--output-format", "json",
            "--model", "claude-haiku-4-5",
            "--system-prompt", self._sanitise(system),
            "--dangerously-skip-permissions",
        ]
        result = subprocess.run(
            cmd,
            input=self._sanitise(user_content),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (rc={result.returncode}): {result.stderr[:200]}"
            )
        stdout = result.stdout.strip()
        if not stdout:
            raise RuntimeError("claude CLI returned empty output")
        try:
            data = json.loads(stdout)
            return data.get("result") or data.get("text") or ""
        except json.JSONDecodeError:
            return stdout


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_backend(name: str | None = None) -> LLMBackend:
    """Return a backend instance by name.

    If name is None, reads SC_LLM_BACKEND env var (default: 'anthropic').
    """
    if name is None:
        name = os.getenv("SC_LLM_BACKEND", "anthropic").strip().lower()
    if name in ("anthropic", "claude"):
        return AnthropicBackend()
    # Future providers — raise clearly so the error is actionable
    raise ValueError(
        f"Unknown SC_LLM_BACKEND: {name!r}. "
        "Available now: 'anthropic'. Future: 'openai', 'gemini'."
    )
