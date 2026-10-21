import fnmatch
from typing import Annotated

from pydantic import Field
from ..support import describe, iter_workspace_files

# Common generated/build directories to skip by default
_DEFAULT_EXCLUDE_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", "dist", "build", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage", ".next",
    "target", "out", ".tox", "htmlcov",
})

MAX_RESULTS = 5000

def register(mcp):
    @mcp.tool(description=describe(
        "Find workspace files by glob, optionally excluding matches. "
        f"Generated and build directories ({', '.join(sorted(_DEFAULT_EXCLUDE_DIRS))}) are excluded by default. "
        f"Returns at most {MAX_RESULTS} results; incomplete is true when the limit was reached. "
        "Scan failures are returned in errors with any partial matches. "
        "When to use: Discover file paths."
    ))
    def find_files(
        glob_pattern: Annotated[str, Field(description="Non-empty glob pattern matched against workspace-relative paths and file names.")],
        exclude_pattern: Annotated[str | None, Field(description="Optional glob pattern to exclude matching workspace-relative paths.")] = None,
        include_generated: Annotated[bool, Field(description="When true, also search generated and build directories that are excluded by default.")] = False,
    ) -> dict:
        if not glob_pattern:
            raise ValueError("glob_pattern cannot be empty")
        errors = []
        scoped_glob = glob_pattern if "/" in glob_pattern or "\\" in glob_pattern else None
        found = []
        incomplete = False
        for _, relative_path in iter_workspace_files(path_glob=scoped_glob, errors=errors):
            # Skip default-excluded dirs unless include_generated
            if not include_generated:
                parts = relative_path.replace("\\", "/").split("/")
                if any(part in _DEFAULT_EXCLUDE_DIRS for part in parts[:-1]):
                    continue
            name = relative_path.rsplit("/", 1)[-1]
            if not (
                fnmatch.fnmatch(relative_path, glob_pattern)
                or fnmatch.fnmatch(name, glob_pattern)
            ):
                continue
            if exclude_pattern and fnmatch.fnmatch(relative_path, exclude_pattern):
                continue
            found.append(relative_path)
            if len(found) >= MAX_RESULTS:
                incomplete = True
                break
        return {"files": sorted(found), "errors": errors, "incomplete": incomplete}
