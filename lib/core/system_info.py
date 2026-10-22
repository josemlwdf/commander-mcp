"""Lightweight health and capabilities endpoint for handy-coder-mcp."""
from __future__ import annotations

import os
import shutil

from .. import support
from ..subagents.resolve import resolve_backend
from ..support import describe
from ..subagents.roles import ROLES


def get_capabilities() -> dict:
    """Return the server's runtime capabilities and optional-dependency status."""
    # Core binaries
    git_ok = shutil.which("git") is not None
    ruff_ok = shutil.which("ruff") is not None
    black_ok = shutil.which("black") is not None
    eslint_ok = shutil.which("eslint") is not None
    prettier_ok = shutil.which("prettier") is not None
    pytest_ok = shutil.which("pytest") is not None

    subagent_backend = resolve_backend()

    tools: dict[str, dict] = {
        # Core tools — always available
        "read_files":           {"available": True, "note": "always enabled"},
        "create_file":          {"available": True, "note": "always enabled"},
        "apply_edits":          {"available": True, "note": "always enabled"},
        "terminal_exec":        {"available": True, "note": "always enabled"},
        "search_text":          {"available": True, "note": "uses Python re, supports regex or literal text"},
        "find_files":           {"available": True, "note": "always enabled"},
        "defer_commit":         {"available": True, "note": "temporarily snooze commit nudges"},
        "skills_manage":        {"available": True, "note": "list, get, or save skills; directory override via HANDY_CODER_SKILLS_DIR"},
        "orchestrator_bootstrap": {"available": True, "note": "creates .handy_coder implementation-plan.md and tasks.md"},
        "orchestrator_update_task_status": {"available": True, "note": "atomically updates [status: ...] on one tasks.md line"},
        "run_tests":            {"available": pytest_ok, "note": "pytest binary" if not pytest_ok else "pytest found"},
        "git_manage":           {"available": git_ok, "note": "git binary" if not git_ok else "git found"},
        "build_project":        {"available": True, "ecosystems": ["python", "node", "dotnet"], "note": "build tool availability is checked for the selected project"},
        "code_get_diagnostics": {
            "available": True,
            "estimated_checks": ["package.json typecheck/check/lint scripts", "pyproject Ruff/mypy", "Makefile check/lint/typecheck/validate targets"],
            "note": "detects and runs the first configured installed check",
        },
        # Extras
        "code_format_lint": {
            "available": any([ruff_ok, black_ok, eslint_ok, prettier_ok]),
            "tools": {
                "ruff": ruff_ok, "black": black_ok,
                "eslint": eslint_ok, "prettier": prettier_ok,
            },
        },
        "web_fetch_docs": {"available": True, "note": "fetches public HTTP(S) text"},
        "subagent_run": {"available": True, "backend": subagent_backend, "roles": {name: {"allowed_tools": list(role.allowed_tools), "max_tool_calls": role.max_tool_calls, "result_format": role.result_format} for name, role in ROLES.items()}, "note": "start subagent task"},
        "subagent_status": {"available": True, "backend": subagent_backend, "note": "poll task state"},
        "subagent_result": {"available": True, "backend": subagent_backend, "note": "fetch paged result"},
        "subagent_complete": {"available": True, "backend": subagent_backend, "note": "close delegated cooperative frame"},
        "subagent_cancel": {"available": True, "backend": subagent_backend, "note": "cancel task"},
    }

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
    return mcp.tool(description=describe(
        "Return server health and capability status: which tools are available, "
        "which optional binaries (git, ruff, black, eslint, prettier, pytest) are installed, "
        "and which optional environment-variable gated features are active. "
        "For optional integrations, tool entries include missing_requirements and setup_docs where applicable. "
        "Call this first to avoid wasting turns discovering disabled tools. "
        "When to use: Check what tools are usable before planning a task."
    ))(get_capabilities)

