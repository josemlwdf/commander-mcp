import json
import sys
import importlib.util
import configparser
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from ..support import inside, run_process, describe, setup_required, path_escape_message

def _has_python_test_setup(root) -> bool:
    if any((root / name).is_file() for name in ("pytest.ini",)):
        return True
    for name in ("pyproject.toml",):
        path = root / name
        if path.is_file():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            if "ini_options" in data.get("tool", {}).get("pytest", {}):
                return True
    for name in ("setup.cfg", "tox.ini"):
        path = root / name
        if path.is_file():
            config = configparser.ConfigParser()
            config.read(path, encoding="utf-8")
            if config.has_section("tool:pytest") or config.has_section("pytest"):
                return True
    if any(root.glob("test_*.py")):
        return True
    return any(
        directory.is_dir() and (
            any(directory.glob("test_*.py")) or any(directory.glob("*_test.py"))
        )
        for directory in (root / "test", root / "tests")
    )

def _path_looks_like_test(path: Path) -> bool:
    """Return True if the path itself is/contains test files."""
    if path.is_file():
        name = path.name
        return name.startswith("test_") or name.endswith("_test.py")
    if path.is_dir():
        return (
            any(path.glob("test_*.py")) or
            any(path.glob("*_test.py")) or
            path.name in ("test", "tests")
        )
    return False

def _pytest_preflight_notes(root: Path, target: Path | None) -> list[str]:
    notes: list[str] = []
    if target is not None:
        return notes
    test_dir = root / "test"
    if test_dir.is_dir() and not (test_dir / "__init__.py").is_file():
        notes.append(
            "Found a `test/` directory without `test/__init__.py`. "
            "Some pytest/import configurations can fail to resolve local package imports; "
            "adding `test/__init__.py` can improve reliability."
        )
    return notes


def _pytest_failure_hints(output: str) -> list[str]:
    hints: list[str] = []
    if "ModuleNotFoundError: No module named 'lib'" in output:
        hints.append(
            "Pytest could not import the local `lib` package. "
            "Run tests from the workspace root, ensure `test/__init__.py` exists, "
            "and prefer invoking pytest as `python -m pytest` in tooling."
        )
    return hints


def _pytest_command() -> list[str]:
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    return ["pytest"]


def _package_config(root) -> dict:
    package = root / "package.json"
    if not package.is_file():
        return {}
    return json.loads(package.read_text(encoding="utf-8"))

def _package_runner_markers(package: dict) -> tuple[set[str], str]:
    configured = set()
    for section in ("dependencies", "devDependencies"):
        dependencies = package.get(section, {})
        if isinstance(dependencies, dict):
            configured.update(dependencies)
    scripts = package.get("scripts", {})
    script_text = " ".join(str(command) for command in scripts.values()) if isinstance(scripts, dict) else ""
    return configured, script_text

def _has_test_setup(root, framework: str) -> bool:
    if framework == "pytest":
        return _has_python_test_setup(root)
    if framework in ("jest", "vitest"):
        package = _package_config(root)
        configured, script_text = _package_runner_markers(package)
        return framework in configured or framework in script_text
    return False

def _detect_framework(root) -> str | None:
    package = _package_config(root)
    configured, script_text = _package_runner_markers(package)
    if "vitest" in configured or "vitest" in script_text:
        return "vitest"
    if "jest" in configured or "jest" in script_text:
        return "jest"
    if _has_python_test_setup(root):
        return "pytest"
    return None

def _parse_pytest_output(stdout: str) -> dict | None:
    """Extract summary counts and per-test failure identifiers from pytest terminal output.

    Handles any ordering of counts, e.g.:
      "1 failed, 3 passed, 2 warnings in 0.3s"
      "1 failed in 0.13s"
      "5 passed in 0.1s"

    Also extracts individual FAILED lines such as:
      "FAILED test/test_foo.py::TestClass::test_method - AssertionError: ..."
    """
    import re
    # Confirm there is a summary line at all before allocating counts.
    if not re.search(r'\d+\s+(?:passed|failed|error)', stdout):
        return None
    counts: dict[str, int] = {"passed": 0, "failed": 0, "errors": 0, "warnings": 0}
    for m in re.finditer(r'(\d+)\s+(passed|failed|errors?|warnings?)', stdout):
        key = m.group(2).rstrip("s")   # normalise plural: "errors" -> "error"
        if key == "error":
            key = "errors"
        if key == "warning":
            key = "warnings"
        if key in counts:
            counts[key] = int(m.group(1))

    # Extract individual FAILED test identifiers.
    failures = []
    for m in re.finditer(r'^FAILED\s+([\w/\\.\-]+::[\w:.\-]+)(?: - (.*))?$', stdout, re.MULTILINE):
        test_id = m.group(1).strip()
        message = (m.group(2) or "").strip()
        parts = test_id.split("::", 1)
        entry: dict = {"test_id": test_id}
        if len(parts) == 2:
            entry["file"] = parts[0]
        if message:
            entry["message"] = message
        failures.append(entry)

    result = dict(counts)
    if failures:
        result["failures"] = failures
    return result


