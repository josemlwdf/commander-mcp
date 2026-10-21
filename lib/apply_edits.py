import difflib, re
from pydantic import BaseModel
from .support import inside, describe

class Position(BaseModel):
    line: int
    col: int
class Edit(BaseModel):
    path: str
    old_str: str | None = None
    new_str: str
    position: Position | None = None

def register(mcp):
    @mcp.tool(description=describe("Apply regex-based edits to workspace files. replace_all defaults to false. Returns unified diffs."))
    def workspace_apply_edits(edits: list[Edit], replace_all: bool = False) -> dict:
        results=[]
        for edit in edits:
            name=edit.path; p=inside(name,must_exist=True)
            if not p.is_file(): raise ValueError(f"Not a file: {name}")
            before=p.read_text(encoding="utf-8"); pattern=edit.old_str
            if pattern is None: raise ValueError("Each edit requires old_str as a Python regular expression")
            flags=0
            pos=edit.position
            if pos:
                lines=before.splitlines(keepends=True); offset=sum(map(len,lines[:max(0,int(pos.line)-1)]))+max(0,int(pos.col)-1)
                match=re.search(pattern,before[offset:])
                if not match: raise ValueError(f"Pattern did not match in {name}")
                a,b=offset+match.start(),offset+match.end()
                after=before[:a]+edit.new_str+before[b:]
            else:
                after,n=re.subn(pattern,lambda _:edit.new_str,before,count=0 if replace_all else 1)
                if n==0: raise ValueError(f"Pattern did not match in {name}")
            diff=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=name,tofile=name))
            p.write_text(after,encoding="utf-8"); results.append({"path":name,"diff":diff})
        return {"edits":results}
