"""Extension loader — discovers and loads domain extensions declared in profile.json.

Extensions live at: domains/{name}/extension/tools.py
Each must expose TOOLS (list) and dispatch(name, args, cm) -> str.
"""
import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context_manager import ContextManager

log = logging.getLogger(__name__)


def _ensure_project_root_on_path(project_root: Path) -> None:
    """Add project root to sys.path so 'import domains.foo' works."""
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _load_extension_module(project_root: Path, name: str):
    """Load an extension module from either the packaged or legacy project-root layout."""
    try:
        return importlib.import_module(f"domains.{name}.extension.tools")
    except ImportError as packaged_error:
        legacy_tools = project_root / "extension" / "tools.py"
        legacy_init = project_root / "extension" / "__init__.py"
        if not legacy_tools.exists():
            raise packaged_error
        package_name = f"_sc_extension_{name}"
        module_name = f"{package_name}.tools"
        existing = sys.modules.get(module_name)
        if existing is not None and getattr(existing, "__file__", None) == str(legacy_tools):
            return existing

        sys.modules.pop(module_name, None)
        sys.modules.pop(package_name, None)

        package_spec = importlib.util.spec_from_file_location(
            package_name,
            legacy_init if legacy_init.exists() else legacy_tools,
            submodule_search_locations=[str(project_root / "extension")],
        )
        if package_spec is None or package_spec.loader is None:
            raise packaged_error
        package_module = importlib.util.module_from_spec(package_spec)
        sys.modules[package_name] = package_module
        package_spec.loader.exec_module(package_module)

        spec = importlib.util.spec_from_file_location(module_name, legacy_tools)
        if spec is None or spec.loader is None:
            raise packaged_error
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module


def load_active_extensions(cm) -> list[dict]:
    """Load extension modules declared in the active profile.
    Returns list of {"name": str, "module": module} dicts.
    """
    ext_names = cm.active_extensions
    if not ext_names:
        return []

    project_root = cm._root
    _ensure_project_root_on_path(project_root)

    loaded = []
    for name in ext_names:
        try:
            mod = _load_extension_module(project_root, name)
            loaded.append({"name": name, "module": mod})
            log.info(f"Loaded extension: {name}")
        except ImportError as e:
            log.warning(f"Extension '{name}' not found or failed to import: {e}")
    return loaded


def get_all_tools(cm) -> list[dict]:
    """Return merged tool definitions from all active extensions."""
    all_tools = []
    for ext in load_active_extensions(cm):
        tools = getattr(ext["module"], "TOOLS", [])
        all_tools.extend(tools)
    return all_tools


def dispatch_extension_tool(name: str, args: dict, cm) -> str:
    """Dispatch a tool call to the appropriate extension. Raises ValueError if not found."""
    for ext in load_active_extensions(cm):
        try:
            result = ext["module"].dispatch(name, args, cm)
            return result
        except ValueError:
            continue
    raise ValueError(f"No extension handles tool: {name}")


def maybe_handle_document(file_bytes: bytes, filename: str, mime_type: str, caption: str, cm, role_name: str = "operator", **kwargs) -> str | None:
    """Give active extensions a chance to handle a document upload directly.

    Extensions can optionally expose:
        maybe_handle_document(file_bytes, filename, mime_type, caption, cm, role_name, **kwargs) -> str | None

    Returns a reply string if the extension handled the document (replaces the default
    staging behavior), or None to fall through to default ingestion.
    """
    for ext in load_active_extensions(cm):
        handler = getattr(ext["module"], "maybe_handle_document", None)
        if handler is None:
            continue
        try:
            result = handler(file_bytes, filename, mime_type, caption, cm, role_name=role_name, **kwargs)
        except TypeError:
            try:
                result = handler(file_bytes, filename, mime_type, caption, cm)
            except TypeError:
                continue
        if result:
            return result
    return None


def handle_web_onboarding_complete(data: dict, cm) -> dict | None:
    """Give active extensions a chance to run domain-specific logic after web onboarding completes.

    Extensions can optionally expose:
        handle_web_onboarding_complete(data, cm) -> dict | None

    `data` is the full request body dict. Returns a dict to merge into the response,
    or None. Non-fatal — errors are logged and skipped.
    """
    for ext in load_active_extensions(cm):
        handler = getattr(ext["module"], "handle_web_onboarding_complete", None)
        if handler is None:
            continue
        try:
            result = handler(data, cm)
            if result:
                return result
        except Exception as e:
            log.warning(f"Extension handle_web_onboarding_complete failed (non-fatal): {e}")
    return None


def get_document_schemas(cm) -> dict | None:
    """Ask the active extension for domain document schemas.

    Extensions can optionally expose:
        get_document_schemas(cm) -> dict

    The returned dict must contain:
        classify_schema:           str         — Phase A JSON schema template
        extraction_schemas:        dict[str, str] — Phase B: doc_type → schema template
        default_extraction_schema: str         — fallback for unknown doc types
        complex_doc_types:         set[str]    — these types get sonnet_model
        haiku_model:               str         — fast/cheap model name
        sonnet_model:              str         — reasoning model name

    Returns the first non-None result, or None if no extension provides schemas.
    Non-fatal — errors are logged and skipped.
    """
    for ext in load_active_extensions(cm):
        handler = getattr(ext["module"], "get_document_schemas", None)
        if handler is None:
            continue
        try:
            result = handler(cm)
            if result:
                return result
        except Exception as e:
            log.warning(f"Extension get_document_schemas failed (non-fatal): {e}")
    return None


def maybe_handle_message(message: str, cm, role_name: str = "operator", history: list[dict] | None = None, **kwargs) -> str | None:
    """Give active extensions a chance to deterministically handle a raw user message.

    Extensions can optionally expose:
        maybe_handle_message(message, cm, role_name, history, **kwargs) -> str | None

    Extra kwargs (e.g. user_id) are passed through to extensions that accept them.
    Returns the first non-empty string result, or None when no extension claims the message.
    """
    for ext in load_active_extensions(cm):
        handler = getattr(ext["module"], "maybe_handle_message", None)
        if handler is None:
            continue
        try:
            result = handler(message, cm, role_name=role_name, history=history, **kwargs)
        except TypeError:
            try:
                result = handler(message, cm, role_name=role_name, history=history)
            except TypeError:
                result = handler(message, cm, role_name=role_name)
        if result:
            return result
    return None
