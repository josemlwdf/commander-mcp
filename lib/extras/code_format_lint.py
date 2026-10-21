"""Lint and format workspace files with available local tooling."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from ..support import inside, run_process, describe


PYTHON_SUFFIXES = {".py"}
JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".css", ".scss", ".md", ".html"}


def _run(executable: str, args: list[str], cwd: Path) -> dict:
    return {"supported": True, **run_process([executable, *args], cwd=cwd)}


def _tool_path(name: str, root: Path) -> str | None:
    available = shutil.which(name)
    if available:
        return available
    local = root / "node_modules" / ".bin" / name
    if not local.is_file():
        return None
    try:
        return str(inside(local, must_exist=True))
    except ValueError:
        return None


def code_format_lint(
    paths: Annotated[
        list[str],
        Field(min_length=1, description="Workspace-relative files or directories to lint or format."),
    ],
    fix: Annotated[
        bool,
        Field(description="Apply available automatic fixes and formatting changes when true; otherwise run checks without writing changes."),
    ] = False,
    language: Annotated[
        Literal["auto", "python", "javascript"],
        Field(description="File language selection. auto infers one supported language from file extensions; specify a language for directories or mixed/unrecognized paths."),
    ] = "auto",
) -> dict:
        if not paths:
            raise ValueError("paths must contain at least one workspace path")
        resolved = [inside(path, must_exist=True) for path in paths]
        if any(not path.is_file() and not path.is_dir() for path in resolved):
            raise ValueError("Every path must refer to a file or directory")
        if language not in ("auto", "python", "javascript"):
            raise ValueError("language must be auto, python, or javascript")
        if language == "auto":
            suffixes = {path.suffix.lower() for path in resolved if path.is_file()}
            if suffixes and suffixes <= PYTHON_SUFFIXES:
                language = "python"
            elif suffixes and suffixes <= JS_SUFFIXES:
                language = "javascript"
            else:
                raise ValueError("Could not infer a single supported language; specify language or separate the paths")
        else:
            suffixes = {path.suffix.lower() for path in resolved if path.is_file()}
            supported_suffixes = PYTHON_SUFFIXES if language == "python" else JS_SUFFIXES
            if suffixes and not suffixes <= supported_suffixes:
                raise ValueError(f"Paths do not all match the selected {language} language")

        args = [str(path) for path in resolved]
        root = inside(".")
        reports = []
        tools_run = []
        tools_skipped = []
        if language == "python":
            ruff = shutil.which("ruff")
            black = shutil.which("black")
            if ruff:
                tools_run.append("ruff")
                reports.append({"tool": "ruff", **_run(ruff, ["check", *(["--fix"] if fix else []), *args], root)})
                tools_run.append("ruff-format")
                format_args = ["format", *([] if fix else ["--check"]), *args]
                reports.append({"tool": "ruff-format", **_run(ruff, format_args, root)})
                if not shutil.which("black"):
                    tools_skipped.append("black")
            elif black:
                if not shutil.which("ruff"):
                    tools_skipped.append("ruff")
                tools_run.append("black")
                command_args = ([] if fix else ["--check"]) + args
                reports.append({"tool": "black", **_run(black, command_args, root)})
            else:
                tools_skipped.extend(["ruff", "black"])
                return {"supported": False, "message": "Install Ruff or Black locally to lint/format Python files.", "tools_run": tools_run, "tools_skipped": tools_skipped}
        else:
            available = False
            eslint = _tool_path("eslint", root)
            prettier = _tool_path("prettier", root)
            if eslint:
                available = True
                tools_run.append("eslint")
                reports.append({"tool": "eslint", **_run(eslint, [*(["--fix"] if fix else []), *args], root)})
            else:
                tools_skipped.append("eslint")
            if prettier:
                available = True
                tools_run.append("prettier")
                reports.append({"tool": "prettier", **_run(prettier, ["--write" if fix else "--check", *args], root)})
            else:
                tools_skipped.append("prettier")
            if not available:
                return {"supported": False, "message": "Install ESLint or Prettier locally to lint/format JavaScript or TypeScript files.", "tools_run": tools_run, "tools_skipped": tools_skipped}
        return {"supported": True, "fix": fix, "reports": reports, "tools_run": tools_run, "tools_skipped": tools_skipped}


def register(mcp):
    return mcp.tool(description=describe(
        "Lint or format Python and JavaScript/TypeScript with installed tools; fix=true applies changes. "
        "Discloses which tools ran or were skipped in tools_run and tools_skipped. "
        "When to use: Check or format selected files."
    ))(code_format_lint)

