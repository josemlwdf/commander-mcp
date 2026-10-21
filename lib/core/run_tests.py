import json
import configparser
import tomllib
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

def register(mcp):
    @mcp.tool(description=describe(
        "Run configured Jest, Vitest, or pytest tests; unfiltered runs require recognized test setup and filter_path is workspace-confined. "
        "When to use: Execute project tests."
    ))
    def run_tests(
        test_framework: Annotated[
            Literal["jest", "pytest", "vitest"] | None,
            Field(description="Test framework to use; omit to detect Jest, Vitest, or pytest from the workspace."),
        ] = None,
        filter_path: Annotated[
            str | None,
            Field(description="Optional workspace-relative test file or target path. When supplied, runs a targeted test without requiring workspace-wide test discovery."),
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
        return {"supported": True, "framework": framework, **run_process(cmd, cwd=root)}
