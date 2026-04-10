"""SDK runtime — in-process, calls brain.respond() or brain.respond_with_tools() directly.

Context is loaded into the system prompt on every call.
Conversation history and staging captures are managed per user_id.

Routing:
  - Profile has no active extensions → brain.respond() (single-shot, JSON, backward compatible)
  - Profile has active extensions    → brain.respond_with_tools() (tool_use loop)

Session type:
  - Framework roles (operator, admin) → writes to staging
  - Domain roles (anything else)      → writes to session memory
"""

import logging
from pathlib import Path

from .base import ClaudeRuntime

log = logging.getLogger(__name__)

# Roles that are considered framework-level and write directly to staging
_FRAMEWORK_ROLES = {"operator", "admin"}


class SDKRuntime(ClaudeRuntime):
    """Anthropic SDK runtime.

    Calls brain.respond() for profiles without extensions.
    Calls brain.respond_with_tools() for profiles with active extensions,
    running the full tool_use loop with extension tool dispatch.
    """

    def __init__(self, role_name: str = "operator") -> None:
        from ..context_manager import ContextManager
        from ..session_manager import SessionManager

        self._cm = ContextManager()
        self._sm = SessionManager()
        self._role_name = role_name
        self._session_type = "framework" if role_name in _FRAMEWORK_ROLES else "domain"

    def call(self, user_message: str, user_id: int) -> str:
        """Process a message and return Claude's reply."""
        # Namespace session by role so each role has independent history
        role_prefix = self._role_name if self._cm.roles else "operator"
        session_id = f"{role_prefix}:{user_id}"

        # Ensure session exists
        self._sm.init_session(session_id, role=role_prefix)

        # Load role-filtered context and AGENT.md path
        if self._cm.roles and self._role_name in self._cm.roles:
            context = self._cm.load_context_for_role(self._role_name)
            agent_md_path = self._cm.agent_md_path_for_role(self._role_name)
        else:
            context = self._cm.load_all_context()
            agent_md_path = None

        history = self._sm.get_history(session_id)
        working_set = self._cm.build_working_set_snapshot(role_name=self._role_name)

        active_exts = self._cm.active_extensions

        if active_exts:
            # Tool-use path — extensions are active
            import sys
            root_str = str(self._cm._root)
            if root_str not in sys.path:
                sys.path.insert(0, root_str)

            from ..ext_loader import get_all_tools, dispatch_extension_tool
            from ..brain import respond_with_tools

            ext_tools = get_all_tools(self._cm)

            # dispatch_fn handles both extension tools and capture tools
            def dispatch_fn(name: str, args: dict) -> str:
                import json as _json
                guarded_args = dict(args)
                guarded_args["__session_role"] = role_prefix

                # Domain roles use capture_to_session (ephemeral)
                if name == "capture_to_session":
                    try:
                        self._sm.add_turn(
                            session_id,
                            "capture",
                            _json.dumps({
                                "summary": guarded_args.get("summary", ""),
                                "content": guarded_args.get("content", ""),
                                "category": guarded_args.get("category", "general"),
                            }),
                        )
                        return _json.dumps({
                            "status": "noted",
                            "message": "Noted — under review.",
                        })
                    except Exception as e:
                        return _json.dumps({"error": str(e)})

                # Framework roles use capture_to_staging
                if name == "capture_to_staging":
                    try:
                        entry_id = self._cm.create_staging_entry(
                            summary=guarded_args.get("summary", ""),
                            content=guarded_args.get("content", ""),
                            category=guarded_args.get("category", "general"),
                            source=f"telegram:{user_id}",
                        )
                        return _json.dumps({
                            "entry_id": entry_id,
                            "status": "pending",
                            "message": "Captured — pending admin review.",
                        })
                    except Exception as e:
                        return _json.dumps({"error": str(e)})

                return dispatch_extension_tool(name, guarded_args, self._cm)

            result = respond_with_tools(
                message=user_message,
                context=context,
                working_set=working_set,
                tools=ext_tools,
                dispatch_fn=dispatch_fn,
                history=history,
                role=role_prefix,
                agent_md_path=agent_md_path,
                categories=list(self._cm.CATEGORY_MAP.keys()),
                session_type=self._session_type,
            )

        else:
            # Classic single-shot path — no extensions
            from ..brain import respond

            result = respond(
                message=user_message,
                context=context,
                working_set=working_set,
                history=history,
                role=role_prefix,
                agent_md_path=agent_md_path,
                categories=list(self._cm.CATEGORY_MAP.keys()),
                session_type=self._session_type,
            )

            # Handle capture — domain roles go to session, framework to staging
            capture = result.get("capture")
            if capture:
                if self._session_type == "domain":
                    # Store capture in session memory for later curation
                    import json as _json
                    self._sm.add_turn(
                        session_id,
                        "capture",
                        _json.dumps({
                            "summary": capture.get("summary", ""),
                            "content": capture.get("content", ""),
                            "category": capture.get("category", "general"),
                        }),
                    )
                else:
                    # Framework role — write to staging
                    try:
                        entry_id = self._cm.create_staging_entry(
                            summary=capture.get("summary", ""),
                            content=capture.get("content", ""),
                            category=capture.get("category", "general"),
                            source=f"telegram:{user_id}",
                        )
                        log.info(f"Staging entry created: {entry_id}")
                    except Exception:
                        log.exception("Failed to create staging entry")

        reply = result.get("reply", "")

        # Persist the turn
        self._sm.add_turn(session_id, "user", user_message)
        self._sm.add_turn(session_id, "assistant", reply)

        return reply

    def reset(self, user_id: int) -> None:
        """Clear conversation history for a user."""
        self._sm.clear(str(user_id))
        log.info(f"Session cleared for user {user_id}")
