"""Lightweight health and capabilities endpoint for handy-coder-mcp."""
from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

from .. import support
from ..subagents.resolve import resolve_backend
from ..support import describe
from ..subagents.roles import ROLES

_MCP = None


def _workspace_tool_available(name: str, *, python: bool = False) -> bool:
    """Check the same workspace-local tool locations used by code_format_lint."""
    root = Path(support.ROOT)
    if python:
        venv_dirs = (root / ".venv" / "bin", root / "venv" / "bin")
        if os.name == "nt":
            venv_dirs += (root / ".venv" / "Scripts", root / "venv" / "Scripts")
        for directory in venv_dirs:
            executable = directory / name
            if executable.is_file() or (os.name == "nt" and executable.with_suffix(".exe").is_file()):
                return True
    else:
        executable = root / "node_modules" / ".bin" / name
        if executable.is_file():
            return True
    return shutil.which(name) is not None


def get_capabilities() -> dict:
    """Return the server's runtime capabilities and optional-dependency status."""
    # Core binaries
    git_ok = shutil.which("git") is not None
    ruff_ok = _workspace_tool_available("ruff", python=True)
    black_ok = _workspace_tool_available("black", python=True)
    eslint_ok = _workspace_tool_available("eslint")
    jsbeautifier_ok = importlib.util.find_spec("jsbeautifier") is not None
    pytest_ok = shutil.which("pytest") is not None

    subagent_backend = resolve_backend()

    tools: dict[str, dict] = {
        # Core tools — always available
        "read_files":           {"available": True, "note": "always enabled"},
        "create_file":          {"available": True, "note": "always enabled"},
        "html_syntax_check":    {"available": True, "note": "dependency-free HTML structural syntax checker"},
        "apply_edits":          {"available": True, "note": "always enabled"},
        "terminal_exec":        {"available": True, "note": "always enabled"},
        "search_text":          {"available": True, "note": "uses Python re, supports regex or literal text"},
        "find_files":           {"available": True, "note": "always enabled"},
        "defer_commit":         {"available": True, "note": "temporarily snooze commit nudges"},
        "skills_manage":        {"available": True, "note": "list, get, or save skills; directory override via HANDY_CODER_SKILLS_DIR"},
        "orchestrator_bootstrap": {"available": True, "note": "creates .handy-coder implementation-plan.md and tasks.md"},
        "orchestrator_update_task_status": {"available": True, "note": "atomically updates [status: ...] on one tasks.md line"},
        "run_tests":            {"available": pytest_ok, "note": "pytest binary" if not pytest_ok else "pytest found"},
        "git_manage":           {"available": git_ok, "note": "git binary" if not git_ok else "git found"},
        "build_project":        {"available": True, "ecosystems": ["python", "node", "dotnet"], "note": "build tool availability is checked for the selected project"},
        "code_get_diagnostics": {
            "available": True,
            "estimated_checks": ["package.json typecheck/check/lint scripts", "pyproject Ruff/mypy", "Makefile check/lint/typecheck/validate targets"],
            "note": "detects and runs the first configured installed check",
        },
        "web_search": {"available": True, "note": "searches the public web via DuckDuckGo Lite and returns titles, snippets, and links; requires outbound network access"},
        "index_codebase": {"available": True, "note": "incrementally indexes local C# files into .handy-coder/code_index.sqlite3 (Tree-sitter and SQLite FTS5); run it before code_navigator; failures return error_code index_unavailable"},
        "code_navigator": {"available": True, "note": "search_code, get_symbol, or find_references over the local C# index; find_references is syntactic, not compiler-resolved; returns a hint when the index is empty"},
        "get_tool_details": {"available": True, "note": "returns full documentation and argument details for one registered tool; unknown names return error_code unknown_tool"},
        # Extras
        "code_format_lint": {
            "available": any([ruff_ok, black_ok, eslint_ok, jsbeautifier_ok]),
            "tools": {
                "ruff": ruff_ok, "black": black_ok,
                "eslint": eslint_ok, "jsbeautifier": jsbeautifier_ok,
            },
        },
        "web_fetch_docs": {"available": True, "note": "fetches public HTTP(S) text"},
        "subagent_run": {"available": True, "backend": subagent_backend, "roles": {name: {"allowed_tools": list(role.allowed_tools), "max_tool_calls": role.max_tool_calls, "result_format": role.result_format} for name, role in ROLES.items()}, "note": "start subagent task"},
        "subagent_status": {"available": True, "backend": subagent_backend, "note": "poll task state"},
        "subagent_result": {"available": True, "backend": subagent_backend, "note": "fetch paged result"},
        "subagent_complete": {"available": True, "backend": subagent_backend, "note": "close delegated cooperative frame"},
        "subagent_cancel": {"available": True, "backend": subagent_backend, "note": "cancel task"},
    }

    registry = getattr(getattr(_MCP, "_tool_manager", None), "_tools", {})
    if isinstance(registry, dict) and registry:
        # The registry is authoritative for exposure; retain per-tool capability
        # metadata where available and add a minimal entry for every registered tool.
        tools = {name: tools.get(name, {"available": True, "note": "registered MCP tool"})
                 for name in registry}
    else:
        for name in ("web_search", "index_codebase", "code_navigator", "get_tool_details"):
            tools.setdefault(name, {"available": True, "note": "registered MCP tool"})
    tools.setdefault("get_capabilities", {"available": True, "note": "this capability endpoint"})
    return {
        # Read at call time: support.configure() rebinds support.ROOT after import.
        "workspace_root": str(support.ROOT),
        "tools": tools,
        "env": {
            "SUBAGENT_BACKEND": bool(os.environ.get("SUBAGENT_BACKEND", "").strip()),
            "SUBAGENT_LOCAL": bool(os.environ.get("SUBAGENT_LOCAL", "").strip()),
        },
        "subagents": {"backend": subagent_backend},
    }


def register(mcp):
    global _MCP
    _MCP = mcp
    return mcp.tool(description=describe(
        "Return server health and capability status: which tools are available, "
        "which optional binaries and Python packages (git, ruff, black, eslint, jsbeautifier, pytest) are installed, "
        "and which optional environment-variable gated features are active. "
        "For optional integrations, tool entries include missing_requirements and setup_docs where applicable. "
        "Call this first to avoid wasting turns discovering disabled tools. "
        "When to use: Check what tools are usable before planning a task."
    ))(get_capabilities)

