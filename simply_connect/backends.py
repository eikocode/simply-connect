"""
LLM backend abstraction for simply-connect intelligence pipeline.

Backends provide a uniform interface for text and vision completions,
allowing the pipeline in intelligence.py to be provider-agnostic.

Select backend via SC_LLM_BACKEND env var (default: anthropic):
  anthropic  — Anthropic Claude SDK (ANTHROPIC_API_KEY) or claude CLI (OAuth)
  openai     — OpenAI SDK (OPENAI_API_KEY) or codex CLI (OAuth)
  gemini     — Google Gemini (GOOGLE_API_KEY) [future]

Domains can also inject a backend directly via process_document(backend=...).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

# Pricing per million tokens (as of 2025-04)
_PRICING: dict[str, tuple[float, float]] = {
    # model-prefix → (input $/M, output $/M)
    "claude-opus-4":    (15.0,  75.0),
    "claude-sonnet-4":  ( 3.0,  15.0),
    "claude-sonnet-3":  ( 3.0,  15.0),
    "claude-haiku-4":   ( 0.80,  4.0),
    "claude-haiku-3":   ( 0.25,  1.25),
}

def _log_api_cost(call_type: str, model: str, usage) -> None:
    """Log token usage and estimated cost for one API call."""
    try:
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        price_in, price_out = next(
            (v for k, v in _PRICING.items() if model.startswith(k)),
            (3.0, 15.0),  # default to Sonnet pricing
        )
        cost = (inp * price_in + out * price_out) / 1_000_000
        log.info(
            "[cost] %s | model=%s | in=%d out=%d tokens | $%.4f USD",
            call_type, model, inp, out, cost,
        )
    except Exception:
        pass  # never let logging break a completion


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
        """True if complete_vision() will work (image bytes accepted)."""
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
    """Anthropic Claude — SDK (ANTHROPIC_API_KEY) with claude CLI (OAuth) fallback.

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
            try:
                return self._sdk_complete(system, user_text, model, max_tokens)
            except Exception as e:
                # 401 / authentication errors mean the key is stale — fall back to CLI
                if self._has_cli() and (
                    "401" in str(e) or "authentication" in str(e).lower()
                    or "invalid x-api-key" in str(e).lower()
                ):
                    import logging
                    logging.getLogger(__name__).warning(
                        "AnthropicBackend: SDK auth failed (%s), falling back to CLI", e
                    )
                    return self._cli_complete(system, user_text, timeout=cli_timeout)
                raise
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
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        if mime_type == "application/pdf":
            # PDFs must use the document content block, not image
            file_block = {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": b64,
                },
            }
        else:
            media_type = mime_type if mime_type in (
                "image/jpeg", "image/png", "image/gif", "image/webp"
            ) else "image/jpeg"
            file_block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64,
                },
            }
        content = [file_block, {"type": "text", "text": prompt}]
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        _log_api_cost("complete_vision", model, resp.usage)
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
        _log_api_cost("complete", model, resp.usage)
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
# OpenAI backend
# ---------------------------------------------------------------------------

