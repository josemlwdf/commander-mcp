"""Workspace-confined builds for common project ecosystems."""
from __future__ import annotations

import json
import importlib.util
import re
import shutil
from pathlib import Path
from typing import Annotated

from pydantic import Field

from ..support import describe, inside, path_escape_message, run_process

_MAX_TIMEOUT_MS = 600_000


def _discover(root: Path) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for directory, dirs, files in __import__("os").walk(root):
        dirs[:] = [name for name in dirs if name not in {".git", "node_modules", ".venv", "venv", "bin", "obj"}]
        path = Path(directory)
        relative = path.relative_to(root).as_posix() or "."
        if "pyproject.toml" in files:
            candidates.append({"ecosystem": "python", "path": relative, "configuration": "pyproject.toml"})
        elif "setup.py" in files:
            candidates.append({"ecosystem": "python", "path": relative, "configuration": "setup.py"})
        if "package.json" in files:
            candidates.append({"ecosystem": "node", "path": relative, "configuration": "package.json"})
        for name in files:
            if name.endswith((".csproj", ".sln", ".fsproj", ".vbproj")):
                candidates.append({"ecosystem": "dotnet", "path": relative, "configuration": name})
    return candidates


def _build_command(candidate: dict[str, str], directory: Path) -> tuple[list[str] | None, str | None]:
    ecosystem = candidate["ecosystem"]
    if ecosystem == "python":
        python = shutil.which("python") or shutil.which("python3")
        if not python:
            return None, "Python build tool unavailable: python is not installed."
        if importlib.util.find_spec("build") is None:
            return None, "Python build tool unavailable: install the 'build' package in the active Python environment."
        return [python, "-m", "build"], None
    if ecosystem == "node":
        try:
            package = json.loads((directory / "package.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"Could not read package.json: {exc}"
        if not package.get("scripts", {}).get("build"):
            return None, "package.json does not declare a build script."
        if (directory / "pnpm-lock.yaml").exists():
            manager = "pnpm"
        elif (directory / "yarn.lock").exists():
            manager = "yarn"
        elif (directory / "bun.lock").exists() or (directory / "bun.lockb").exists():
            manager = "bun"
        else:
            manager = "npm"
        executable = shutil.which(manager)
        return ([executable, "run", "build"] if executable else None), (None if executable else f"Node build tool unavailable: {manager} is not installed.")
    executable = shutil.which("dotnet")
    if not executable:
        return None, ".NET build tool unavailable: dotnet is not installed."
    config_path = directory / candidate["configuration"]
    return [executable, "build", str(config_path)], None


def _diagnostics(output: str, severity: str) -> list[dict]:
    found = []
    patterns = [
        re.compile(r"^(?P<file>.+?)\((?P<line>\d+),(?P<column>\d+)\):\s*(?P<severity>warning|error)\s*(?P<code>[^:]*):?\s*(?P<message>.*)$", re.I),
        re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<column>\d+):\s*(?P<severity>warning|error):\s*(?P<message>.*)$", re.I),
        re.compile(r"^(?P<file>.+?):(?P<line>\d+):\s*(?P<severity>warning|error):\s*(?P<message>.*)$", re.I),
        re.compile(r"^(?P<severity>warning|error):\s*(?P<message>.*)$", re.I),
    ]
    for line in output.splitlines():
        for pattern in patterns:
            match = pattern.match(line.strip())
            if match and match.group("severity").lower() == severity:
                item = match.groupdict()
                if item.get("line"):
                    item["line"] = int(item["line"])
                if item.get("column"):
                    item["column"] = int(item["column"])
                item["message"] = (item.get("message") or "").strip()
                found.append(item)
                break
    return found


def build_project(
    path: Annotated[str | None, Field(description="Workspace-relative project directory or configuration file path; omit to detect projects across the workspace.")] = None,
    timeout_ms: Annotated[int, Field(description="Build timeout in milliseconds, from 1 to 600000; defaults to 30000.")] = 30_000,
) -> dict:
    """Detect and build a Python, Node, or .NET project without invoking a shell."""
    if not 1 <= int(timeout_ms) <= _MAX_TIMEOUT_MS:
        raise ValueError("timeout_ms must be between 1 and 600000")
    root = inside(".")
    candidates = _discover(root)
    if path is not None:
        try:
            selected = inside(path, must_exist=True)
        except ValueError:
            return {"success": False, "supported": False, "error_code": "path_escape",
                    "message": path_escape_message(path), "path": path}
        except FileNotFoundError:
            return {"success": False, "supported": False, "error_code": "not_found",
                    "message": f"Workspace path not found: {path}", "path": path}
        selected_file = not selected.is_dir()
        selected_name = selected.name
        if selected_file and selected.name in {item["configuration"] for item in candidates}:
            selected = selected.parent
        if not selected.is_dir():
            raise ValueError("path must select a project directory or configuration file")
        rel = selected.relative_to(root).as_posix() or "."
        candidates = [item for item in candidates if item["path"] == rel and (not selected_file or item["configuration"] == selected_name)]
        if not candidates:
            return {"success": False, "supported": False, "error_code": "project_not_found",
                    "message": f"No supported project configuration found at '{rel}'.",
                    "target": rel, "choices": []}
    if not candidates:
        return {"success": False, "supported": False, "error_code": "project_not_found", "message": "No supported Python, Node, or .NET project configuration was found.", "choices": []}
    if len(candidates) > 1:
        return {"success": False, "supported": True, "error_code": "ambiguous_project", "message": "Select one project using path; no build was run.", "choices": candidates}
    candidate = candidates[0]
    directory = inside(candidate["path"], must_exist=True)
    command, error = _build_command(candidate, directory)
    if error:
        return {"success": False, "supported": False, "error_code": "build_tool_unavailable", "message": error, "configuration": candidate["configuration"], "target": candidate["path"]}
    result = run_process(command, cwd=directory, timeout_ms=timeout_ms)
    raw = result.get("stdout", "") + result.get("stderr", "")
    errors, warnings = _diagnostics(raw, "error"), _diagnostics(raw, "warning")
    return {
        "success": result["exit_code"] == 0 and not result.get("timed_out", False),
        "supported": True,
        "exit_code": result["exit_code"],
        "timed_out": result.get("timed_out", False),
        "configuration": candidate["configuration"],
        "target": candidate["path"],
        "summary": f"Build {'succeeded' if result['exit_code'] == 0 and not result.get('timed_out') else 'failed'} for {candidate['path']}" + (f" ({len(errors)} errors, {len(warnings)} warnings)" if errors or warnings else ""),
        "errors": errors,
        "warnings": warnings,
        "raw_output": raw,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "duration_ms": result.get("duration_ms"),
    }


def register(mcp):
    return mcp.tool(description=describe("Build one detected Python, Node, or .NET project using its declared build system. If several projects exist, supply path to select one; otherwise project choices are returned without running a build. Uses the project lockfile to select a Node package manager and returns parsed diagnostics plus raw output: raw_output is stdout followed by stderr (concatenated, not interleaved), and stdout and stderr are also returned separately. When to use: Build a workspace project and inspect structured build results."))(build_project)
