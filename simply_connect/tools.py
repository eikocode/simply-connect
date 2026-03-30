"""MCP tool definitions for the committed-context agent MCP server.

Four tools expose the three-layer context architecture to Claude:
  - get_committed_context   → read authoritative context files
  - get_staging_entries     → inspect pending staging queue
  - capture_to_staging      → write a new candidate context update
  - ingest_document         → parse a document file into staging entries
"""

TOOLS = [
    {
        "name": "get_committed_context",
        "description": (
            "Read the committed (authoritative) context for this contract workspace. "
            "Returns all context files: business, parties, preferences, and contracts. "
            "Use this to ground responses in verified facts about the business and its contracts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Optional: filter to a specific category. "
                        "One of: business, parties, preferences, contracts. "
                        "Omit to return all categories."
                    ),
                }
            },
        },
    },
    {
        "name": "get_staging_entries",
        "description": (
            "List staging entries — candidate context updates captured from operator sessions "
            "that have not yet been approved and committed. "
            "Staged entries are unconfirmed: use them as hints, not facts. "
            "Always flag when information comes from staging."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Filter by status: 'pending', 'approved', 'rejected', 'deferred'. "
                        "Omit to return all pending entries."
                    ),
                }
            },
        },
    },
    {
        "name": "capture_to_staging",
        "description": (
            "Create a new staging entry — a candidate context update to be reviewed by admin "
            "before being committed to authoritative context. "
            "Use this when the operator explicitly asks to remember, note, or save something. "
            "Do NOT use for casual conversation — only capture deliberate instructions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One-line summary of what is being captured (max 80 chars).",
                },
                "content": {
                    "type": "string",
                    "description": "The full content to store in staging.",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Context category: business | parties | preferences | contracts | general"
                    ),
                },
                "source": {
                    "type": "string",
                    "description": "Source identifier, e.g. 'telegram:12345' or 'webmcp'.",
                },
            },
            "required": ["summary", "content", "category"],
        },
    },
    {
        "name": "ingest_document",
        "description": (
            "Ingest a document file (text, PDF, or image) and extract structured content "
            "into staging entries for admin review. "
            "Use this when the operator provides a document such as a utility bill, invoice, "
            "contract scan, or any file to be processed. "
            "Supported formats: .txt, .md, .pdf, .jpg, .jpeg, .png, .webp, .gif. "
            "Creates staging entries from extracted content — nothing is committed automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": (
                        "Absolute or relative path to the document file. "
                        "Supported: .txt .md .pdf .jpg .jpeg .png .webp .gif"
                    ),
                },
            },
            "required": ["filepath"],
        },
    },
]
