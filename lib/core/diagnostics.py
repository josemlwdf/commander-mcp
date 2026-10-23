import json
import tomllib
import os
import shutil
from pathlib import Path
from typing import Annotated

from pydantic import Field
from .. import support
from ..support import inside, run_process, describe, setup_required


def _configured_check_command(name: str, command: list[str]) -> list[str]:
    """Use a workspace virtualenv executable when it is not on the server PATH."""
    if name != "ruff":
        return command
    root = Path(support.ROOT)
    venv_dirs = (root / ".venv" / "bin", root / "venv" / "bin")
    if os.name == "nt":
        venv_dirs += (root / ".venv" / "Scripts", root / "venv" / "Scripts")
    for directory in venv_dirs:
        executable = directory / "ruff"
        if executable.is_file():
            return [str(executable), *command[1:]]
        if os.name == "nt" and executable.with_suffix(".exe").is_file():
            return [str(executable.with_suffix(".exe")), *command[1:]]
    located = shutil.which("ruff")
    return [located, *command[1:]] if located else command


def code_get_diagnostics(
    path: Annotated[str | None, Field(description="Optional workspace-relative directory to inspect; defaults to the workspace root.")] = None,
    check: Annotated[str | None, Field(description="Optional configured check to run, such as ruff, mypy, lint, typecheck, check, or validate.")] = None,
    timeout_ms: Annotated[int, Field(ge=1, le=600000, description="Diagnostic process timeout in milliseconds; defaults to 30000.")] = 30000,
) -> dict:
    try:
        base = inside(path or ".", must_exist=True)
    except FileNotFoundError:
        return {"supported": False, "ok": False, "error_code": "not_found",
                "message": f"Workspace path not found: {path}", "path": path}
    except ValueError:
        return {"supported": False, "ok": False, "error_code": "path_escape",
                "message": support.path_escape_message(path), "path": path}
    candidates = []
    detection_trace: list[dict[str, str]] = []
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
        detection_trace.append({"source": "package.json", "check": "scripts", "status": "inspected"})
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
                    detection_trace.append({"source": "package.json", "check": key, "status": "configured"})
    py = base / "pyproject.toml"
    if py.is_file():
        detection_trace.append({"source": "pyproject.toml", "check": "tools", "status": "inspected"})
        try:
            data = tomllib.loads(py.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            config_errors.append({"file": "pyproject.toml", "message": f"Could not parse pyproject.toml: {exc}"})
        else:
            detection_trace.append({"source": "pyproject.toml", "check": "configuration", "status": "inspected"})
            cfg = data.get("tool", {})
            if "ruff" in cfg:
                candidates.append(("ruff", ["ruff", "check", "."]))
                detection_trace.append({"source": "pyproject.toml", "check": "ruff", "status": "configured"})
            if "mypy" in cfg:
                candidates.append(("mypy", ["mypy", "."]))
                detection_trace.append({"source": "pyproject.toml", "check": "mypy", "status": "configured"})
    mk = base / "Makefile"
    if mk.is_file():
        detection_trace.append({"source": "Makefile", "check": "targets", "status": "inspected"})
        targets = set()
        for line in mk.read_text(encoding="utf-8").splitlines():
            if line and not line[0].isspace() and ":" in line and not line.startswith("#"):
                targets.add(line.split(":", 1)[0])
        for t in ("check", "lint", "typecheck", "validate"):
            if t in targets:
                candidates.append((t, ["make", t]))
                detection_trace.append({"source": "Makefile", "check": t, "status": "configured"})
    if check:
        available = list(dict.fromkeys(item[0] for item in candidates))
        selected = [candidate for candidate in candidates if candidate[0] == check]
        if not selected:
            return {"supported": False, "error_code": "check_not_found", "message": f"No configured diagnostic check named {check!r}.", "requested_check": check, "available_checks": available, "detection_trace": detection_trace}
        candidates = selected
    if not candidates and config_errors:
        files = ", ".join(e["file"] for e in config_errors)
        return {
            "supported": True,
            "error_code": "invalid_config",
            "ok": False,
            "message": f"Could not read diagnostics configuration; fix these files and retry: {files}.",
            "config_errors": config_errors,
            "detection_trace": detection_trace,
        }
    if not candidates:
        return {
            "supported": False,
            "message": (
                f"No supported diagnostics check found in {base} "
                "(looked for package scripts, Ruff/mypy configuration, and Makefile check/lint/typecheck/validate targets)."
            ),
            "detection_trace": detection_trace,
        }
    # Try each candidate in order, skipping tools that are not installed.
    for name, cmd in candidates:
        cmd = _configured_check_command(name, cmd)
        res = run_process(cmd, cwd=base, timeout_ms=timeout_ms)
        # Distinguish "tool not installed" from "tool ran and reported findings".
        if res["exit_code"] != 0 and res.get("stderr", "").startswith("Failed to start process:"):
            # Try the next candidate; if this is the last one, return the error.
            if (name, cmd) == candidates[-1]:
                return {
                    **setup_required(
                        missing_requirements=[f"binary:{name}"],
                        remediation_commands=[f"Install the configured {name} diagnostic tool."],
                        documentation_url="docs/SETUP.md#diagnostics",
                        message=f"The '{name}' tool is configured but could not be started.", error_code="tool_not_installed",
                    ),
                    "supported": True, "check": name, "ok": False,
                    **_history(), **res,
                }
            skipped.append({"check": name, "error_code": "tool_not_installed"})
            continue
        ok = res["exit_code"] == 0
        return {"supported": True, "check": name, "ok": ok, "detection_trace": detection_trace, **_history(), **res}
    # Unreachable but satisfies type checkers.
    return {"supported": False, "message": "No diagnostic tool could be run."}


def register(mcp):
    return mcp.tool(description=describe(
        "Detect and run the first available diagnostic tool in the selected workspace directory; path selects directory, check selects a named configured check, timeout_ms bounds execution. "
        "Detection order: package.json scripts (typecheck > check > lint), "
        "pyproject.toml tools (ruff > mypy), then Makefile targets (check > lint > typecheck > validate). "
        "Returns the check name, ok (true when exit_code is 0), exit code, stdout, stderr, and duration. "
        "Returns error_code='tool_not_installed' when the configured tool binary cannot be started; "
        "falls back to the next available candidate automatically and lists skipped candidates in skipped. "
        "A malformed package.json or pyproject.toml is reported in config_errors; if no usable candidate remains, error_code='invalid_config'. "
        "When to use: Check project health."
    ))(code_get_diagnostics)
