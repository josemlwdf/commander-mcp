#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, sys
from pathlib import Path

from lib import ide_detector


def _is_vscode_process_dir(path: Path) -> bool:
    """Check whether a path points to a VS Code application/process directory."""
    return ide_detector._is_vscode_process_dir(path)


def _resolve_root(candidate: str | None = None) -> str:
    """Determine the workspace root, recovering it from IDE settings if needed."""
    script_dir = Path(__file__).resolve().parent
    detected = ide_detector.detect_working_dir(candidate, script_dir=script_dir)
    return detected or str(script_dir)


try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:
    if exc.name != "mcp":
        raise
    print(
        "[handy-coder-mcp] Missing required dependency 'mcp'. "
        "Install the project with `python -m pip install .` using the "
        "same Python interpreter configured for the MCP client.",
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

from lib import support, worker_supervisor
from lib.core import (
    read_files, apply_edits, terminal_exec, code_search, find_files,
    diagnostics, run_tests, git_manage, system_info,
)
from lib.extras import (
    lsp_lookup, semantic_search, code_format_lint, web_fetch, debugger, browser,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Workspace-confined Handy Coder MCP server")
    parser.add_argument("-r", "--root", default=None, help="Authorized workspace root")
    parser.add_argument("-s", "--shell", default=None, help="Override command shell")
    args = parser.parse_args()

    resolved_root = _resolve_root(args.root)
    try:
        support.configure(resolved_root, args.shell)
    except Exception as exc:
        print(f"[handy-coder-mcp] Startup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    detected_ide = ide_detector.detect_ide()
    ide_info = f"; ide: {detected_ide}" if detected_ide else ""
    print(f"[handy-coder-mcp] Authorized root: {support.ROOT}; shell: {support.SHELL}{ide_info}", file=sys.stderr)
    mcp = FastMCP("handy-coder-mcp")
    for module in (
        read_files, apply_edits, terminal_exec, code_search, find_files,
        diagnostics, run_tests, git_manage, lsp_lookup, semantic_search,
        code_format_lint, web_fetch, debugger, browser, system_info,
    ):
        module.register(mcp)

    worker_supervisor.supervise_fastmcp_tools(mcp)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
