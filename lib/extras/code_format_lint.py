"""Lint and format workspace files with available local tooling."""
from __future__ import annotations

import difflib
import importlib.util
import os
import shutil
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from ..support import inside, run_process, describe
from ..utils.python_postprocess import process_python_file


PYTHON_SUFFIXES = {".py"}
JS_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".css", ".scss", ".md", ".html"}
JS_FORMAT_SUFFIXES = {".js", ".mjs", ".cjs"}


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


def _python_tool_path(name: str, root: Path) -> str | None:
    """Prefer Python tools installed in this workspace's virtual environment."""
    if os.name == "nt":
        venv_dirs = (root / ".venv" / "Scripts", root / "venv" / "Scripts")
    else:
        venv_dirs = (root / ".venv" / "bin", root / "venv" / "bin")
    for directory in venv_dirs:
        executable = directory / name
        if executable.is_file():
            return str(executable)
        if os.name == "nt":
            executable = executable.with_suffix(".exe")
            if executable.is_file():
                return str(executable)
    return shutil.which(name)


def _jsbeautifier_module():
    """Load the optional Python JavaScript formatter, if installed."""
    try:
        import jsbeautifier
    except ImportError:
        return None
    return jsbeautifier


def _javascript_files(paths: list[Path]) -> list[Path]:
    """Return selected JavaScript files supported by jsbeautifier."""
    files = []
    for path in paths:
        candidates = path.rglob("*") if path.is_dir() else (path,)
        files.extend(candidate for candidate in candidates
                     if candidate.is_file() and candidate.suffix.lower() in JS_FORMAT_SUFFIXES)
    return sorted(set(files))


def _format_javascript(paths: list[Path], fix: bool) -> dict:
    formatter = _jsbeautifier_module()
    if formatter is None:
        return {"available": False, "report": None}

    options = formatter.default_options()
    options.indent_size = 2
    reports = []
    for path in _javascript_files(paths):
        try:
            before = path.read_text(encoding="utf-8")
            after = formatter.beautify(before, options)
            changed = before != after
            if fix and changed:
                path.write_text(after, encoding="utf-8")
            reports.append({
                "path": str(path),
                "supported": True,
                "exit_code": 0 if fix or not changed else 1,
                "stdout": "Formatted." if fix and changed else ("Already formatted." if not changed else "Formatting needed."),
                "stderr": "",
                "changed": changed,
            })
        except (OSError, UnicodeError, ValueError) as exc:
            reports.append({"path": str(path), "supported": True, "exit_code": 1,
                            "stdout": "", "stderr": str(exc)})
    return {"available": True, "report": {"tool": "jsbeautifier", "files": reports,
             "exit_code": 0 if all(item["exit_code"] == 0 for item in reports) else 1}}


def _skipped(tool: str, reason: str = "not_installed") -> dict:
    """Return a structured skipped-tool entry."""
    return {"tool": tool, "reason": reason}


