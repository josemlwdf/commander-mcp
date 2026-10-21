#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, sys
from pathlib import Path


def _is_vscode_process_dir(path: Path) -> bool:
    """Check whether a path points to a VS Code application/process directory."""
    path_str = str(path).lower().replace("\\", "/")
    if "microsoft vs code" in path_str or path.name.lower() in ("code", "code - insiders"):
        return True
    vscode_cwd = os.environ.get("VSCODE_CWD", "").strip()
    if vscode_cwd:
        try:
            if path.resolve() == Path(vscode_cwd).resolve():
                return True
        except (OSError, ValueError):
            pass
    return False


def _resolve_root(candidate: str | None = None) -> str:
    """Determine the workspace root, ensuring the VS Code process directory is never used."""
    # Handle unexpanded VS Code template variables
    if candidate and (candidate.startswith("${") or not candidate.strip()):
        candidate = None

    if candidate:
        p = Path(candidate).expanduser()
        if not _is_vscode_process_dir(p):
            return str(p)

    # 1. Explicit environment override
    if root := os.environ.get("HANDY_CODER_ROOT", "").strip():
        if not root.startswith("${") and not _is_vscode_process_dir(Path(root)):
            return root

    # 2. VS Code workspace folder environment variables
    for var in ("VSCODE_WORKSPACE_FOLDER", "WORKSPACE_FOLDER", "VSCODE_PROJECT_DIR"):
        if root := os.environ.get(var, "").strip():
            if not root.startswith("${") and not _is_vscode_process_dir(Path(root)):
                return root

    # 3. Check current working directory unless it is the VS Code process directory
    try:
        cwd = Path.cwd()
        if not _is_vscode_process_dir(cwd):
            return str(cwd)
    except Exception:
        pass

    # 4. Fall back to script's parent checkout directory
    script_dir = Path(__file__).resolve().parent
    return str(script_dir)


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
    diagnostics, run_tests, git_manage,
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

    print(f"[handy-coder-mcp] Authorized root: {support.ROOT}; shell: {support.SHELL}", file=sys.stderr)
    mcp = FastMCP("handy-coder-mcp")
    for module in (
        read_files, apply_edits, terminal_exec, code_search, find_files,
        diagnostics, run_tests, git_manage, lsp_lookup, semantic_search,
        code_format_lint, web_fetch, debugger, browser,
    ):
        module.register(mcp)

    worker_supervisor.supervise_fastmcp_tools(mcp)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
