"""
Super-Contract — Operator CLI

Entry point registered as:
    simply-connect
    sc

Usage:
    sc
    sc --session my-session
    sc --data-dir /path/to/simply-connect

This is the operator-facing interface. Admin capabilities are in admin_cli.py.
"""

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

DIVIDER = "─" * 52


def _print_welcome(committed: dict, staging: list, profile_name: str = "Assistant", role_name: str = "operator") -> None:
    committed_count = sum(1 for v in committed.values() if v and v.strip())
    staging_count = len(staging)

    print()
    print(f"  {profile_name}  ·  {role_name} session")
    print(DIVIDER)
    print(f"  Context: {committed_count} committed files loaded", end="")
    if staging_count:
        print(f"  ·  {staging_count} staging entr{'y' if staging_count == 1 else 'ies'} pending review")
    else:
        print()
    print(f"  Type /status for details  ·  /starter for example prompts  ·  /quit to exit")
    print(DIVIDER)
    print()


def _print_status(cm) -> None:
    summary = cm.status_summary()
    print()
    print("  Committed context:")
    for info in summary["committed"]:
        words = info["words"]
        mtime = info["last_modified"]
        status_str = f"{words} words  ·  last modified {mtime}" if words else "(empty)"
        print(f"    {info['file']:<22} {status_str}")

    counts = summary["staging"]
    total = sum(counts.values())
    print()
    print(f"  Staging ({total} total):")
    print(
        f"    {counts.get('unconfirmed', 0)} unconfirmed  ·  "
        f"{counts.get('approved', 0)} approved  ·  "
        f"{counts.get('rejected', 0)} rejected  ·  "
        f"{counts.get('deferred', 0)} deferred"
    )
    print()


