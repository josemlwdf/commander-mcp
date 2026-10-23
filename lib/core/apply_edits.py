import difflib
import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from ..support import inside, describe
from ..utils.html_prettify import prettify_html


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line: int = Field(ge=1, description="1-based line where the first regex match search begins.")
    col: int = Field(ge=1, description="1-based column where the first regex match search begins.")


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="Workspace-relative path of the existing file to edit.")
    pattern: str = Field(description="Required Python regular expression used to locate text. Capture groups can be referenced with expand_groups=true.")
    replacement: str = Field(description="Required replacement text. Literal by default; with expand_groups=true, Python re capture references such as \\1 and \\g<name> are expanded.")
    position: Position | None = Field(default=None, description="Optional object with 1-based line and col keys that limits the search to the first match at or after that position.")


def _edit_error(code: str, edit_index: int, message: str, dry_run: bool, hint: str | None = None) -> dict:
    # Raised during staging, before any write, so nothing needs rolling back.
    payload = {
        "error_code": code,
        "edit_index": edit_index,
        "message": f"[edit {edit_index}] {message}",
        "edits": [],
        "dry_run": dry_run,
    }
    if hint:
        payload["hint"] = hint
    return payload


def _expand(match: re.Match, template: str, expand_groups: bool) -> str:
    # match.expand raises re.error for unknown group references or bad escapes.
    return match.expand(template) if expand_groups else template


