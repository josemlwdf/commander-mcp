from pathlib import Path
from pydantic import BaseModel
from .support import inside, MAX_READ_BYTES, describe

class LineRange(BaseModel):
    path: str
    start: int
    end: int

def register(mcp):
    @mcp.tool(description=describe("Read UTF-8 workspace files, optionally restricting each file to 1-based inclusive line ranges. Maximum size per file is 2 MiB."))
    def workspace_read_files(paths: list[str], line_ranges: list[LineRange] | None = None) -> dict:
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
            output.append({"path":str(p.relative_to(__import__('lib.support',fromlist=['ROOT']).ROOT)),"content":content})
        return {"files":output}
