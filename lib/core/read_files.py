from typing import Annotated

from pydantic import BaseModel, Field
from .. import support
from ..support import inside, MAX_READ_BYTES, describe

class LineRange(BaseModel):
    path: str = Field(description="Workspace-relative file path; must match a value in paths.")
    start: int = Field(description="First line to include, using 1-based numbering.")
    end: int = Field(description="Last line to include, using 1-based numbering; must be greater than or equal to start.")

def register(mcp):
    @mcp.tool(description=describe(
        "Read UTF-8 workspace files (max 2 MiB each), optionally by 1-based line range. "
        "When to use: Inspect known files."
    ))
    def read_files(
        paths: Annotated[
            list[str],
            Field(description="Workspace-relative file paths to read."),
        ],
        line_ranges: Annotated[
            list[LineRange] | None,
            Field(description="Optional 1-based line ranges for selected files. Each range's path must match a path in paths; files without a range are read in full."),
        ] = None,
    ) -> dict:
        ranges = {r.path:r for r in (line_ranges or [])}
        output=[]
        for name in paths:
            p=inside(name, must_exist=True)
            if not p.is_file(): raise ValueError(f"Not a file: {name}")
            if p.stat().st_size > MAX_READ_BYTES: raise ValueError(f"File exceeds 2 MiB read limit: {name}")
            content=p.read_text(encoding="utf-8")
            if name in ranges:
                r=ranges[name]; start=int(r.start); end=int(r.end)
                if start < 1 or end < start: raise ValueError("line ranges require 1 <= start <= end")
                content="\n".join(content.splitlines()[start-1:end])
            output.append({
                "path": str(p.relative_to(support.ROOT)),
                "resolved_path": str(p),
                "content": content,
            })
        return {"files":output}