def _print_starters(prompts: list[str], role_name: str) -> None:
    print()
    print(f"  Starter prompts for {role_name}:")
    for prompt in prompts:
        print(f"    - {prompt}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Super-Contract — operator session",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run sc-admin for context review and intake.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Path to the simply-connect project root (auto-detected by default)",
    )
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        help="Session ID to resume (default: new UUID session)",
    )
    parser.add_argument(
        "--role",
        type=str,
        default=None,
        help="Role to use (must match a key in profile.json 'roles'; default: operator)",
    )
    args = parser.parse_args()

    # Late imports so entry point starts quickly
    from simply_connect.context_manager import ContextManager
    from simply_connect.config import config
    from simply_connect.runtimes import get_runtime
    from simply_connect.session_manager import SessionManager
    from simply_connect import brain

    # Initialise
    cm = ContextManager(root=args.data_dir)
    load_dotenv(cm._root / ".env", override=True)

    # Resolve role
    role_name = args.role or "operator"
    if args.role and args.role not in cm.roles:
        print(f"  Warning: role '{args.role}' not found in profile.json — using 'operator'")
        role_name = "operator"

    session_id = args.session or str(uuid.uuid4())[:8]
    # Namespace session by role when roles are configured
    role_prefix = role_name if cm.roles else "operator"
    namespaced_session_id = f"{role_prefix}:{session_id}" if cm.roles else session_id

    # Resolve data dir from project root
    project_root = cm._root
    data_dir = project_root / "data" / "sessions"
    data_dir.mkdir(parents=True, exist_ok=True)
    sm = SessionManager(data_dir=data_dir)
    sm.init_session(namespaced_session_id, role=role_prefix)

    # Load role-filtered context and AGENT.md path
    if cm.roles and role_name in cm.roles:
        context = cm.load_context_for_role(role_name)
        agent_md_path = cm.agent_md_path_for_role(role_name)
    else:
        context = cm.load_all_context()
        agent_md_path = None
    starter_prompts = cm.starter_prompts_for_role(role_name)
    runtime = None
    runtime_name = os.getenv("SC_CLAUDE_RUNTIME", config.CLAUDE_RUNTIME)
    if runtime_name != "sdk":
        runtime = get_runtime(
            runtime_name,
            role_name=role_name,
            project_root=project_root,
            agent_md_path=agent_md_path,
        )

    history = sm.get_history(namespaced_session_id)

    _print_welcome(context["committed"], context["staging"], profile_name=cm.profile_name, role_name=role_name)

    # Conversation loop
    try:
        while True:
            try:
                user_input = input("  You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n\n  Session ended. Goodbye.")
                break

            if not user_input:
                continue

            # Refresh the full role-aware context on every turn so external
            # sc-admin approvals are visible immediately in the running session.
            if cm.roles and role_name in cm.roles:
                context = cm.load_context_for_role(role_name)
            else:
                context = cm.load_all_context()

            # Built-in commands
            if user_input.lower() in ("/quit", "/exit", "quit", "exit"):
                print("\n  Session ended. Goodbye.")
                break

            if user_input.lower() == "/status":
                _print_status(cm)
                continue

            if user_input.lower() == "/help":
                print()
                print("  Commands:")
                print("    /status  — context and staging summary")
                print("    /starter — role-specific starter prompts")
                print("    /ingest  — ingest a file into staging")
                print("    /quit    — end session")
                print()
                print("  Say 'remember this' or 'note that' to capture context.")
                print("  Admin runs sc-admin review to approve captured items.")
                print()
                continue

            if user_input.lower() == "/starter":
                _print_starters(starter_prompts, role_name)
                continue

            ingest_prefixes = ("/ingest ", "ingest ")
            if user_input.lower().startswith(ingest_prefixes):
                from simply_connect.admin_cli import ingest_to_staging

                raw_path = user_input.split(" ", 1)[1].strip()
                target = Path(raw_path)
                if not target.is_absolute():
                    target = (project_root / target).resolve()

                result = ingest_to_staging(cm, target)
                if result.get("ok"):
                    lines = [
                        f"Ingested {Path(result['filepath']).name} into staging.",
                        "",
                    ]
                    for item in result.get("entries", []):
                        lines.append(f"- Staged ({item['category']}): {item['summary']}")
                    lines.extend(
                        [
                            "",
                            "Framework approval is still required.",
                            "Next: run sc-admin review to approve and commit.",
                        ]
                    )
                else:
                    lines = [f"Ingest failed: {result.get('error', 'unknown error')}"]

                reply = "\n".join(lines)
                print()
                for line in reply.splitlines():
                    print(f"  Agent: {line}" if line else "  Agent:")
                print()
                sm.add_turn(namespaced_session_id, "user", user_input)
                sm.add_turn(namespaced_session_id, "assistant", reply)
                history = sm.get_history(namespaced_session_id)
                context["staging"] = cm.list_staging(status="unconfirmed")
                continue

            if cm.active_extensions:
                from simply_connect.ext_loader import maybe_handle_message

                direct_result = maybe_handle_message(user_input, cm, role_name=role_prefix)
                if direct_result:
                    result = {
                        "reply": direct_result,
                        "capture": None,
                        "confidence": 1.0,
                        "used_unconfirmed": False,
                        "raw_response": "",
                    }
                    reply = result.get("reply", "")
                    print()
                    for line in reply.splitlines():
                        print(f"  Agent: {line}" if line else "  Agent:")
                    print()

                    sm.add_turn(namespaced_session_id, "user", user_input)
                    sm.add_turn(namespaced_session_id, "assistant", reply)
                    history = sm.get_history(namespaced_session_id)
                    context["staging"] = cm.list_staging(status="unconfirmed")
                    continue

            if runtime is not None:
                reply = runtime.call(user_input, user_id=abs(hash(namespaced_session_id)) % (10**9))
                result = {
                    "reply": reply,
                    "capture": None,
                    "confidence": 1.0,
                    "used_unconfirmed": False,
                    "raw_response": "",
                }
            elif cm.active_extensions:
                from simply_connect.ext_loader import get_all_tools, dispatch_extension_tool

                ext_tools = get_all_tools(cm)

                def dispatch_fn(name: str, args: dict) -> str:
                    import json as _json

                    guarded_args = dict(args)
                    guarded_args["__session_role"] = role_prefix
                    if name == "capture_to_staging":
                        try:
                            entry_id = cm.create_staging_entry(
                                summary=guarded_args.get("summary", ""),
                                content=guarded_args.get("content", ""),
                                category=guarded_args.get("category", "general"),
                                source="operator",
                            )
                            context["staging"] = cm.list_staging(status="unconfirmed")
                            return _json.dumps({
                                "entry_id": entry_id,
                                "status": "pending",
                                "message": "Captured — pending admin review.",
                            })
                        except Exception as e:
                            return _json.dumps({"error": str(e)})
                    return dispatch_extension_tool(name, guarded_args, cm)

                result = brain.respond_with_tools(
                    message=user_input,
                    context=context,
                    tools=ext_tools,
                    dispatch_fn=dispatch_fn,
                    history=history,
                    role=role_prefix,
                    agent_md_path=agent_md_path,
                )
            else:
                # Send to brain
                result = brain.respond(
                    message=user_input,
                    context=context,
                    history=history,
                    role=role_prefix,
                    agent_md_path=agent_md_path,
                    categories=list(cm.CATEGORY_MAP.keys()),
                )

            reply = result.get("reply", "")
            capture = result.get("capture")

            # Handle capture — create staging entry
            if capture:
                try:
                    entry_id = cm.create_staging_entry(
                        summary=capture.get("summary", user_input[:60]),
                        content=capture.get("content", user_input),
                        category=capture.get("category", "general"),
                        source="operator",
                    )
                    log.info(f"Staging entry created: {entry_id}")
                    # Refresh staging count in context
                    context["staging"] = cm.list_staging(status="unconfirmed")
                except Exception as e:
                    log.error(f"Failed to create staging entry: {e}")

            # Print reply
            print()
            for line in reply.split("\n"):
                print(f"  Agent: {line}" if line else "")
            print()

            # Persist conversation
            sm.add_turn(namespaced_session_id, "user", user_input)
            sm.add_turn(namespaced_session_id, "assistant", reply)
            history = sm.get_history(namespaced_session_id)

    except Exception as e:
        print(f"\n  Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
