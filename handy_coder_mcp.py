#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from lib import support
from lib import read_files, apply_edits, terminal_exec, code_search, find_files, diagnostics, run_tests, git_manage

def main() -> None:
    parser=argparse.ArgumentParser(description="Workspace-confined Handy Coder MCP server")
    parser.add_argument("-r","--root",default=os.environ.get("HANDY_CODER_ROOT", os.environ.get("COMMANDER_ROOT", os.getcwd())))
    parser.add_argument("-s","--shell",default=None,help="Override command shell")
    args=parser.parse_args()
    try: support.configure(args.root,args.shell)
    except Exception as exc:
        print(f"[handy-coder-mcp] Startup failed: {exc}",file=sys.stderr); raise SystemExit(1)
    print(f"[handy-coder-mcp] Authorized root: {support.ROOT}; working directory: {support.WORKING_DIRECTORY}; shell: {support.SHELL}",file=sys.stderr)
    mcp=FastMCP("handy-coder-mcp")
    for module in (read_files,apply_edits,terminal_exec,code_search,find_files,diagnostics,run_tests,git_manage): module.register(mcp)
    mcp.run(transport="stdio")

if __name__=="__main__": main()
