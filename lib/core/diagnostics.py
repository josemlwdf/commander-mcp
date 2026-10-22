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
    pkg = base / "package.json"
    if pkg.is_file():
        data = json.loads(pkg.read_text(encoding="utf-8"))
        scripts = data.get("scripts", {})
        for key in ("typecheck", "check", "lint"):
            if key in scripts:
                candidates.append((key, ["npm", "run", key]))
    py = base / "pyproject.toml"
    if py.is_file():
        data = tomllib.loads(py.read_text(encoding="utf-8"))
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
                    **res,
                }
            continue
        ok = res["exit_code"] == 0
        return {"supported": True, "check": name, "ok": ok, **res}
    # Unreachable but satisfies type checkers.
    return {"supported": False, "message": "No diagnostic tool could be run."}


def register(mcp):
    return mcp.tool(description=describe(
        "Detect and run the first available diagnostic tool in the selected workspace directory. "
        "Detection order: package.json scripts (typecheck > check > lint), "
        "pyproject.toml tools (ruff > mypy), then Makefile targets (check > lint > typecheck > validate). "
        "Returns the check name, ok (true when exit_code is 0), exit code, stdout, stderr, and duration. "
        "Returns error_code='tool_not_installed' when the configured tool binary cannot be started; "
        "falls back to the next available candidate automatically. "
        "When to use: Check project health."
    ))(code_get_diagnostics)
