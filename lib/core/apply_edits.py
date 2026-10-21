import difflib, re
from typing import Annotated

from pydantic import BaseModel, Field
from ..support import inside, describe

class Position(BaseModel):
    line: int = Field(description="1-based line where the first regex match search begins.")
    col: int = Field(description="1-based column where the first regex match search begins.")
class Edit(BaseModel):
    path: str = Field(description="Workspace-relative path of the existing file to edit.")
    old_str: str | None = Field(default=None, description="Required Python regular expression to locate text to replace.")
    new_str: str = Field(description="Replacement text. Python regular-expression replacement escapes are not interpreted.")
    position: Position | None = Field(default=None, description="Optional 1-based line and column that limits the search to the first match at or after that position.")

def register(mcp):
    @mcp.tool(description=describe(
        "Apply regex edits to workspace files; replace_all defaults to false. Returns unified diffs. "
        "When to use: Make localized changes."
    ))
    def apply_edits(
        edits: Annotated[
            list[Edit],
            Field(description="Ordered edits to apply to workspace files. Each edit specifies a file, regex pattern, replacement text, and optional starting position."),
        ],
        replace_all: Annotated[
            bool,
            Field(description="If true, replace every match for each edit; otherwise replace only the first match."),
        ] = False,
    ) -> dict:
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
            p.write_text(after,encoding="utf-8"); results.append({
                "path": name,
                "resolved_path": str(p),
                "diff": diff,
            })
        return {"edits":results}