class OpenAIBackend:
    """OpenAI — SDK (OPENAI_API_KEY) with codex CLI (OAuth) fallback.

    Both paths support vision:
      SDK:  base64 image_url in message content (GPT-4o / GPT-4o-mini)
      CLI:  codex exec -i <tempfile>  (writes bytes to temp file, passes via -i flag)

    Model names from Anthropic schemas (e.g. "claude-haiku-4-5") are automatically
    mapped to OpenAI equivalents. OpenAI model names (e.g. "gpt-4o-mini") pass through.
    """

    # Anthropic → OpenAI model name mapping
    _MODEL_MAP: dict[str, str] = {
        "claude-haiku-4-5":   "gpt-4o-mini",
        "claude-haiku-3-5":   "gpt-4o-mini",
        "claude-sonnet-4-5":  "gpt-4o",
        "claude-sonnet-3-5":  "gpt-4o",
        "claude-opus-4-5":    "gpt-4o",
        "claude-opus-3":      "gpt-4o",
    }
    _DEFAULT_FAST     = "gpt-4o-mini"
    _DEFAULT_CAPABLE  = "gpt-4o"
    _CLI_MODEL        = "gpt-4o-mini"  # model used for codex CLI calls

    def name(self) -> str:
        return "openai"

    # ---- availability ----

    def is_available(self) -> bool:
        return self._has_api_key() or self._has_cli()

    def supports_vision(self) -> bool:
        # Both SDK and codex CLI support vision
        return self._has_api_key() or self._has_cli()

    def _has_api_key(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY", "").strip())

    def _has_cli(self) -> bool:
        return shutil.which("codex") is not None

    # ---- completions ----

    def complete(
        self,
        system: str,
        user_text: str,
        model: str,
        max_tokens: int = 4096,
        cli_timeout: int = 150,
    ) -> str:
        resolved = self._resolve_model(model)
        if self._has_api_key():
            return self._sdk_complete(system, user_text, resolved, max_tokens)
        elif self._has_cli():
            return self._cli_complete(system, user_text, timeout=cli_timeout)
        raise RuntimeError(
            "OpenAIBackend.complete(): no OPENAI_API_KEY and no codex CLI found"
        )

    def complete_vision(
        self,
        system: str,
        file_bytes: bytes,
        mime_type: str,
        prompt: str,
        model: str,
        max_tokens: int = 4096,
        cli_timeout: int = 150,
    ) -> str:
        resolved = self._resolve_model(model)
        if self._has_api_key():
            return self._sdk_vision_complete(
                system, file_bytes, mime_type, prompt, resolved, max_tokens
            )
        elif self._has_cli():
            return self._cli_vision_complete(
                system, file_bytes, mime_type, prompt, timeout=cli_timeout
            )
        raise RuntimeError(
            "OpenAIBackend.complete_vision(): no OPENAI_API_KEY and no codex CLI found"
        )

    # ---- model resolution ----

    def _resolve_model(self, model: str) -> str:
        """Map Anthropic model names to OpenAI equivalents; pass OpenAI names through."""
        if model in self._MODEL_MAP:
            return self._MODEL_MAP[model]
        if model.startswith(("gpt-", "o1", "o3", "o4")):
            return model  # already an OpenAI model name
        log.warning(f"OpenAIBackend: unknown model {model!r}, defaulting to {self._DEFAULT_FAST}")
        return self._DEFAULT_FAST

    # ---- SDK paths ----

    def _sdk_complete(
        self, system: str, user_text: str, model: str, max_tokens: int
    ) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
        )
        return resp.choices[0].message.content or ""

    def _sdk_vision_complete(
        self,
        system: str,
        file_bytes: bytes,
        mime_type: str,
        prompt: str,
        model: str,
        max_tokens: int,
    ) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # Use safe media type for non-image mime types (e.g. PDF — send as JPEG)
        media_type = mime_type if mime_type in (
            "image/jpeg", "image/png", "image/gif", "image/webp"
        ) else "image/jpeg"
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
        )
        return resp.choices[0].message.content or ""

    # ---- codex CLI paths ----

    def _cli_complete(self, system: str, user_content: str, timeout: int = 150) -> str:
        """Text completion via `codex exec`. System prompt is prepended to the prompt."""
        full_prompt = f"{system}\n\n{user_content}"
        return self._codex_exec(full_prompt, image_path=None, timeout=timeout)

    def _cli_vision_complete(
        self,
        system: str,
        file_bytes: bytes,
        mime_type: str,
        prompt: str,
        timeout: int = 150,
    ) -> str:
        """Vision completion via `codex exec -i <file>`. Writes bytes to temp file."""
        suffix = {
            "image/jpeg": ".jpg",
            "image/png":  ".png",
            "image/gif":  ".gif",
            "image/webp": ".webp",
        }.get(mime_type, ".jpg")

        tmp_img = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(file_bytes)
                tmp_img = f.name
            full_prompt = f"{system}\n\n{prompt}"
            return self._codex_exec(full_prompt, image_path=tmp_img, timeout=timeout)
        finally:
            if tmp_img:
                try:
                    os.unlink(tmp_img)
                except Exception:
                    pass

    def _codex_exec(
        self, prompt: str, image_path: str | None = None, timeout: int = 150
    ) -> str:
        """Core codex exec call. Writes last-message output to a temp file."""
        tmp_out = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                tmp_out = f.name

            cmd = [
                "codex", "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--dangerously-bypass-approvals-and-sandbox",
                "--model", self._CLI_MODEL,
                "-o", tmp_out,
            ]
            if image_path:
                cmd += ["-i", image_path]
            cmd.append(prompt)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"codex exec failed (rc={result.returncode}): {result.stderr[:200]}"
                )
            with open(tmp_out) as f:
                output = f.read().strip()
            if not output:
                raise RuntimeError("codex exec returned empty output")
            return output

        finally:
            if tmp_out:
                try:
                    os.unlink(tmp_out)
                except Exception:
                    pass


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
    if name in ("openai", "codex"):
        return OpenAIBackend()
    raise ValueError(
        f"Unknown SC_LLM_BACKEND: {name!r}. "
        "Available: 'anthropic' (default), 'openai'. Future: 'gemini'."
    )