def _parse_js_test_output(stdout: str) -> dict | None:
    """Extract common Jest/Vitest test summary counts and failed test names."""
    import re
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    found = False
    for label, key in (("passed", "passed"), ("failed", "failed"),
                       ("skipped", "skipped"), ("pending", "skipped"), ("todo", "skipped")):
        matches = re.findall(rf"(\d+)\s+{label}\b", stdout, re.I)
        if matches:
            counts[key] = sum(map(int, matches))
            found = True
    failures = [{"test_id": m.group(1)} for m in re.finditer(r"^\s*[×✗✕❯●]\s+(.+?)\s*$", stdout, re.M)]
    if not found:
        return None
    if failures:
        counts["failures"] = failures
    return counts


def run_tests(
    test_framework: Annotated[
        Literal["jest", "pytest", "vitest"] | None,
        Field(description="Test framework to use; omit to detect Jest, Vitest, or pytest from the workspace."),
    ] = None,
    filter_path: Annotated[
        str | None,
        Field(description="Optional workspace-relative test file or target path. When supplied and points to test files, runs without requiring workspace-wide test discovery."),
    ] = None,
    fail_fast: Annotated[bool, Field(description="Stop after the first failure where the runner supports it.")] = False,
    parallel: Annotated[int | None, Field(ge=1, le=64, description="Optional worker count; pytest requires pytest-xdist for values greater than one.")] = None,
    coverage: Annotated[bool, Field(description="Enable coverage collection when supported by the test runner.")] = False,
    verbose: Annotated[bool, Field(description="Request verbose test output.")] = False,
) -> dict:
    root = inside(".")
    framework = test_framework
    detection_trace: list[dict[str, str]] = []
    if framework and framework not in ("jest", "pytest", "vitest"):
        raise ValueError("test_framework must be jest, pytest, or vitest")
    if not framework:
        framework = _detect_framework(root)
        detection_trace.append({"source": "workspace configuration", "status": f"detected:{framework}" if framework else "no_supported_framework"})
    else:
        detection_trace.append({"source": "test_framework parameter", "status": f"selected:{framework}"})
    if not framework:
        return {
            "supported": False,
            "message": "No Jest, Vitest, or pytest setup detected.",
            "detection_trace": detection_trace,
        }
    if filter_path:
        try:
            target = inside(filter_path, must_exist=True)
        except FileNotFoundError:
            return {"supported": False, "error_code": "not_found",
                    "message": f"Test filter path not found: {filter_path}", "path": filter_path}
        except ValueError:
            return {"supported": False, "error_code": "path_escape",
                    "message": path_escape_message(filter_path), "path": filter_path}
        if not target.is_file() and not target.is_dir():
            return {"supported": False, "error_code": "not_a_file_or_directory",
                    "message": f"Test filter path is not a file or directory: {filter_path}", "path": filter_path}
    else:
        target = None
    if target is None and not _has_test_setup(root, framework):
        return {
            "supported": False,
            "message": (
                f"No {framework} test setup detected in the workspace. "
                "Provide filter_path to run a specific target, or add a recognized test configuration."
            ),
            "detection_trace": detection_trace,
        }
    preflight_notes: list[str] = []
    if framework == "pytest":
        preflight_notes = _pytest_preflight_notes(root, target)
        cmd = _pytest_command() + (["-x"] if fail_fast else []) + (["-n", str(parallel)] if parallel else []) + (["--cov"] if coverage else []) + (["-v"] if verbose else []) + ([str(target)] if target else [])
    else:
        cmd = ["npx", "--no-install", framework, "run"] + (["--bail"] if fail_fast and framework == "jest" else []) + (["--maxWorkers", str(parallel)] if parallel and framework == "jest" else []) + (["--coverage"] if coverage else []) + (["--verbose"] if verbose else []) + ([str(target)] if target else [])
    result = run_process(cmd, cwd=root)
    if result.get("stderr", "").startswith("Failed to start process:"):
        requirement = "binary:pytest" if framework == "pytest" else f"package:{framework}"
        command = "python -m pip install pytest" if framework == "pytest" else "npm install --save-dev " + framework
        return {
            **setup_required(
                missing_requirements=[requirement], remediation_commands=[command],
                documentation_url="docs/SETUP.md#test-execution-setup-run_tests-pytest",
                message=f"The configured {framework} test runner could not be started.", error_code="tool_not_installed",
            ),
            "framework": framework, "detection_trace": detection_trace, **result,
        }
    out = {"supported": True, "framework": framework, "detection_trace": detection_trace, **result}
    combined_output = result.get("stdout", "") + result.get("stderr", "")
    if framework == "pytest":
        parsed = _parse_pytest_output(combined_output)
        if parsed:
            out["parsed"] = parsed
        hints = _pytest_failure_hints(combined_output)
        if hints:
            out["hints"] = hints
        if preflight_notes:
            out["preflight_notes"] = preflight_notes
    else:
        parsed = _parse_js_test_output(combined_output)
        if parsed is not None:
            out["parsed"] = parsed
    return out


def register(mcp):
    return mcp.tool(description=describe(
        "Run configured Jest, Vitest, or pytest tests; unfiltered runs require recognized test setup. "
        "filter_path is workspace-confined; when it points to test files, discovery is skipped. "
        "Returns parsed summary counts when available; for pytest, also returns failures list with "
        "test_id, file, and message fields for each failed test, plus optional hints for common import issues. "
        "When to use: Execute project tests."
    ))(run_tests)
