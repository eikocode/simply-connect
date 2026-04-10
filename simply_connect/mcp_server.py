#!/usr/bin/env python3
"""MCP server exposing committed-context agent tools.

Supports two transports:
  - stdio  (default): for CLIRuntime / Claude Code
  - http   (--http):  for WebMCP / browser surface

Tools exposed:
  - get_committed_context   → read authoritative context files
  - get_staging_entries     → inspect pending staging queue
  - capture_to_staging      → write a new candidate context update
  - ingest_document         → parse a document file into staging entries

Usage:
  python -m simply-connect.mcp_server           # stdio
  python -m simply-connect.mcp_server --http    # HTTP/SSE on $SC_MCP_PORT (default 3004)
  sc-mcp                                         # stdio (entry point)
  sc-mcp --http                                  # HTTP/SSE (entry point)
"""
import argparse
import json
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from .context_manager import ContextManager
from .tools import TOOLS

app = Server("simply-connect")
_cm = ContextManager()
_session_role = os.environ.get("SC_SESSION_ROLE", "operator")
_capture_roles = set(_cm._profile.get("capture_roles", []))

# Roles that are considered framework-level and write directly to staging
_FRAMEWORK_ROLES = {"operator", "admin"}
_is_domain_role = _session_role not in _FRAMEWORK_ROLES

# ---------------------------------------------------------------------------
# Extension tool discovery (at module load time)
# ---------------------------------------------------------------------------

_extension_tools: list[dict] = []

try:
    import sys
    _root_str = str(_cm._root)
    if _root_str not in sys.path:
        sys.path.insert(0, _root_str)
    from .ext_loader import get_all_tools as _get_all_ext_tools, dispatch_extension_tool as _dispatch_ext_tool
    _extension_tools = _get_all_ext_tools(_cm)
except Exception as _ext_load_err:
    import logging as _logging
    _logging.getLogger(__name__).debug(f"Extensions not loaded: {_ext_load_err}")
    _dispatch_ext_tool = None

_all_tools = TOOLS + _extension_tools


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["input_schema"],
        )
        for t in _all_tools
    ]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    if name == "get_committed_context":
        if _session_role in _cm.roles:
            committed = _cm.load_context_for_role(_session_role)["committed"]
        else:
            committed = _cm.load_committed()
        category = arguments.get("category")
        if category:
            filtered = {k: v for k, v in committed.items() if k == category}
        else:
            filtered = committed
        result = {
            "committed_context": filtered,
            "note": "This is authoritative context — full trust.",
        }
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "get_staging_entries":
        status_filter = arguments.get("status", "pending")
        entries = _cm.list_staging(status=status_filter)
        result = {
            "staging_entries": entries,
            "count": len(entries),
            "note": "Staging entries are UNCONFIRMED — treat as hints, not facts. Always flag when used.",
        }
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "capture_to_staging":
        # Domain roles cannot write to staging
        if _is_domain_role:
            raise ValueError(
                f"Domain role '{_session_role}' cannot write to staging. "
                f"Use capture_to_session instead."
            )
        if _capture_roles and _session_role not in _capture_roles:
            raise ValueError(f"Role '{_session_role}' is not allowed to capture to staging")
        summary = arguments.get("summary", "")
        content = arguments.get("content", "")
        category = arguments.get("category", "general")
        source = arguments.get("source", "mcp")

        if not summary or not content:
            raise ValueError("Both 'summary' and 'content' are required for capture_to_staging")

        entry_id = _cm.create_staging_entry(
            summary=summary,
            content=content,
            category=category,
            source=source,
        )
        result = {
            "entry_id": entry_id,
            "status": "pending",
            "message": f"Captured to staging as '{entry_id}'. Pending admin review before committing to context.",
        }
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "capture_to_session":
        # Domain roles write to session — ephemeral, for later curation
        if not _is_domain_role and _capture_roles and _session_role not in _capture_roles:
            raise ValueError(f"Role '{_session_role}' is not allowed to capture to session")
        summary = arguments.get("summary", "")
        content = arguments.get("content", "")
        category = arguments.get("category", "general")

        if not summary or not content:
            raise ValueError("Both 'summary' and 'content' are required for capture_to_session")

        result = {
            "status": "noted",
            "message": "Noted — under review.",
            "summary": summary,
            "category": category,
        }
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "ingest_document":
        from pathlib import Path as _Path
        from .ingestion import ingest_document

        filepath = _Path(arguments["filepath"])
        committed = _cm.load_committed()
        profile = _cm._profile

        result = ingest_document(filepath, committed, profile)

        # Auto-create staging entries for each extraction
        if result.get("success") and result.get("extractions"):
            staged_ids = []
            for item in result["extractions"]:
                item_summary = item.get("summary", filepath.name)
                item_content = item.get("content", "")
                item_category = item.get("category", "general")
                if item_content.strip():
                    entry_id = _cm.create_staging_entry(
                        summary=item_summary,
                        content=item_content,
                        category=item_category,
                        source=f"ingest:{filepath.name}",
                    )
                    staged_ids.append(entry_id)
            result["staged_entry_ids"] = staged_ids

        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    # Fall through to extension tools
    if _dispatch_ext_tool is not None:
        try:
            guarded_arguments = dict(arguments)
            guarded_arguments["__session_role"] = _session_role
            result_str = _dispatch_ext_tool(name, guarded_arguments, _cm)
            return [types.TextContent(type="text", text=result_str)]
        except ValueError:
            pass

    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Transport runners
# ---------------------------------------------------------------------------

async def _run_stdio() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


async def _run_http(port: int) -> None:
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.cors import CORSMiddleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    import uvicorn

    sse = SseServerTransport("/mcp/messages/")

    async def handle_sse(request: Request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app.run(streams[0], streams[1], app.create_initialization_options())

    async def handle_root(request: Request):
        tool_names = [t["name"] for t in _all_tools]
        return JSONResponse({
            "name": "simply-connect",
            "description": "Super-contract context tools — committed context, staging, and capture",
            "transport": "sse",
            "mcp_endpoint": "/mcp/sse",
            "tools": tool_names,
        })

    async def handle_well_known_mcp(request: Request):
        return JSONResponse({
            "name": "simply-connect",
            "description": "Super-contract context tools — committed context, staging, and capture",
            "mcp_endpoint": "/mcp/sse",
            "transport": "sse",
            "protocol": "mcp",
        })

    starlette_app = Starlette(
        routes=[
            Route("/", handle_root),
            Route("/.well-known/mcp.json", handle_well_known_mcp),
            Route("/mcp/sse", handle_sse),
            Mount("/mcp/messages/", app=sse.handle_post_message),
        ],
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["GET", "POST"],
                allow_headers=["*"],
            )
        ],
    )

    config_uvicorn = uvicorn.Config(starlette_app, host="0.0.0.0", port=port)
    server = uvicorn.Server(config_uvicorn)
    await server.serve()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Super-contract MCP server")
    parser.add_argument("--http", action="store_true", help="Run HTTP/SSE transport instead of stdio")
    parser.add_argument("--port", type=int, default=None, help="HTTP port (default: $SC_MCP_PORT or 3004)")
    args = parser.parse_args()

    import asyncio
    import os

    if args.http:
        port = args.port or int(os.environ.get("SC_MCP_PORT", 3004))
        print(f"Starting simply-connect MCP server (HTTP/SSE) on port {port}...")
        asyncio.run(_run_http(port))
    else:
        asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
