#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, sys
from pathlib import Path


def _default_root() -> str:
    # 1. Explicit env override
    if root := os.environ.get("HANDY_CODER_ROOT", "").strip():
        return root
    # 2. VS Code workspace folder (set by some MCP clients)
    if root := os.environ.get("VSCODE_WORKSPACE_FOLDER", "").strip():
        return root
    # 3. When run directly as a script, use the script's parent directory
    script_dir = Path(__file__).resolve().parent
    if (script_dir / "pyproject.toml").exists() or (script_dir / ".git").exists():
        return str(script_dir)
    # 4. Current working directory
    return os.getcwd()
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
from lib import support
from lib.core import (
    read_files, apply_edits, terminal_exec, code_search, find_files,
    diagnostics, run_tests, git_manage,
)
from lib.extras import (
    lsp_lookup, semantic_search, code_format_lint, web_fetch, debugger, browser,
)

def main() -> None:
    parser=argparse.ArgumentParser(description="Workspace-confined Handy Coder MCP server")
    parser.add_argument("-r","--root",default=_default_root())
    parser.add_argument("-s","--shell",default=None,help="Override command shell")
    args=parser.parse_args()
    try: support.configure(args.root,args.shell)
    except Exception as exc:
        print(f"[handy-coder-mcp] Startup failed: {exc}",file=sys.stderr); raise SystemExit(1)
    print(f"[handy-coder-mcp] Authorized root: {support.ROOT}; shell: {support.SHELL}",file=sys.stderr)
    mcp=FastMCP("handy-coder-mcp")
    for module in (
        read_files, apply_edits, terminal_exec, code_search, find_files,
        diagnostics, run_tests, git_manage, lsp_lookup, semantic_search,
        code_format_lint, web_fetch, debugger, browser,
    ):
        module.register(mcp)
    mcp.run(transport="stdio")

if __name__=="__main__": main()
