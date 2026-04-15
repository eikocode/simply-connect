#!/usr/bin/env python3
"""Web relay for simply-connect operator assistant.

Exposes a Starlette HTTP API that mirrors the Telegram relay surface — accepts
chat messages, file uploads, and onboarding state — and dispatches to the
configured Claude runtime.

Runtime is instantiated ONCE at module load (same pattern as TelegramRelay).
ContextManager is instantiated per-request so it always reflects current disk state.

Endpoints:
  GET  /health                  — Status check: runtime, document_parser, role
  GET  /onboarding/status       — Read onboarding state for ?user_id=
  POST /onboarding/complete     — Write onboarding state + fire extension hook
  POST /chat                    — Send a message, get a reply
  POST /upload                  — Upload a file (PDF/image), get a reply

Environment variables:
  SC_WEB_PORT            — HTTP port (default: 8090)
  SC_WEB_ALLOWED_ORIGINS — Comma-separated CORS origins (default: *)
  SC_CLAUDE_RUNTIME      — Runtime selector (sdk/cli/kilo/opencode, default: sdk)
  SC_DOCUMENT_PARSER     — Parser selector (claude/docling, default: claude)
  SC_DATA_DIR            — Project root override (default: cwd)

Usage:
  python -m simply_connect.web_relay
  sc-web
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import config
from .runtimes import get_runtime

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ROLE_NAME = "operator"
_DEFAULT_PORT = 8090

# ---------------------------------------------------------------------------
# Module-level runtime (instantiated once, reused across requests)
# ---------------------------------------------------------------------------

_runtime = get_runtime(config.CLAUDE_RUNTIME, role_name=_ROLE_NAME)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    """Resolve the project data root from SC_DATA_DIR or cwd."""
    return Path(os.getenv("SC_DATA_DIR", ".")).resolve()


def _onboarding_path(user_id: str) -> Path:
    return _project_root() / "data" / "onboarding" / f"{user_id}.json"


def _read_onboarding(user_id: str) -> dict:
    path = _onboarding_path(user_id)
    if not path.exists():
        return {"completed": False}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"completed": False}


def _write_onboarding(user_id: str, state: dict) -> None:
    path = _onboarding_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "runtime": config.CLAUDE_RUNTIME,
        "document_parser": config.DOCUMENT_PARSER,
        "role": _ROLE_NAME,
    })


async def handle_onboarding_status(request: Request) -> JSONResponse:
    user_id = request.query_params.get("user_id", "")
    if not user_id:
        return JSONResponse({"error": "user_id is required"}, status_code=400)

    state = _read_onboarding(user_id)
    return JSONResponse({
        "completed": state.get("completed", False),
        "first_name": state.get("first_name"),
        "household_mode": state.get("household_mode"),
        "household_members": state.get("family_members", []),
    })


async def handle_onboarding_complete(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    user_id = data.get("user_id", "")
    if not user_id:
        return JSONResponse({"error": "user_id is required"}, status_code=400)

    first_name = (data.get("first_name") or data.get("name") or user_id).strip()
    household_mode = data.get("household_mode", "solo")
    family_members = data.get("family_members", [])
    language = data.get("language", "en")

    state = {
        "completed": True,
        "source": "web",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "first_name": first_name,
        "household_mode": household_mode,
        "family_members": family_members,
        "language": language,
    }
    # Carry through any extra fields from the request body
    for k, v in data.items():
        if k not in state:
            state[k] = v

    _write_onboarding(user_id, state)

    # Fire extension hook — best-effort, non-fatal
    try:
        from .context_manager import ContextManager
        from .ext_loader import handle_web_onboarding_complete
        cm = ContextManager()
        handle_web_onboarding_complete(data, cm)
    except Exception as e:
        log.warning(f"handle_web_onboarding_complete extension hook failed (non-fatal): {e}")

    household_size = 1 + len(family_members)
    primary_user = first_name or user_id

    return JSONResponse({
        "success": True,
        "primary_user": primary_user,
        "household_mode": household_mode,
        "household_size": household_size,
        "household_members": family_members,
    })


async def handle_chat(request: Request) -> JSONResponse:
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "Invalid JSON body"}, status_code=400)

    message = data.get("message", "")
    user_id = str(data.get("user_id", "default"))
    first_name = data.get("first_name", "")

    if not message:
        return JSONResponse({"success": False, "error": "message is required"}, status_code=400)

    # Stash first_name on the runtime so it's available during the call
    if not hasattr(_runtime, "_user_meta"):
        _runtime._user_meta = {}
    if first_name:
        _runtime._user_meta[user_id] = {"first_name": first_name}

    # Onboarding gate
    onboarding = _read_onboarding(user_id)
    if not onboarding.get("completed", False):
        return JSONResponse(
            {"error": "onboarding_required", "redirect": "/onboarding"},
            status_code=409,
        )

    try:
        import asyncio as _asyncio
        reply = await _asyncio.get_event_loop().run_in_executor(
            None, _runtime.call, message, user_id
        )
        return JSONResponse({"success": True, "reply": reply})
    except Exception as e:
        log.error(f"Runtime call failed for user {user_id}: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


async def handle_upload(request: Request) -> JSONResponse:
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"success": False, "error": "Failed to parse multipart form"}, status_code=400)

    upload = form.get("file")
    caption = str(form.get("caption", "") or "")
    user_id = str(form.get("user_id", "") or "")

    if upload is None:
        return JSONResponse({"success": False, "error": "file is required"}, status_code=400)

    try:
        file_bytes = await upload.read()
        filename = upload.filename or "upload"
        mime_type = upload.content_type or "application/octet-stream"
    except Exception as e:
        return JSONResponse({"success": False, "error": f"Failed to read file: {e}"}, status_code=400)

    try:
        from .context_manager import ContextManager
        from .ext_loader import maybe_handle_document
        cm = ContextManager()
        reply = maybe_handle_document(
            file_bytes,
            filename,
            mime_type,
            caption,
            cm,
            role_name=_ROLE_NAME,
            user_id=user_id,
        )
        if reply:
            return JSONResponse({"success": True, "reply": reply})
        return JSONResponse(
            {"success": False, "error": "No extension handled this document type"},
            status_code=422,
        )
    except Exception as e:
        log.error(f"Document upload failed: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Tool dispatch + context endpoints (mirrors web_api.py for mcp.js clients)
# ---------------------------------------------------------------------------

async def handle_tool(request: Request) -> JSONResponse:
    """POST /tool/{name} — dispatch an extension tool by name."""
    tool_name = request.path_params.get("name", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        from .context_manager import ContextManager
        from .ext_loader import dispatch_extension_tool
        cm = ContextManager()
        result_str = dispatch_extension_tool(tool_name, body, cm)
        try:
            import json as _json
            return JSONResponse({"success": True, "result": _json.loads(result_str)})
        except Exception:
            return JSONResponse({"success": True, "result": result_str})
    except ValueError as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=404)
    except Exception as e:
        log.exception(f"Tool '{tool_name}' failed")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


async def handle_context(request: Request) -> JSONResponse:
    """GET /context or /context/{category} — return committed context files."""
    category = request.path_params.get("category", None)
    try:
        from .context_manager import ContextManager
        cm = ContextManager()
        committed = cm.load_committed()
        if category:
            if category not in committed:
                return JSONResponse({"error": f"Category '{category}' not found"}, status_code=404)
            return JSONResponse({"category": category, "content": committed[category]})
        return JSONResponse(committed)
    except Exception as e:
        log.exception("Context fetch failed")
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _build_app(allowed_origins: list[str]) -> Starlette:
    from starlette.routing import Mount
    return Starlette(
        routes=[
            Route("/health", handle_health, methods=["GET"]),
            Route("/onboarding/status", handle_onboarding_status, methods=["GET"]),
            Route("/onboarding/complete", handle_onboarding_complete, methods=["POST"]),
            Route("/chat", handle_chat, methods=["POST"]),
            Route("/upload", handle_upload, methods=["POST"]),
            Route("/tool/{name}", handle_tool, methods=["POST"]),
            Route("/context", handle_context, methods=["GET"]),
            Route("/context/{category}", handle_context, methods=["GET"]),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=allowed_origins,
                allow_methods=["GET", "POST"],
                allow_headers=["*"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import asyncio

    port = int(os.environ.get("SC_WEB_PORT", _DEFAULT_PORT))
    origins_raw = os.environ.get("SC_WEB_ALLOWED_ORIGINS", "*")
    allowed_origins = [o.strip() for o in origins_raw.split(",") if o.strip()] or ["*"]

    print(f"Starting simply-connect web relay on port {port}...")
    print(f"  runtime: {config.CLAUDE_RUNTIME}")
    print(f"  document_parser: {config.DOCUMENT_PARSER}")
    print(f"  allowed_origins: {allowed_origins}")

    app = _build_app(allowed_origins)
    cfg = uvicorn.Config(app, host="0.0.0.0", port=port)
    server = uvicorn.Server(cfg)
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()
