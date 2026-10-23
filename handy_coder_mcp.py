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


def _load_fastmcp():
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
    return FastMCP

from lib import support, worker_supervisor
from lib import tool_details
from lib.core import (
    read_files, create_file, apply_edits, terminal_exec, code_search, find_files,
    diagnostics, run_tests, git_manage, build_project, commit_nudge, system_info, skills, orchestrator_bootstrap, orchestrator_tasks,
)
from lib.core import code_navigator
from lib.core import web_search_tool
from lib.core import html_syntax_check
from lib.extras import (
    code_format_lint, web_fetch,
)
from lib.subagents import tools as subagent_tools


def _assert_tool_argument_types_known(mcp) -> None:
    """Fail startup if a registered JSON schema exposes an unknown argument type."""
    registered = getattr(getattr(mcp, "_tool_manager", None), "_tools", {})
    unknown = []

    def visit(schema, location: str) -> None:
        if isinstance(schema, dict):
            if schema.get("type") == "unknown":
                unknown.append(location)
            for key, value in schema.items():
                if isinstance(value, dict):
                    visit(value, f"{location}.{key}")
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        visit(item, f"{location}.{key}[{index}]")

    for name, tool in registered.items():
        schema = getattr(tool, "parameters", None) or getattr(tool, "input_schema", None)
        if schema is None:
            unknown.append(f"{name}.<schema>")
        else:
            visit(schema, name)
    assert not unknown, "MCP tool arguments have unknown JSON Schema types: " + ", ".join(unknown)


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
    FastMCP = _load_fastmcp()
    mcp = FastMCP("handy-coder-mcp")
    tool_details.install(mcp)
    for module in (
        read_files, create_file, apply_edits, terminal_exec, code_search, find_files,
        diagnostics, run_tests, git_manage, build_project, commit_nudge, html_syntax_check,
        code_format_lint, web_fetch, web_search_tool, system_info, skills, orchestrator_bootstrap, orchestrator_tasks, subagent_tools, code_navigator,
    ):
        module.register(mcp)
    _assert_tool_argument_types_known(mcp)

    try:
        result = code_navigator.index_codebase()
        if result.get("error"):
            raise RuntimeError(result["message"])
        print(f"[handy-coder-mcp] C# index: {result}", file=sys.stderr)
    except Exception as exc:
        print(f"[handy-coder-mcp] Startup indexing failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    worker_supervisor.supervise_fastmcp_tools(mcp)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