def _language_error(code: str, message: str) -> dict:
    """Structured result for a language-selection failure (no tool was run)."""
    return {"supported": True, "ok": False, "error_code": code, "message": message}


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
        Literal["auto", "python", "javascript", "text"],
        Field(description=(
            "File language selection. "
            "auto: infer supported languages from file extensions (.py → python; .js/.jsx/.ts/.tsx/etc. → javascript); "
            "python: run ruff and/or black. "
            "javascript: run ESLint and jsbeautifier for .js/.mjs/.cjs files (also accepts TypeScript .ts/.tsx files for ESLint). "
            "text: successful no-op; no tools or fixes are run. "
            "Specify a language for directories or use language='auto' when a language cannot be inferred. "
            "create_file preserves submitted content by default; format_content=true enables formatting and post_process_python=true enables Python processing."
        )),
    ] = "auto",
    config_file: Annotated[str | None, Field(description="Optional workspace-relative formatter/linter config file.")] = None,
    ignore: Annotated[list[str] | None, Field(description="Optional rule codes to ignore for Ruff.")] = None,
    severity: Annotated[Literal["all", "error", "warning"], Field(description="Ruff severity filter; all by default.")] = "all",
) -> dict:
        if not paths:
            raise ValueError("paths must contain at least one workspace path")
        resolved = []
        path_errors = []
        for path in paths:
            try:
                resolved.append(inside(path, must_exist=True))
            except FileNotFoundError:
                path_errors.append({"path": path, "error_code": "not_found", "message": f"Workspace path not found: {path}"})
            except ValueError:
                path_errors.append({"path": path, "error_code": "path_escape", "message": f"Path escapes authorized workspace: {path}"})
        if path_errors:
            return {"supported": True, "ok": False, "error_code": "invalid_paths", "errors": path_errors,
                    "message": "One or more requested paths could not be used."}
        if any(not path.is_file() and not path.is_dir() for path in resolved):
            raise ValueError("Every path must refer to a file or directory")
        if language not in ("auto", "python", "javascript", "text"):
            raise ValueError("language must be auto, python, javascript, or text")
        if language == "text":
            return {
                "supported": True,
                "ok": True,
                "language": "text",
                "message": "Text selected; no formatting or fixes were run.",
                "tools_run": [],
                "tools_skipped": [],
            }
        if language == "auto":
            suffixes = {path.suffix.lower() for path in resolved if path.is_file()}
            if suffixes and suffixes <= PYTHON_SUFFIXES:
                language = "python"
            elif suffixes and suffixes <= JS_SUFFIXES:
                language = "javascript"
            else:
                if suffixes and suffixes <= PYTHON_SUFFIXES | JS_SUFFIXES:
                    return _language_error(
                        "mixed_languages",
                        "Paths contain both Python and JavaScript/TypeScript files. "
                        "Separate them into two calls or specify language explicitly."
                    )
                return _language_error(
                    "unsupported_language",
                    f"Could not infer a supported language from extensions: "
                    f"{', '.join(sorted(suffixes) or ['(none)'])}. "
                    "Supported: .py (python) and "
                    ".js/.jsx/.ts/.tsx/.mjs/.cjs/.json/.css/.scss/.md/.html (javascript)."
                )
        else:
            suffixes = {path.suffix.lower() for path in resolved if path.is_file()}
            supported_suffixes = PYTHON_SUFFIXES if language == "python" else JS_SUFFIXES
            if suffixes and not suffixes <= supported_suffixes:
                return _language_error("language_mismatch", f"Paths do not all match the selected {language} language")

        # Snapshot originals for diff computation when fix=True.
        originals: dict[str, str] = {}
        if fix:
            for p in resolved:
                candidates = p.rglob("*") if p.is_dir() else (p,)
                for candidate in candidates:
                    if not candidate.is_file():
                        continue
                    try:
                        originals[str(candidate)] = candidate.read_bytes().decode("utf-8")
                    except OSError:
                        pass

        processing = []
        if fix and language == "python":
            selected = [p for p in resolved if p.is_file() and p.suffix.lower() == ".py"]
            for p in selected:
                try:
                    processing.append(process_python_file(p))
                except (OSError, UnicodeError, SyntaxError) as exc:
                    processing.append({"path": str(p), "ok": False, "error": str(exc), "changes": [], "uncertain": []})

        args = [str(path) for path in resolved]
        config_path = str(inside(config_file, must_exist=True)) if config_file else None
        root = inside(".")
        reports = []
        tools_run = []
        tools_skipped = []
        if language == "python":
            ruff = _python_tool_path("ruff", root)
            black = _python_tool_path("black", root)
            if ruff:
                tools_run.append("ruff")
                reports.append({"tool": "ruff", **_run(ruff, ["check", *(["--fix"] if fix else []), *args], root)})
                tools_run.append("ruff-format")
                format_args = ["format", *([] if fix else ["--check"]), *args]
                reports.append({"tool": "ruff-format", **_run(ruff, format_args, root)})
                if not black:
                    tools_skipped.append(_skipped("black"))
            elif black:
                if not ruff:
                    tools_skipped.append(_skipped("ruff"))
                tools_run.append("black")
                command_args = ([] if fix else ["--check"]) + args
                reports.append({"tool": "black", **_run(black, command_args, root)})
            else:
                tools_skipped.extend([_skipped("ruff"), _skipped("black")])
                reports.append({"tool": "python-formatters", "supported": False,
                                "message": "Install Ruff or Black locally to lint/format Python files."})
        else:
            available = False
            eslint = _tool_path("eslint", root)
            jsbeautifier_result = _format_javascript(resolved, fix)
            if eslint:
                available = True
                tools_run.append("eslint")
                reports.append({"tool": "eslint", **_run(eslint, [*(["--fix"] if fix else []), *args], root)})
            else:
                tools_skipped.append(_skipped("eslint"))
            if jsbeautifier_result["available"]:
                available = True
                tools_run.append("jsbeautifier")
                reports.append(jsbeautifier_result["report"])
            else:
                tools_skipped.append(_skipped("jsbeautifier"))
            if not available:
                return {"supported": False, "message": "Install ESLint locally or install the 'javascript-format' extra for jsbeautifier.", "tools_run": tools_run, "tools_skipped": tools_skipped}

        formatter_available = not (language == "python" and not any(t in tools_run for t in ("ruff", "black")))
        result: dict = {"supported": formatter_available, "fix": fix, "reports": reports, "tools_run": tools_run, "tools_skipped": tools_skipped}
        if not formatter_available:
            result["message"] = "Install Ruff or Black locally to lint/format Python files."

        # Compute top-level ok: true when all tools ran and all exited 0.
        ok = bool(tools_run) and all(
            r.get("exit_code") == 0 and "error_code" not in r
            for r in reports
            if "exit_code" in r
        )
        ok = ok and formatter_available
        if processing:
            result["processing"] = processing
            ok = ok and all(item.get("ok", False) for item in processing)
        result["ok"] = ok

        # When fix=True, compute unified diffs for any files that changed.
        if fix and originals:
            diffs: dict[str, str] = {}
            for p in resolved:
                if p.is_file() and str(p) in originals:
                    try:
                        after = p.read_bytes().decode("utf-8")
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
        "Lint or format Python and JavaScript/TypeScript with installed tools; JavaScript formatting uses optional jsbeautifier for .js/.mjs/.cjs files; language=text is a successful no-op. "
        "create_file preserves submitted content by default; format_content=true enables formatting and post_process_python=true enables Python processing. "
        "ok is true when all tools ran successfully (exit_code 0). "
        "tools_skipped entries include the tool name and the reason (e.g. not_installed). "
        "Report entries include error_code='tool_not_installed' when a tool binary cannot be started. "
        "Language selection failures return ok=false with error_code mixed_languages, unsupported_language or language_mismatch and a message; no tool is run. "
        "When fix=true, diffs contains unified diffs of changed files. "
        "When to use: Check or format selected files."
    ))(code_format_lint)
