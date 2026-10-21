import difflib, re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from ..support import inside, describe

class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line: int = Field(ge=1, description="1-based line where the first regex match search begins.")
    col: int = Field(ge=1, description="1-based column where the first regex match search begins.")

class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="Workspace-relative path of the existing file to edit.")
    old_str: str | None = Field(default=None, description="Required Python regular expression pattern used to locate text to replace.")
    new_str: str = Field(description="Literal replacement text inserted in place of matches. Escape sequences and backreferences are not interpreted.")
    position: Position | None = Field(default=None, description="Optional 1-based line and column that limits the search to the first match at or after that position.")


def apply_edits(
    edits: Annotated[
        list[Edit],
        Field(min_length=1, description="Ordered edits to apply to workspace files. Each edit specifies a file, regex pattern, replacement text, and optional starting position."),
    ],
    replace_all: Annotated[
        bool,
        Field(description="If true, replace every match for each edit; otherwise replace only the first match."),
    ] = False,
) -> dict:
    # Phase 1: Validate and stage all edits in memory
    original_contents: dict[str, str] = {}
    staged_contents: dict[str, str] = {}
    results = []

    for edit in edits:
        name = edit.path
        p = inside(name, must_exist=True)
        if not p.is_file():
            raise ValueError(f"Not a file: {name}")

        resolved_key = str(p)
        if resolved_key not in original_contents:
            disk_text = p.read_text(encoding="utf-8")
            original_contents[resolved_key] = disk_text
            staged_contents[resolved_key] = disk_text

        before = staged_contents[resolved_key]
        pattern = edit.old_str
        if pattern is None:
            raise ValueError("Each edit requires old_str as a Python regular expression")

        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid regular expression in old_str: {exc}")

        pos = edit.position
        if pos:
            lines = before.splitlines(keepends=True)
            offset = sum(map(len, lines[:max(0, int(pos.line) - 1)])) + max(0, int(pos.col) - 1)
            match = rx.search(before[offset:])
            if not match:
                raise ValueError(f"Pattern did not match in {name}")
            a, b = offset + match.start(), offset + match.end()
            after = before[:a] + edit.new_str + before[b:]
        else:
            if replace_all:
                after, n = rx.subn(lambda _: edit.new_str, before, count=0)
                if n == 0:
                    raise ValueError(f"Pattern did not match in {name}")
            else:
                match = rx.search(before)
                if not match:
                    raise ValueError(f"Pattern did not match in {name}")
                after = before[:match.start()] + edit.new_str + before[match.end():]

        diff = "".join(difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            fromfile=name,
            tofile=name,
        ))
        staged_contents[resolved_key] = after
        results.append({
            "path": name,
            "resolved_path": str(p),
            "diff": diff,
        })

    # Phase 2: Write all staged changes to disk; roll back on failure
    written: list[str] = []
    try:
        for resolved_key, new_text in staged_contents.items():
            if new_text != original_contents[resolved_key]:
                Path(resolved_key).write_text(new_text, encoding="utf-8")
                written.append(resolved_key)
    except OSError as exc:
        for key in reversed(written):
            try:
                Path(key).write_text(original_contents[key], encoding="utf-8")
            except OSError:
                pass
        raise RuntimeError(f"Write failed; all edits rolled back: {exc}") from exc

    return {"edits": results}


def register(mcp):
    return mcp.tool(description=describe(
        "Apply regex edits to workspace files; replace_all defaults to false. "
        "Validates and stages the entire batch before writing; a later invalid edit leaves earlier edits unapplied. "
        "old_str is a Python regular expression pattern used to locate text; new_str is literal replacement text. "
        "Returns unified diffs. When to use: Make localized changes."
    ))(apply_edits)
