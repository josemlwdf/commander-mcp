import json
import configparser
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field
from ..support import inside, run_process, describe

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


def run_tests(
    test_framework: Annotated[
        Literal["jest", "pytest", "vitest"] | None,
        Field(description="Test framework to use; omit to detect Jest, Vitest, or pytest from the workspace."),
    ] = None,
    filter_path: Annotated[
        str | None,
        Field(description="Optional workspace-relative test file or target path. When supplied and points to test files, runs without requiring workspace-wide test discovery."),
    ] = None,
) -> dict:
    root = inside(".")
    framework = test_framework
    if framework and framework not in ("jest", "pytest", "vitest"):
        raise ValueError("test_framework must be jest, pytest, or vitest")
    if not framework:
        framework = _detect_framework(root)
    if not framework:
        return {
            "supported": False,
            "message": "No Jest, Vitest, or pytest setup detected.",
        }
    target = inside(filter_path) if filter_path else None
    if target is None and not _has_test_setup(root, framework):
        return {
            "supported": False,
            "message": (
                f"No {framework} test setup detected in the workspace. "
                "Provide filter_path to run a specific target, or add a recognized test configuration."
            ),
        }
    if framework == "pytest":
        cmd = ["pytest"] + ([str(target)] if target else [])
    else:
        cmd = ["npx", "--no-install", framework, "run"] + ([str(target)] if target else [])
    result = run_process(cmd, cwd=root)
    out = {"supported": True, "framework": framework, **result}
    if framework == "pytest":
        parsed = _parse_pytest_output(result.get("stdout", "") + result.get("stderr", ""))
        if parsed:
            out["parsed"] = parsed
    return out


def register(mcp):
    return mcp.tool(description=describe(
        "Run configured Jest, Vitest, or pytest tests; unfiltered runs require recognized test setup. "
        "filter_path is workspace-confined; when it points to test files, discovery is skipped. "
        "Returns parsed summary counts when available; for pytest, also returns failures list with "
        "test_id, file, and message fields for each failed test. "
        "When to use: Execute project tests."
    ))(run_tests)
