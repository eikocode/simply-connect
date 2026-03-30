# Super-Contract

A committed-context agent framework — deploy it for any professional domain by swapping in a domain template.

---

## What It Is

Super-Contract is a **domain-agnostic committed-context agent framework**. The engine — three-layer memory, staging quality gate, operator/admin roles, extension tool-use loop, Telegram relay, MCP server — is shared across all deployments. What changes per deployment is the **domain**: a bundle of `AGENT.md`, `profile.json`, skeleton context files, role definitions, and optionally a live-data extension.

**Domains (from [simply-connect-domains](https://github.com/your-org/simply-connect-domains)):**
- **Decision Pack** — multi-role underwriting workflow around one canonical Decision Pack
- **Minpaku** — short-term rental management with live booking data via Minpaku API
- **Super-Landlord** — utility bill scanning, debit note generation, property/tenant tracking

**What makes it different:** nothing enters the knowledge base without passing a review gate. When you say "remember this", the update goes to staging first. An admin (human or AI) reviews it before it becomes permanent. The system improves deliberately, not accidentally.

---

## Architecture

```
Operator says "remember this"
        │
        ▼
  [Staging Layer]             ← unconfirmed, visible to agent (flagged)
  staging/*.md
        │
        ▼
  Admin: sc-admin review      ← human review, AI review, or both
        │
        ├── approve ──────────────────────────────────────────┐
        ├── reject  (discarded)                               │
        └── defer   (held for later)                          ▼
                                                    [Committed Context]
                                                    context/*.md
                                                    ← authoritative, full trust
```

### Three Layers

| Layer | Location | Trust | Owner |
|---|---|---|---|
| Committed context | `context/*.md` | Full — ground truth | Admin |
| Staging | `staging/*.md` | Partial — flagged in responses | Operator creates, Admin approves |
| Session memory | `data/sessions/` | Ephemeral — lost on exit | Automatic |

### Two Roles

| Role | Command | Can do |
|---|---|---|
| Operator | `sc` | Domain work, capture updates to staging |
| Admin | `sc-admin` | Review staging, approve/reject, intake from AIOS |

Admin can be a human, an AI process (`--auto`), or both.

---

## Domain System

A **domain** defines the agent's persona, context schema, intake mapping, role definitions, and optional live-data extension. Domains are distributed separately from the engine via [simply-connect-domains](https://github.com/your-org/simply-connect-domains).

### Domain structure

```
domains/{name}/
  profile.json        # name, context_files, category_map, extensions, roles
  AGENT.md            # agent instructions — persona, workflows, response format
  context/*.md        # skeleton context files
  roles/              # per-role AGENT.md files (multi-role deployments)
  admin/intake_map.md # human reference for intake mapping
  extension/          # legacy colocated extension layout (optional)
    tools.py          # TOOLS list + dispatch() function
    client.py         # external API wrapper
  domains/{ext_name}/extension/
    tools.py          # deployable host import path for active extensions
```

### Initialise a deployment

```bash
# Clone the domains library alongside the engine (auto-detected)
git clone https://github.com/your-org/simply-connect-domains

# Or set SC_DOMAINS_DIR explicitly
export SC_DOMAINS_DIR=/path/to/simply-connect-domains/domains

# Initialise real deployments
mkdir -p ../deployments/minpaku ../deployments/super-landlord ../deployments/decision-pack
sc-admin --data-dir ../deployments/minpaku init minpaku
sc-admin --data-dir ../deployments/super-landlord init super-landlord
sc-admin --data-dir ../deployments/decision-pack init decision-pack
```

`sc-admin init` copies the domain template into your project root. Existing files are not overwritten (use `--force` to override).
For domains with extensions such as `decision-pack`, the initialized project also carries the local extension package that the SDK runtime imports during tool use.

If you are using this workspace layout, there is also a helper script that wraps the same flow:

```bash
cd /Users/andrew/backup/work/simply-connect-workspace/deployments
./bootstrap_new_deployment.sh minpaku /path/to/deployments/minpaku
```

The helper:
- creates the target directory
- runs `sc-admin init <domain>`
- adds a minimal `.env.example`
- prints the recommended next steps

For a full local `decision-pack` walkthrough using the host extension tools:

```bash
cd /Users/andrew/backup/work/simply-connect-workspace/simply-connect
./scripts/run_decision_pack_demo.sh
./scripts/run_decision_pack_demo.sh /Users/andrew/backup/work/simply-connect-workspace/deployments/decision-pack-alt
```

For an interactive `sc` walkthrough after init:

```bash
cd /Users/andrew/backup/work/simply-connect-workspace/deployments/decision-pack
python3 -m simply_connect.cli --role founder
```

Use `/starter` inside `sc` to see role-specific starter prompts supplied by the active deployment.
Detailed prompt cookbooks and workflow walkthroughs should live with the domain docs, not the engine docs.

The intended deployment layout is one isolated directory per domain under `deployments/`, not running day-to-day sessions from the engine or template repositories.

### Multi-role deployments

A domain can declare multiple roles in `profile.json`, each with its own context filter, AGENT.md, and Telegram bot:

```json
{
  "extensions": ["minpaku"],
  "roles": {
    "operator":    {"agent_md": "roles/operator/AGENT.md",    "context_filter": ["properties","operations","pricing","contacts"], "telegram_bot_env": "MINPAKU_OPERATOR_BOT_TOKEN"},
    "host":        {"agent_md": "roles/operator/AGENT.md",    "context_filter": ["properties","operations","pricing","contacts"], "telegram_bot_env": "MINPAKU_HOST_BOT_TOKEN"},
    "guest":       {"agent_md": "roles/guest/AGENT.md",       "context_filter": ["properties"],                                   "telegram_bot_env": "MINPAKU_GUEST_BOT_TOKEN"},
    "housekeeping":{"agent_md": "roles/housekeeping/AGENT.md","context_filter": ["properties","operations"],                      "telegram_bot_env": "MINPAKU_HOUSEKEEPING_BOT_TOKEN"}
  }
}
```

Run a role-specific relay:

```bash
sc-relay --role operator
sc-relay --role guest
sc --role housekeeping
```

Sessions are namespaced by role (`operator:42`, `guest:99`) so each role has independent conversation history.

---

## Extension System

**Extensions** add live data access — things the agent can actively fetch during a conversation, beyond static committed context. Each domain's extension lives at `domains/{name}/extension/`.

Each extension provides:
- `tools.py` — Anthropic-compatible tool definitions (`TOOLS` list + `dispatch(name, args, cm) -> str`)
- `client.py` — optional external API wrapper

### Declare extensions in `profile.json`

```json
{
  "name": "Minpaku",
  "extensions": ["minpaku"]
}
```

When active, the SDK runtime runs a tool-use loop so the agent can call live-data tools mid-conversation, and the MCP server registers them for Claude Code.

The `decision-pack` domain uses this mechanism to expose its extracted shared-submission services through a local extension package in initialized projects.

### Routing Model

Inside a domain deployment, `sc` handles messages in two steps:

1. **Deterministic domain handling first**
   - Active extensions can expose `maybe_handle_message(...)`.
   - Use this for stable, high-confidence commands such as:
     - `mark Unit A available in Minpaku`
     - `show outstanding debit notes for Unit A`
     - `confirm booking bk-123 after payment verified`
   - Deterministic handlers should stay code-driven:
     - regex / string matching
     - direct context reads
     - direct API calls
     - deterministic parsing of prior session turns
   - They should not call a model internally.

2. **Domain-aware model fallback second**
   - If no deterministic handler claims the message, `sc` falls back to the deployment's normal role-aware assistant path.
   - That path may use the configured runtime/model, but it remains scoped to the active domain, role, tools, and context.

There is usually no separate generic third fallback. Inside a domain deployment, the fallback should still be domain-aware rather than becoming a cross-domain general assistant.

The contract is simple:
- deterministic handler returns a reply string -> use it and stop
- deterministic handler returns `None` -> continue to the domain-aware model path

### Minpaku extension

Tools: `list_properties`, `search_properties`, `get_bookings_by_property`

Recommended Minpaku split:
- `sc-admin` owns framework mechanics such as ingest and framework review.
- `sc --role operator` owns Minpaku business actions such as publish, update, unlist, and booking confirmation after payment verification.
- `sc-admin publish-minpaku`, `update-minpaku`, and `unlist-minpaku` remain available as compatibility helpers, but `sc --role operator` is the preferred path.

Required env vars:
```
MINPAKU_API_URL=http://your-minpaku-instance:8000
MINPAKU_API_KEY=your_api_key_here
```

---

## Document Ingestion

Ingest a document directly into staging:

```bash
sc-admin ingest water-bill-march.pdf
sc-admin ingest electricity-invoice.jpg
sc-admin ingest lease-agreement.txt
```

Claude reads the file, extracts structured content relevant to the active domain's categories, and creates staging entries. Nothing is committed until `sc-admin review` approves.

For local image-heavy workflows such as landlord bill ingestion, you can avoid API-key requirements by using Docling:

```bash
pip install -e '.[local-ingest]'
export SC_DOCUMENT_PARSER=docling
```

The `local-ingest` extra installs both:
- `docling` for local image/PDF parsing
- `pypdf` for text-based PDF extraction

### Supported formats

| Format | Method |
|---|---|
| `.txt`, `.md` | Direct text read |
| `.pdf` | Text extraction via `pypdf` (install: `pip install -e ".[pdf]"` or `pip install -e ".[local-ingest]"`) |
| `.pdf` (image-based) | Claude vision fallback or Docling |
| `.jpg`, `.jpeg`, `.png`, `.webp`, `.gif` | Claude vision or Docling |

---

## Installation

```bash
cd simply-connect
pip install -e .
```

Add your API key to `.env`:

```
ANTHROPIC_API_KEY=your_key_here
```

Clone the domains library alongside the engine for zero-config domain resolution:

```
your-workspace/
  simply-connect/           # engine
  simply-connect-domains/   # domains library (auto-detected)
```

---

## Quick Start: Operator

```bash
sc
# or with a role:
sc --role operator
```

```
  Super-Contract  ·  operator session
  ────────────────────────────────────────────────────
  Context: 3 committed files loaded  ·  0 staging entries pending
  Type /status for details  ·  /quit to exit
  ────────────────────────────────────────────────────

  You: What bookings do we have this week?
  Agent: [calls list_properties, then get_bookings_by_property]
         Here are this week's bookings across your properties...
```

**Operator commands:**

| Command | What it does |
|---|---|
| `/status` | Show committed context and staging counts |
| `/quit` | End session |

---

## Quick Start: Admin

### 1. Initialise from a domain template

```bash
sc-admin init minpaku
```

### 2. Bootstrap from AIOS

```bash
sc-admin intake
```

Reads AIOS `context/` files, extracts relevant content, creates staging entries.

### 3. Review and approve

```bash
sc-admin review        # interactive
sc-admin review --auto # AI-powered: auto-approves high-confidence, defers the rest
```

### 4. Check health

```bash
sc-admin status
```

---

## CLI Reference

### `sc` / `simply-connect` (Operator)

```
sc [--data-dir PATH] [--session SESSION_ID] [--role ROLE]
```

| Flag | Default | Description |
|---|---|---|
| `--data-dir` | auto-detected | Path to simply-connect project root |
| `--session` | new UUID | Session ID to resume a previous session |
| `--role` | operator | Role name (must match a key in profile.json roles) |

### `sc-admin` / `simply-connect-admin` (Admin)

```
sc-admin [--data-dir PATH] <command>
```

| Command | Description |
|---|---|
| `status` | Context health summary |
| `init <domain>` | Initialise deployment from a domain template |
| `init <domain> --force` | Overwrite existing files during init |
| `intake` | Bootstrap from AIOS context files |
| `ingest <file>` | Parse a document into staging entries |
| `review` | Interactive staging review |
| `review --auto` | AI-powered auto-review |

### `sc-relay` (Telegram)

```
sc-relay [--role ROLE]
```

### `sc-mcp` (MCP Server)

```
sc-mcp              # stdio — for Claude Code / CLIRuntime
sc-mcp --http       # HTTP/SSE — for WebMCP browser surface
sc-mcp --http --port 3005
```

---

## Telegram Bot (sc-relay)

### Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token.
2. Add to `.env`:
   ```
   SC_TELEGRAM_BOT_TOKEN=your_token_here
   SC_TELEGRAM_ALLOWED_USERS=your_user_id   # optional
   ```
3. Start the relay:
   ```bash
   sc-relay
   # or for a specific role:
   sc-relay --role guest
   ```

### Multi-role bots

Each role can have its own bot token, set via the env var declared in `profile.json`:

```
MINPAKU_OPERATOR_BOT_TOKEN=...
MINPAKU_GUEST_BOT_TOKEN=...
```

```bash
sc-relay --role operator # uses MINPAKU_OPERATOR_BOT_TOKEN
sc-relay --role guest    # uses MINPAKU_GUEST_BOT_TOKEN
```

### Telegram commands

| Command | What it does |
|---|---|
| `/start` | Welcome message |
| `/status` | Context health dashboard |
| `/reset` | Clear conversation history |
| `/help` | Command reference |

### Runtime selection

```
SC_CLAUDE_RUNTIME=sdk   # (default) in-process, brain.respond()
SC_CLAUDE_RUNTIME=cli   # claude -p subprocess + MCP server
```

---

## WebMCP Browser Surface

```bash
sc-mcp --http     # start on port 3004 (or $SC_MCP_PORT)
open simply-connect/webmcp.html
```

### WebMCP tools

| Tool | Description |
|---|---|
| `get_committed_context` | Read authoritative context files |
| `get_staging_entries` | Inspect pending staging queue |
| `capture_to_staging` | Create a candidate context update |

---

## Staging Entry Lifecycle

```
captured (unconfirmed)
    │
    ├── sc-admin review → approved  → promoted to context/*.md
    ├── sc-admin review → rejected  → file kept, status = rejected
    └── sc-admin review → deferred  → stays pending, review later
```

Each staging entry is a Markdown file with YAML frontmatter in `staging/`. Human-readable, git-diffable, auditable.

---

## File Structure

```
simply-connect/                     Engine repo
├── pyproject.toml                  Package definition + CLI entry points
├── profile.json                    Active domain profile for this deployment
├── AGENT.md                        Agent instructions (loaded at runtime)
├── .env.example                    Environment variable reference
├── webmcp.html                     Browser surface for WebMCP
│
├── simply-connect/                 Installable Python package (engine)
│   ├── brain.py                    Claude intelligence layer + tool-use loop
│   ├── context_manager.py          Three-layer context + roles + extensions
│   ├── ext_loader.py               Domain extension discovery and dispatch
│   ├── session_manager.py          Session persistence
│   ├── config.py                   Environment config
│   ├── cli.py                      Operator entry point (sc)
│   ├── admin_cli.py                Admin entry point (sc-admin)
│   ├── relay.py                    Telegram relay (sc-relay)
│   ├── mcp_server.py               MCP server stdio+HTTP (sc-mcp)
│   ├── tools.py                    Core MCP tool definitions
│   ├── ingestion.py                Document ingestion (txt/md/pdf/image → staging)
│   └── runtimes/
│       ├── base.py                 ClaudeRuntime ABC
│       ├── sdk.py                  SDKRuntime (in-process, tool-use loop)
│       └── cli.py                  CLIRuntime (claude -p + MCP)
│
├── context/                        Committed context (admin-controlled)
├── staging/                        Candidate updates (pending review)
├── admin/intake_map.md             Active intake map reference
├── data/sessions/                  Conversation history
│
└── tests/
    ├── test_context_manager.py
    ├── test_brain.py
    ├── test_brain_tools.py
    ├── test_extensions_loader.py
    └── test_multi_role.py

simply-connect-domains/             Domains library repo (separate)
└── domains/
    ├── minpaku/                    Short-term rental domain
    │   ├── profile.json            (extensions: ["minpaku"], roles: operator/guest/housekeeping/maintenance/finance)
    │   ├── AGENT.md
    │   ├── context/                (properties, operations, pricing, contacts)
    │   ├── roles/                  (operator, guest, housekeeping, maintenance, finance)
    │   ├── admin/intake_map.md
    │   └── extension/              Minpaku API integration
    │       ├── tools.py            list_properties, search_properties, get_bookings_by_property
    │       └── client.py           MinpakuClient (httpx)
    └── super-landlord/             Property management domain
        ├── profile.json
        ├── AGENT.md
        ├── context/                (properties, tenants, utilities, debit_notes)
        └── admin/intake_map.md
```

---

## Running Tests

```bash
cd simply-connect
pip install -e ".[dev]"
pytest tests/ -v
```

Tests use mock API clients and in-memory fixtures — no API key required.

---

## Design Philosophy

- **Honest about the mechanism.** The system accumulates context through file I/O, not model learning. Described accurately, not oversold.
- **Quality gate over volume.** Every update passes review. The knowledge base improves, not just grows.
- **Role separation.** Operators work. Admins govern. Neither can accidentally do the other's job.
- **Surface separation.** CLI, Telegram, and WebMCP are independent surfaces over the same context layer.
- **Domain separation.** Engine and domain templates are separate repos — deploy one engine, swap domains.
