import difflib, re
from typing import Annotated

from pydantic import BaseModel, Field
from ..support import inside, describe

class Position(BaseModel):
    line: int = Field(description="1-based line where the first regex match search begins.")
    col: int = Field(description="1-based column where the first regex match search begins.")

class Edit(BaseModel):
    path: str = Field(description="Workspace-relative path of the existing file to edit.")
    old_str: str | None = Field(default=None, description="Literal text to locate and replace (not a regex). Must match exactly once, or exactly the first occurrence when replace_all is false.")
    new_str: str = Field(description="Replacement text inserted literally; no regex escape sequences are interpreted.")
    position: Position | None = Field(default=None, description="Optional 1-based line and column that limits the search to the first match at or after that position.")

def register(mcp):
    @mcp.tool(description=describe(
        "Apply literal-text edits to workspace files. Validates and stages all edits before writing any; "
        "a failed edit leaves earlier edits unapplied. Returns unified diffs. "
        "old_str and new_str are literal strings, not regular expressions. "
        "When to use: Make localized changes."
    ))
    def apply_edits(
        edits: Annotated[
            list[Edit],
            Field(description="Ordered edits to apply to workspace files. Each edit specifies a file, literal text to find, and replacement text."),
        ],
        replace_all: Annotated[
            bool,
            Field(description="If true, replace every occurrence of old_str; otherwise replace only the first."),
        ] = False,
    ) -> dict:
        # --- Phase 1: Validate and stage all edits (no writes yet) ---
        staged = []  # list of (path_obj, before_text, after_text, diff_str, original_path_str)
        for edit in edits:
            name = edit.path
            p = inside(name, must_exist=True)
            if not p.is_file():
                raise ValueError(f"Not a file: {name}")
            before = p.read_text(encoding="utf-8")
            pattern = edit.old_str
            if pattern is None:
                raise ValueError("Each edit requires old_str")
            pos = edit.position
            if pos:
                lines = before.splitlines(keepends=True)
                offset = sum(map(len, lines[:max(0, int(pos.line) - 1)])) + max(0, int(pos.col) - 1)
                idx = before.find(pattern, offset)
                if idx == -1:
                    raise ValueError(f"old_str not found in {name} at or after the given position")
                after = before[:idx] + edit.new_str + before[idx + len(pattern):]
            else:
                if replace_all:
                    if pattern not in before:
                        raise ValueError(f"old_str not found in {name}")
                    after = before.replace(pattern, edit.new_str)
                else:
                    idx = before.find(pattern)
                    if idx == -1:
                        raise ValueError(f"old_str not found in {name}")
                    after = before[:idx] + edit.new_str + before[idx + len(pattern):]
            diff = "".join(difflib.unified_diff(
                before.splitlines(True), after.splitlines(True),
                fromfile=name, tofile=name,
            ))
            staged.append((p, before, after, diff, name))

        # --- Phase 2: Write all staged edits; rollback on failure ---
        written = []
        results = []
        try:
            for p, before, after, diff, name in staged:
                p.write_text(after, encoding="utf-8")
                written.append((p, before))
                results.append({
                    "path": name,
                    "resolved_path": str(p),
                    "diff": diff,
                })
        except OSError as exc:
            # Rollback already-written files
            for p_written, original in reversed(written):
                try:
                    p_written.write_text(original, encoding="utf-8")
                except OSError:
                    pass
            raise RuntimeError(f"Write failed; all edits rolled back: {exc}") from exc

        return {"edits": results}