def apply_edits(
    edits: Annotated[
        list[Edit],
        Field(min_length=1, description="Ordered edits to apply to workspace files. Each edit requires path, pattern, and replacement, and may include position with 1-based line and col keys (search start). Edits are staged in order; later edits run against earlier staged changes, including repeated edits to the same file."),
    ],
    replace_all: Annotated[
        bool,
        Field(description="If true, replace every match for each edit; otherwise replace only the first match."),
    ] = False,
    max_replacements: Annotated[int | None, Field(ge=1, description="Optional maximum replacements per edit. Applies with replace_all; when omitted, all matches are replaced.")] = None,
    case_insensitive: Annotated[bool, Field(description="Compile patterns with case-insensitive matching.")] = False,
    dry_run: Annotated[
        bool,
        Field(description="If true, validate and compute diffs for all edits but do not write any changes to disk."),
    ] = False,
    expand_groups: Annotated[
        bool,
        Field(description="If true, replacement is a Python re replacement template: \\1 and \\g<name> insert capture groups and backslash escapes are interpreted. Defaults to false (replacement text is literal)."),
    ] = False,
) -> dict:
    # Phase 1: Validate and stage all edits in memory
    original_contents: dict[str, str] = {}
    staged_contents: dict[str, str] = {}
    results = []

    for edit_index, edit in enumerate(edits):
        name = edit.path
        try:
            p = inside(name, must_exist=True)
        except FileNotFoundError:
            return _edit_error("not_found", edit_index, f"File not found: {name}", dry_run)
        except ValueError:
            return _edit_error("path_escape", edit_index, f"Path escapes authorized workspace root: {name}", dry_run)
        except OSError as exc:
            # strerror only: str(exc) embeds the absolute host path.
            return _edit_error("io_error", edit_index, f"{exc.strerror or 'File could not be accessed'}: {name}", dry_run)
        if not p.is_file():
            return _edit_error("not_a_file", edit_index, f"Not a file: {name}", dry_run)

        resolved_key = str(p)
        if resolved_key not in original_contents:
            try:
                disk_text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                return _edit_error("binary_file", edit_index, f"File is not valid UTF-8 (binary or non-text): {name}", dry_run)
            except OSError as exc:
                return _edit_error("io_error", edit_index, f"{exc.strerror or 'File could not be read'}: {name}", dry_run)
            original_contents[resolved_key] = disk_text
            staged_contents[resolved_key] = disk_text

        before = staged_contents[resolved_key]
        pattern = edit.pattern
        replacement_text = edit.replacement

        try:
            rx = re.compile(pattern, re.IGNORECASE if case_insensitive else 0)
        except re.error as exc:
            return _edit_error(
                "invalid_regex",
                edit_index,
                f"Invalid regular expression in pattern: {exc}",
                dry_run,
                hint="Use Python re syntax. For literal text, escape regex metacharacters such as . * + ? ( ) [ ] { } |.",
            )

        pos = edit.position
        match_count = 0
        if pos:
            lines = before.splitlines(keepends=True)
            offset = sum(map(len, lines[:max(0, int(pos.line) - 1)])) + max(0, int(pos.col) - 1)
            match = rx.search(before[offset:])
            if not match:
                return _edit_error(
                    "no_match",
                    edit_index,
                    f"Pattern did not match in {name}",
                    dry_run,
                    hint="Check the pattern against current staged content, or use replace_all=true if multiple matches are expected.",
                )
            a, b = offset + match.start(), offset + match.end()
            try:
                new_text = _expand(match, replacement_text, expand_groups)
            except re.error as exc:
                return _edit_error(
                    "invalid_replacement",
                    edit_index,
                    f"Invalid replacement in replacement: {exc}",
                    dry_run,
                    hint="When expand_groups=true, ensure referenced groups exist (e.g. \\1 or \\g<name>).",
                )
            after = before[:a] + new_text + before[b:]
            match_count = 1
        else:
            if replace_all:
                try:
                    after, match_count = rx.subn(lambda m: _expand(m, replacement_text, expand_groups), before, count=max_replacements or 0)
                except re.error as exc:
                    return _edit_error(
                        "invalid_replacement",
                        edit_index,
                        f"Invalid replacement in replacement: {exc}",
                        dry_run,
                        hint="When expand_groups=true, ensure referenced groups exist (e.g. \\1 or \\g<name>).",
                    )
                if match_count == 0:
                    return _edit_error(
                        "no_match",
                        edit_index,
                        f"Pattern did not match in {name}",
                        dry_run,
                        hint="Check the pattern against current staged content, or use replace_all=true if multiple matches are expected.",
                    )
            else:
                match = rx.search(before)
                if not match:
                    return _edit_error(
                        "no_match",
                        edit_index,
                        f"Pattern did not match in {name}",
                        dry_run,
                        hint="Check the pattern against current staged content, or use replace_all=true if multiple matches are expected.",
                    )
                try:
                    new_text = _expand(match, replacement_text, expand_groups)
                except re.error as exc:
                    return _edit_error(
                        "invalid_replacement",
                        edit_index,
                        f"Invalid replacement in replacement: {exc}",
                        dry_run,
                        hint="When expand_groups=true, ensure referenced groups exist (e.g. \\1 or \\g<name>).",
                    )
                after = before[:match.start()] + new_text + before[match.end():]
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

    # Prettify the final staged HTML once per file, after all regex edits.
    files = []
    for resolved_key, new_text in staged_contents.items():
        path = Path(resolved_key)
        if path.suffix.lower() not in {".html", ".htm"} or new_text == original_contents[resolved_key]:
            continue
        relative_path = path.relative_to(inside(".")).as_posix()
        formatted, status = prettify_html(new_text)
        if status["ok"]:
            new_text = formatted
            staged_contents[resolved_key] = formatted
        original = original_contents[resolved_key]
        files.append({
            "path": relative_path,
            "diff": "".join(difflib.unified_diff(
                original.splitlines(True), new_text.splitlines(True),
                fromfile=relative_path, tofile=relative_path,
            )),
            "formatter": status,
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

    response = {"edits": results, "files": files, "dry_run": dry_run}
    if not dry_run and any(
        Path(path).suffix.lower() == ".py"
        and new_text != original_contents[path]
        for path, new_text in staged_contents.items()
    ):
        response["dependency_hint"] = (
            "Review requirements.txt and add any new third-party dependencies introduced by these Python edits."
        )
    return response


def register(mcp):
    return mcp.tool(description=describe(
        "Apply regex edits to workspace files; replace_all defaults to false; dry_run=true validates without writing. "
        "Validates and stages the entire batch before writing; a later invalid edit leaves earlier edits unapplied. "
        "Edits are processed in order against staged content, so repeated/overlapping edits on the same file are order-sensitive. "
        "Each edit requires pattern and replacement; legacy old_str/new_str aliases are rejected by schema validation. "
        "pattern is a Python regular expression (re module); replacement text is literal unless expand_groups=true (Python re template: \\1, \\g<name>). "
        "replace_all=true replaces all matches, or at most max_replacements when provided. case_insensitive=true ignores case. position is an object with 1-based line and col keys and limits search to the first match starting at or after that coordinate. "
        "Error messages include the zero-based edit index ([edit N]) for easy identification. "
        "Edit failures return error_code (invalid_regex, invalid_replacement, no_match, not_found, path_escape, not_a_file, binary_file, io_error, invalid_edit) "
        "with edit_index and message instead of raising; nothing is written. "
        "Each edit result includes matches (count of replacements made) and its edit diff. Changed HTML files are prettified once after staging and are summarized in files with one original-to-final diff and formatter status per file; dry runs preview this without writing. Successful edits to Python files include a reminder to update requirements.txt if they introduce third-party dependencies. "
        "Returns unified diffs. When to use: Make localized changes."
    ))(apply_edits)
