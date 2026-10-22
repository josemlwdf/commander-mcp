"""Lightweight health and capabilities endpoint for handy-coder-mcp."""
from __future__ import annotations

import os
import shutil

from .. import support
from ..subagents.resolve import resolve_backend
from ..support import describe


def get_capabilities() -> dict:
    """Return the server's runtime capabilities and optional-dependency status."""
    # Core binaries
    git_ok = shutil.which("git") is not None
    ruff_ok = shutil.which("ruff") is not None
    black_ok = shutil.which("black") is not None
    eslint_ok = shutil.which("eslint") is not None
    prettier_ok = shutil.which("prettier") is not None
    pytest_ok = shutil.which("pytest") is not None

    # Optional packages
    try:
        import playwright  # noqa: F401
        playwright_ok = True
    except ImportError:
        playwright_ok = False

    try:
        import sentence_transformers  # noqa: F401
        sentence_transformers_ok = True
    except ImportError:
        sentence_transformers_ok = False

    # Environment-variable gated features
    lsp_configured = bool(os.environ.get("HANDY_CODER_LSP_COMMAND", "").strip())
    dap_port_configured = bool(os.environ.get("HANDY_CODER_DAP_PORT", "").strip())
    dap_start_configured = bool(os.environ.get("HANDY_CODER_DAP_START_REQUEST", "").strip())
    dap_configured = dap_port_configured and dap_start_configured
    search_configured = bool(os.environ.get("HANDY_CODER_SEARCH_URL", "").strip())
    embedding_configured = bool(os.environ.get("HANDY_CODER_EMBEDDING_MODEL", "").strip())

    subagent_backend = resolve_backend()

    tools: dict[str, dict] = {
        # Core tools — always available
        "read_files":           {"available": True, "note": "always enabled"},
        "create_file":          {"available": True, "note": "always enabled"},
        "apply_edits":          {"available": True, "note": "always enabled"},
        "terminal_exec":        {"available": True, "note": "always enabled"},
        "search_text":          {"available": True, "note": "uses Python re, supports regex or literal text"},
        "code_search_ripgrep":  {"available": True, "note": "deprecated alias for search_text; prefer search_text for new usage"},
        "find_files":           {"available": True, "note": "always enabled"},
        "defer_commit":         {"available": True, "note": "temporarily snooze commit nudges"},
        "skills_manage":        {"available": True, "note": "list, get, or save skills; directory override via HANDY_CODER_SKILLS_DIR"},
        "orchestrator_bootstrap": {"available": True, "note": "creates .handy_coder implementation-plan.md and tasks.md"},
        "orchestrator_update_task_status": {"available": True, "note": "atomically updates [status: ...] on one tasks.md line"},
        "run_tests":            {"available": pytest_ok, "note": "pytest binary" if not pytest_ok else "pytest found"},
        "git_manage":           {"available": git_ok, "note": "git binary" if not git_ok else "git found"},
        "code_get_diagnostics": {
            "available": any([ruff_ok, black_ok, eslint_ok, prettier_ok]),
            "note": "needs ruff, black, eslint, or prettier",
        },
        # Extras
        "code_format_lint": {
            "available": any([ruff_ok, black_ok, eslint_ok, prettier_ok]),
            "tools": {
                "ruff": ruff_ok, "black": black_ok,
                "eslint": eslint_ok, "prettier": prettier_ok,
            },
        },
        "web_fetch_docs": {
            "available": True,
            "search_enabled": search_configured,
            "note": "search requires HANDY_CODER_SEARCH_URL",
        },
        "lsp_symbol_lookup": {
            "available": lsp_configured,
            "configured": lsp_configured,
            "missing_requirements": [] if lsp_configured else ["HANDY_CODER_LSP_COMMAND"],
            "note": "requires HANDY_CODER_LSP_COMMAND",
            "setup_docs": "docs/SETUP.md#lsp-lsp_symbol_lookup",
        },
        "debug_session_step": {
            "available": dap_configured,
            "configured": dap_configured,
            "missing_requirements": [
                name for name, ok in {
                    "HANDY_CODER_DAP_PORT": dap_port_configured,
                    "HANDY_CODER_DAP_START_REQUEST": dap_start_configured,
                }.items() if not ok
            ],
            "note": "requires HANDY_CODER_DAP_PORT + HANDY_CODER_DAP_START_REQUEST",
            "setup_docs": "docs/SETUP.md#dap-debug_session_step",
        },
        "vector_search_codebase": {
            "available": embedding_configured and sentence_transformers_ok,
            "configured": embedding_configured,
            "sentence_transformers_installed": sentence_transformers_ok,
            "missing_requirements": [
                *([] if embedding_configured else ["HANDY_CODER_EMBEDDING_MODEL"]),
                *([] if sentence_transformers_ok else ["sentence-transformers package"]),
            ],
            "note": "requires HANDY_CODER_EMBEDDING_MODEL + sentence-transformers package",
            "setup_docs": "docs/SETUP.md#semantic-search-vector_search_codebase",
        },
        "browser_action": {
            "available": playwright_ok,
            "configured": playwright_ok,
            "playwright_installed": playwright_ok,
            "missing_requirements": [] if playwright_ok else ["playwright package (and Chromium runtime)"],
            "note": "requires optional playwright package + Chromium",
            "setup_docs": "docs/SETUP.md#browser-automation-browser_action",
        },
        "subagent_run": {"available": True, "backend": subagent_backend, "note": "start subagent task"},
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
            "HANDY_CODER_LSP_COMMAND": lsp_configured,
            "HANDY_CODER_DAP_PORT": dap_port_configured,
            "HANDY_CODER_DAP_START_REQUEST": dap_start_configured,
            "HANDY_CODER_SEARCH_URL": search_configured,
            "HANDY_CODER_EMBEDDING_MODEL": embedding_configured,
            "SUBAGENT_BACKEND": bool(os.environ.get("SUBAGENT_BACKEND", "").strip()),
            "SUBAGENT_LOCAL": bool(os.environ.get("SUBAGENT_LOCAL", "").strip()),
        },
        "subagents": {"backend": subagent_backend},
    }


def register(mcp):
    return mcp.tool(description=describe(
        "Return server health and capability status: which tools are available, "
        "which optional binaries (git, ruff, black, eslint, prettier, pytest) are installed, "
        "and which environment-variable gated features (LSP, DAP, search, semantic search, browser) are active. "
        "For optional integrations, tool entries include missing_requirements and setup_docs where applicable. "
        "Call this first to avoid wasting turns discovering disabled tools. "
        "When to use: Check what tools are usable before planning a task."
    ))(get_capabilities)

