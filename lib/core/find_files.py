import fnmatch
from typing import Annotated

from pydantic import Field
from ..support import describe, iter_workspace_files

def register(mcp):
    @mcp.tool(description=describe(
        "Find workspace files by glob, optionally excluding matches. Scan failures are returned in errors with any partial matches. "
        "When to use: Discover file paths."
    ))
    def find_files(
        glob_pattern: Annotated[str, Field(description="Non-empty glob pattern matched against workspace-relative paths and file names.")],
        exclude_pattern: Annotated[str | None, Field(description="Optional glob pattern to exclude matching workspace-relative paths.")]=None,
    ) -> dict:
        if not glob_pattern:
            raise ValueError("glob_pattern cannot be empty")
        errors = []
        scoped_glob = glob_pattern if "/" in glob_pattern or "\\" in glob_pattern else None
        found = []
        for _, relative_path in iter_workspace_files(path_glob=scoped_glob, errors=errors):
            name = relative_path.rsplit("/", 1)[-1]
            if not (
                fnmatch.fnmatch(relative_path, glob_pattern)
                or fnmatch.fnmatch(name, glob_pattern)
            ):
                continue
            if exclude_pattern and fnmatch.fnmatch(relative_path, exclude_pattern):
                continue
            found.append(relative_path)
        return {"files": sorted(found), "errors": errors}
