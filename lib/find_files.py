import fnmatch
from .support import inside, describe

def register(mcp):
    @mcp.tool(description=describe("Find workspace files matching a glob pattern; optional exclude_pattern filters matching paths."))
    def workspace_find_files(glob_pattern: str, exclude_pattern: str | None = None) -> dict:
        if not glob_pattern: raise ValueError("glob_pattern cannot be empty")
        root=inside("."); found=[]
        for p in root.rglob("*"):
            if not p.is_file() or ".git" in p.parts: continue
            rel=str(p.relative_to(root))
            if (fnmatch.fnmatch(rel,glob_pattern) or fnmatch.fnmatch(p.name,glob_pattern)) and not (exclude_pattern and fnmatch.fnmatch(rel,exclude_pattern)):
                found.append(rel)
        return {"files":sorted(found)}
