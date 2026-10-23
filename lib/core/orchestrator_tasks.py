from __future__ import annotations

import os
import re
import tempfile
from typing import Annotated, Literal

from pydantic import Field

from .. import support
from ..support import describe, inside


Status = Literal["non_started", "interrupted", "complete", "failed"]

_STATUS_LINE_RE = re.compile(
    r"^(?P<prefix>\s*-\s*\[\s*[ xX]?\s*\]\s*\[status:\s*)(?P<status>non_started|interrupted|complete|failed)(?P<suffix>\s*\].*)$"
)


def _error(code: str, message: str, **extra) -> dict:
    return {"error_code": code, "message": message, **extra}


def _atomic_write(path, content: str) -> None:
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _select_line(lines: list[str], line_number: int | None, task_contains: str | None) -> tuple[int | None, dict | None]:
    if (line_number is None) == (task_contains is None):
        return None, _error(
            "invalid_selector",
            "Provide exactly one selector: either line_number or task_contains.",
        )

    if line_number is not None:
        if line_number < 1 or line_number > len(lines):
            return None, _error(
                "line_out_of_range",
                "line_number must refer to an existing 1-based line in tasks.md.",
                line_number=line_number,
                total_lines=len(lines),
            )
        return line_number - 1, None

    needle = (task_contains or "").strip()
    if not needle:
        return None, _error("invalid_selector", "task_contains cannot be empty.")

    matches = [index for index, line in enumerate(lines) if needle in line]
    if not matches:
        return None, _error("task_not_found", "No task line matched task_contains.", task_contains=needle)
    if len(matches) > 1:
        return None, _error(
            "ambiguous_task_selector",
            "task_contains matched multiple lines. Use line_number or a more specific text.",
            task_contains=needle,
            match_lines=[m + 1 for m in matches],
        )
    return matches[0], None


def orchestrator_update_task_status(
    status: Annotated[Status, Field(description="New task status. Allowed values: non_started, interrupted, complete, failed.")],
    line_number: Annotated[int | None, Field(ge=1, description="Optional 1-based tasks.md line number to update. Provide exactly one selector: line_number or task_contains.")] = None,
    task_contains: Annotated[str | None, Field(description="Optional text selector matched against tasks.md lines. Provide exactly one selector: task_contains or line_number.")] = None,
    tasks_path: Annotated[str, Field(description="Workspace-relative path to the task tracker file. Defaults to .handy-coder/tasks.md.")] = ".handy-coder/tasks.md",
) -> dict:
    try:
        path = inside(tasks_path, must_exist=True)
    except FileNotFoundError:
        return _error("not_found", f"Task tracker file not found: {tasks_path}")
    except ValueError:
        return _error("path_escape", f"Path escapes authorized workspace root: {tasks_path}")

    if not path.is_file():
        return _error("not_a_file", f"Task tracker path is not a file: {tasks_path}")

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _error("binary_file", f"Task tracker file is not valid UTF-8: {tasks_path}")
    except OSError as exc:
        return _error("io_error", f"{exc.strerror or 'Read failed'}: {tasks_path}")

    lines = text.splitlines()
    target_index, selection_error = _select_line(lines, line_number, task_contains)
    if selection_error is not None:
        return selection_error

    target_line = lines[target_index]
    match = _STATUS_LINE_RE.match(target_line)
    if not match:
        return _error(
            "invalid_task_format",
            "Selected line does not match expected task format '- [ ] [status: ...] <task>'.",
            line_number=target_index + 1,
            line=target_line,
        )

    previous_status = match.group("status")
    if previous_status == status:
        return {
            "updated": False,
            "line_number": target_index + 1,
            "status": status,
            "message": "Selected task already has the requested status.",
        }

    lines[target_index] = f"{match.group('prefix')}{status}{match.group('suffix')}"
    new_text = "\n".join(lines)
    if text.endswith("\n"):
        new_text += "\n"

    try:
        _atomic_write(path, new_text)
    except OSError as exc:
        return _error("io_error", f"{exc.strerror or 'Write failed'}: {tasks_path}")

    return {
        "updated": True,
        "tasks_file": str(path.relative_to(support.ROOT)).replace("\\", "/"),
        "line_number": target_index + 1,
        "previous_status": previous_status,
        "status": status,
        "line": lines[target_index],
    }


def register(mcp):
    return mcp.tool(description=describe(
        "Update one task status in `.handy-coder/tasks.md` (or another tasks file) with an atomic write. "
        "Select the target by 1-based line_number or task_contains text, set status to non_started/interrupted/complete/failed, "
        "and keep the `[ ] [status: ...]` task format intact. "
        "When to use: Track orchestrator task progress safely between subagent runs."
    ))(orchestrator_update_task_status)
