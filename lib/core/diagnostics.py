import json, tomllib
from pathlib import Path
from typing import Annotated

from pydantic import Field
from ..support import inside, run_process, describe


def code_get_diagnostics(
    path: Annotated[str | None, Field(description="Optional workspace-relative directory to inspect; defaults to the workspace root.")] = None,
) -> dict:
    base = inside(path or ".", must_exist=True)
    candidates = []
    skipped: list[dict] = []
    config_errors: list[dict] = []

    def _history() -> dict:
        extra: dict = {}
        if skipped:
            extra["skipped"] = skipped
        if config_errors:
            extra["config_errors"] = config_errors
        return extra

    pkg = base / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("top-level value must be an object")
            scripts = data.get("scripts", {})
            if not isinstance(scripts, dict):
                raise ValueError("'scripts' must be an object")
        except (ValueError, UnicodeDecodeError) as exc:
            # json.JSONDecodeError is a ValueError subclass.
            config_errors.append({"file": "package.json", "message": f"Could not parse package.json: {exc}"})
        else:
            for key in ("typecheck", "check", "lint"):
                if key in scripts:
                    candidates.append((key, ["npm", "run", key]))
    py = base / "pyproject.toml"
    if py.is_file():
        try:
            data = tomllib.loads(py.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            config_errors.append({"file": "pyproject.toml", "message": f"Could not parse pyproject.toml: {exc}"})
        else:
            cfg = data.get("tool", {})
            if "ruff" in cfg:
                candidates.append(("ruff", ["ruff", "check", "."]))
            if "mypy" in cfg:
                candidates.append(("mypy", ["mypy", "."]))
    mk = base / "Makefile"
    if mk.is_file():
        targets = set()
        for line in mk.read_text(encoding="utf-8").splitlines():
            if line and not line[0].isspace() and ":" in line and not line.startswith("#"):
                targets.add(line.split(":", 1)[0])
        for t in ("check", "lint", "typecheck", "validate"):
            if t in targets:
                candidates.append((t, ["make", t]))
    if not candidates and config_errors:
        files = ", ".join(e["file"] for e in config_errors)
        return {
            "supported": True,
            "error_code": "invalid_config",
            "ok": False,
            "message": f"Could not read diagnostics configuration; fix these files and retry: {files}.",
            "config_errors": config_errors,
        }
    if not candidates:
        return {
            "supported": False,
            "message": (
                f"No supported diagnostics check found in {base} "
                "(looked for package scripts, Ruff/mypy configuration, and Makefile check/lint/typecheck/validate targets)."
            ),
        }
    # Try each candidate in order, skipping tools that are not installed.
    for name, cmd in candidates:
        res = run_process(cmd, cwd=base)
        # Distinguish "tool not installed" from "tool ran and reported findings".
        if res["exit_code"] != 0 and res.get("stderr", "").startswith("Failed to start process:"):
            # Try the next candidate; if this is the last one, return the error.
            if (name, cmd) == candidates[-1]:
                return {
                    "supported": True,
                    "check": name,
                    "error_code": "tool_not_installed",
                    "ok": False,
                    "message": (
                        f"The '{name}' tool is configured but could not be started. "
                        "Install it and try again."
                    ),
                    **_history(),
                    **res,
                }
            skipped.append({"check": name, "error_code": "tool_not_installed"})
            continue
        ok = res["exit_code"] == 0
        return {"supported": True, "check": name, "ok": ok, **_history(), **res}
    # Unreachable but satisfies type checkers.
    return {"supported": False, "message": "No diagnostic tool could be run."}


def register(mcp):
    return mcp.tool(description=describe(
        "Detect and run the first available diagnostic tool in the selected workspace directory. "
        "Detection order: package.json scripts (typecheck > check > lint), "
        "pyproject.toml tools (ruff > mypy), then Makefile targets (check > lint > typecheck > validate). "
        "Returns the check name, ok (true when exit_code is 0), exit code, stdout, stderr, and duration. "
        "Returns error_code='tool_not_installed' when the configured tool binary cannot be started; "
        "falls back to the next available candidate automatically and lists skipped candidates in skipped. "
        "A malformed package.json or pyproject.toml is reported in config_errors; if no usable candidate remains, error_code='invalid_config'. "
        "When to use: Check project health."
    ))(code_get_diagnostics)
