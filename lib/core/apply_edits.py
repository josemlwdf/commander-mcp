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
    old_str: str = Field(description="Python regular expression pattern used to locate text to replace. Escape special regex characters like (, ), $, . with \\ for literal matching.")
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
    dry_run: Annotated[
        bool,
        Field(description="If true, validate and compute diffs for all edits but do not write any changes to disk."),
    ] = False,
) -> dict:
    # Phase 1: Validate and stage all edits in memory
    original_contents: dict[str, str] = {}
    staged_contents: dict[str, str] = {}
    results = []

    for edit_index, edit in enumerate(edits):
        name = edit.path
        p = inside(name, must_exist=True)
        if not p.is_file():
            raise ValueError(f"[edit {edit_index}] Not a file: {name}")

        resolved_key = str(p)
        if resolved_key not in original_contents:
            disk_text = p.read_text(encoding="utf-8")
            original_contents[resolved_key] = disk_text
            staged_contents[resolved_key] = disk_text

        before = staged_contents[resolved_key]
        pattern = edit.old_str
        if pattern is None:
            raise ValueError(f"[edit {edit_index}] Each edit requires old_str as a Python regular expression")

        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"[edit {edit_index}] Invalid regular expression in old_str: {exc}")

        pos = edit.position
        match_count = 0
        if pos:
            lines = before.splitlines(keepends=True)
            offset = sum(map(len, lines[:max(0, int(pos.line) - 1)])) + max(0, int(pos.col) - 1)
            match = rx.search(before[offset:])
            if not match:
                raise ValueError(f"[edit {edit_index}] Pattern did not match in {name}")
            a, b = offset + match.start(), offset + match.end()
            after = before[:a] + edit.new_str + before[b:]
            match_count = 1
        else:
            if replace_all:
                after, match_count = rx.subn(lambda _: edit.new_str, before, count=0)
                if match_count == 0:
                    raise ValueError(f"[edit {edit_index}] Pattern did not match in {name}")
            else:
                match = rx.search(before)
                if not match:
                    raise ValueError(f"[edit {edit_index}] Pattern did not match in {name}")
                after = before[:match.start()] + edit.new_str + before[match.end():]
                match_count = 1

        diff = "".join(difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            fromfile=name,
            tofile=name,
        ))
        staged_contents[resolved_key] = after
        results.append({
            "path": name,
            "diff": diff,
            "matches": match_count,
        })

    # Phase 2: Write all staged changes to disk; roll back on failure.
    # Skipped entirely when dry_run=True.
    written: list[str] = []
    if not dry_run:
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

    return {"edits": results, "dry_run": dry_run}


def register(mcp):
    return mcp.tool(description=describe(
        "Apply regex edits to workspace files; replace_all defaults to false; dry_run=true validates without writing. "
        "Validates and stages the entire batch before writing; a later invalid edit leaves earlier edits unapplied. "
        "old_str is a Python regular expression pattern (re module); new_str is literal replacement text. "
        "Error messages include the zero-based edit index ([edit N]) for easy identification. "
        "Each result entry includes matches (count of replacements made) and a unified diff. "
        "Returns unified diffs. When to use: Make localized changes."
    ))(apply_edits)
