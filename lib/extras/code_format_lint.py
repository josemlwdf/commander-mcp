"""Lint and format workspace files with available local tooling."""
from __future__ import annotations

import difflib
import shutil
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from ..support import inside, run_process, describe


PYTHON_SUFFIXES = {".py"}
JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".css", ".scss", ".md", ".html"}


def _run(executable: str, args: list[str], cwd: Path) -> dict:
    result = run_process([executable, *args], cwd=cwd)
    entry = {"supported": True, **result}
    # Tag entries where the tool binary itself failed to start.
    if result["exit_code"] != 0 and result.get("stderr", "").startswith("Failed to start process:"):
        entry["error_code"] = "tool_not_installed"
    return entry


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


def _skipped(tool: str, reason: str = "not_installed") -> dict:
    """Return a structured skipped-tool entry."""
    return {"tool": tool, "reason": reason}


def _unified_diff(before: str, after: str, path: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(True),
        after.splitlines(True),
        fromfile=path,
        tofile=path,
    ))


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
        Field(description=(
            "File language selection. "
            "auto: infer from extensions (.py → python; .js/.jsx/.ts/.tsx/etc. → javascript). "
            "python: run ruff and/or black. "
            "javascript: run eslint and/or prettier (also covers TypeScript .ts/.tsx files). "
            "Specify a language for directories or when auto cannot infer."
        )),
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
                if suffixes and suffixes <= PYTHON_SUFFIXES | JS_SUFFIXES:
                    raise ValueError(
                        "Paths contain both Python and JavaScript/TypeScript files. "
                        "Separate them into two calls or specify language explicitly."
                    )
                raise ValueError(
                    f"Could not infer a supported language from extensions: "
                    f"{', '.join(sorted(suffixes) or ['(none)'])}. "
                    "Supported: .py (python) and "
                    ".js/.jsx/.ts/.tsx/.mjs/.cjs/.json/.css/.scss/.md/.html (javascript)."
                )
        else:
            suffixes = {path.suffix.lower() for path in resolved if path.is_file()}
            supported_suffixes = PYTHON_SUFFIXES if language == "python" else JS_SUFFIXES
            if suffixes and not suffixes <= supported_suffixes:
                raise ValueError(f"Paths do not all match the selected {language} language")

        # Snapshot originals for diff computation when fix=True.
        originals: dict[str, str] = {}
        if fix:
            for p in resolved:
                if p.is_file():
                    try:
                        originals[str(p)] = p.read_text(encoding="utf-8")
                    except OSError:
                        pass

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
                    tools_skipped.append(_skipped("black"))
            elif black:
                if not shutil.which("ruff"):
                    tools_skipped.append(_skipped("ruff"))
                tools_run.append("black")
                command_args = ([] if fix else ["--check"]) + args
                reports.append({"tool": "black", **_run(black, command_args, root)})
            else:
                tools_skipped.extend([_skipped("ruff"), _skipped("black")])
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
                tools_skipped.append(_skipped("eslint"))
            if prettier:
                available = True
                tools_run.append("prettier")
                reports.append({"tool": "prettier", **_run(prettier, ["--write" if fix else "--check", *args], root)})
            else:
                tools_skipped.append(_skipped("prettier"))
            if not available:
                return {"supported": False, "message": "Install ESLint or Prettier locally to lint/format JavaScript or TypeScript files.", "tools_run": tools_run, "tools_skipped": tools_skipped}

        result: dict = {"supported": True, "fix": fix, "reports": reports, "tools_run": tools_run, "tools_skipped": tools_skipped}

        # Compute top-level ok: true when all tools ran and all exited 0.
        ok = bool(reports) and all(
            r.get("exit_code") == 0 and "error_code" not in r
            for r in reports
        )
        result["ok"] = ok

        # When fix=True, compute unified diffs for any files that changed.
        if fix and originals:
            diffs: dict[str, str] = {}
            for p in resolved:
                if p.is_file() and str(p) in originals:
                    try:
                        after = p.read_text(encoding="utf-8")
                    except OSError:
                        continue
                    before = originals[str(p)]
                    if after != before:
                        rel = str(p.relative_to(root))
                        diffs[rel] = _unified_diff(before, after, rel)
            result["diffs"] = diffs

        return result


def register(mcp):
    return mcp.tool(description=describe(
        "Lint or format Python and JavaScript/TypeScript with installed tools; fix=true applies changes. "
        "ok is true when all tools ran successfully (exit_code 0). "
        "tools_skipped entries include the tool name and the reason (e.g. not_installed). "
        "Report entries include error_code='tool_not_installed' when a tool binary cannot be started. "
        "When fix=true, diffs contains unified diffs of changed files. "
        "When to use: Check or format selected files."
    ))(code_format_lint)
