import fnmatch
import time
from typing import Annotated, Literal

from pydantic import Field
from ..support import describe, iter_workspace_files

# Common generated/build directories to skip by default
_DEFAULT_EXCLUDE_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", "dist", "build", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage", ".next",
    "target", "out", ".tox", "htmlcov",
})

MAX_RESULTS = 5000


def _globstar_match(relative_path: str, pattern: str) -> bool:
    """Return True when a pattern containing ** also matches with zero intermediate directories.

    ``src/**/*.py`` should match ``src/foo.py`` (zero intermediate dirs) in
    addition to ``src/sub/foo.py`` (one or more).  fnmatch alone won't do
    this because ``**`` is not a special wildcard in fnmatch — it just means
    "any two characters".  We handle the zero-dir case by stripping each
    ``**/`` segment and re-testing.
    """
    if "**" not in pattern:
        return False
    # Collapse every "**/" away to check the zero-intermediate-dir case.
    collapsed = pattern.replace("**/", "")
    name = relative_path.rsplit("/", 1)[-1]
    return fnmatch.fnmatch(relative_path, collapsed) or fnmatch.fnmatch(name, collapsed)


def find_files(
    glob_pattern: Annotated[str, Field(description="Non-empty glob pattern matched against workspace-relative paths and file names.")],
    exclude_pattern: Annotated[str | None, Field(description="Optional glob pattern to exclude matching workspace-relative paths.")] = None,
    include_generated: Annotated[bool, Field(description="When true, also search generated and build directories that are excluded by default.")] = False,
    file_type: Annotated[str | None, Field(description="Optional extension filter such as .py; file selects all file types.")] = None,
    min_size: Annotated[int | None, Field(ge=0, description="Optional minimum file size in bytes; files only.")] = None,
    max_size: Annotated[int | None, Field(ge=0, description="Optional maximum file size in bytes; files only.")] = None,
    sort_by: Annotated[Literal["path", "size", "modified"], Field(description="Sort results by path (default), size, or modified (last-modified time).")] = "path",
    include_metadata: Annotated[bool, Field(description="Include size_bytes and modified_time per result.")] = False,
) -> dict:
    if not glob_pattern:
        raise ValueError("glob_pattern cannot be empty")
    if file_type not in (None, "file") and not (
        isinstance(file_type, str) and file_type.startswith(".") and len(file_type) > 1
    ):
        raise ValueError("file_type must be 'file' or a dotted extension such as '.py'")
    if sort_by not in {"path", "size", "modified"}:
        raise ValueError("sort_by must be path, size, or modified")
    if min_size is not None and max_size is not None and min_size > max_size:
        raise ValueError("min_size cannot exceed max_size")
    errors = []
    scoped_glob = glob_pattern if "/" in glob_pattern or "\\" in glob_pattern else None
    found = []
    incomplete = False
    start_time = time.monotonic()
    metadata: dict[str, dict] = {}
    for path, relative_path in iter_workspace_files(path_glob=scoped_glob, errors=errors):
        if time.monotonic() - start_time > 15.0:
            incomplete = True
            errors.append({
                "path": ".",
                "message": (
                    "File discovery timed out after 15 s; results are partial. "
                    "Use a more specific glob_pattern or a scoped path prefix to reduce the search space."
                ),
            })
            break
        # Skip default-excluded dirs unless include_generated
        if not include_generated:
            parts = relative_path.replace("\\", "/").split("/")
            if any(part in _DEFAULT_EXCLUDE_DIRS for part in parts[:-1]):
                continue
        name = relative_path.rsplit("/", 1)[-1]
        if not (
            fnmatch.fnmatch(relative_path, glob_pattern)
            or fnmatch.fnmatch(name, glob_pattern)
            or _globstar_match(relative_path, glob_pattern)
        ):
            continue
        if exclude_pattern and fnmatch.fnmatch(relative_path, exclude_pattern):
            continue
        if file_type == "file" and not path.is_file():
            continue
        if file_type and file_type != "file" and path.suffix.lower() != file_type.lower():
            continue
        stat = path.stat()
        if min_size is not None and stat.st_size < min_size or max_size is not None and stat.st_size > max_size:
            continue
        found.append(relative_path)
        metadata[relative_path] = {"size_bytes": stat.st_size, "modified_time": stat.st_mtime}
        if len(found) >= MAX_RESULTS:
            incomplete = True
            break
    found.sort(key=(lambda item: metadata[item]["size_bytes"]) if sort_by == "size" else (lambda item: metadata[item]["modified_time"]) if sort_by == "modified" else None)
    files = [{"path": item, **metadata[item]} for item in found] if include_metadata else found
    return {"files": files, "errors": errors, "incomplete": incomplete}


def register(mcp):
    return mcp.tool(description=describe(
        "Find workspace files by glob, optionally excluding matches. "
        "The .git directory is always excluded. "
        f"Generated and build directories ({', '.join(sorted(_DEFAULT_EXCLUDE_DIRS))}) are excluded by default unless include_generated is true. "
        f"Returns at most {MAX_RESULTS} results; incomplete is true when the limit was reached. "
        "Scan failures are returned in errors with any partial matches. "
        "When to use: Discover file paths."
    ))(find_files)
