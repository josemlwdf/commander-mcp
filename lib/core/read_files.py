from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from .. import support
from ..support import inside, MAX_READ_BYTES, describe

class LineRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(description="Workspace-relative file path; must match a value in paths.")
    start: int = Field(ge=1, description="First line to include, using 1-based numbering.")
    end: int = Field(ge=1, description="Last line to include, using 1-based numbering; must be greater than or equal to start.")


def read_files(
    paths: Annotated[
        list[str],
        Field(min_length=1, description="Workspace-relative file paths to read."),
    ],
    line_ranges: Annotated[
        list[LineRange] | None,
        Field(description="Optional 1-based line ranges for selected files. Each range is an object with keys: path (must match a path in paths), start (first line) and end (last line, >= start); files without a range are read in full."),
    ] = None,
    start_line: Annotated[int | None, Field(ge=1, description="Shorthand first line for a single file; requires end_line.")] = None,
    end_line: Annotated[int | None, Field(ge=1, description="Shorthand last line for a single file; requires start_line.")] = None,
    encoding: Annotated[str, Field(description="Text encoding used to decode files; defaults to UTF-8.")] = "utf-8",
    include_metadata: Annotated[bool, Field(description="Include file size in bytes and modification time metadata.")] = False,
) -> dict:
    if not paths:
        raise ValueError("paths must contain at least one workspace path")
    if (start_line is None) != (end_line is None):
        raise ValueError("start_line and end_line must be supplied together")
    if start_line is not None and len(paths) != 1:
        raise ValueError("start_line/end_line shorthand requires exactly one path")
    ranges = {r.path: r for r in (line_ranges or [])}
    if start_line is not None:
        ranges[paths[0]] = LineRange(path=paths[0], start=start_line, end=end_line)
    output = []
    file_errors: list[dict] = []
    for name in paths:
        try:
            p = inside(name, must_exist=True)
        except FileNotFoundError:
            file_errors.append({
                "path": name,
                "error_code": "not_found",
                "message": f"File not found: {name}",
            })
            continue
        except ValueError:
            file_errors.append({
                "path": name,
                "error_code": "path_escape",
                "message": f"Path escapes authorized workspace root: {name}",
            })
            continue
        except OSError as exc:
            # Use strerror only: str(exc) embeds the absolute host path.
            file_errors.append({
                "path": name,
                "error_code": "io_error",
                "message": f"{exc.strerror or 'File could not be read'}: {name}",
            })
            continue
        if not p.is_file():
            file_errors.append({"path": name, "error_code": "not_a_file", "message": f"Not a file: {name}"})
            continue
        if p.stat().st_size > MAX_READ_BYTES:
            file_errors.append({"path": name, "error_code": "file_too_large", "message": f"File exceeds 2 MiB read limit: {name}"})
            continue
        try:
            full_content = p.read_text(encoding=encoding)
        except UnicodeDecodeError:
            file_errors.append({
                "path": name,
                "error_code": "binary_file",
                "message": f"File is not valid UTF-8 (binary or non-text): {name}",
            })
            continue
        all_lines = full_content.splitlines()
        total_lines = len(all_lines)
        entry = {
            "path": str(p.relative_to(support.ROOT)),
            "total_lines": total_lines,
        }
        if include_metadata:
            stat = p.stat()
            entry["metadata"] = {"size_bytes": stat.st_size, "modified_time": stat.st_mtime}
        if name in ranges:
            r = ranges[name]
            start = int(r.start)
            end = int(r.end)
            if start < 1 or end < start:
                raise ValueError("line ranges require 1 <= start <= end")
            content = "\n".join(all_lines[start - 1:end])
            entry["content"] = content
            entry["applied_start"] = start
            entry["applied_end"] = min(end, total_lines)
        else:
            entry["content"] = full_content
        output.append(entry)
    return {"files": output, "errors": file_errors}


def register(mcp):
    return mcp.tool(description=describe(
        "Read UTF-8 workspace files (max 2 MiB each), optionally by 1-based line range. "
        "line_ranges is a list of {path, start, end} objects; files without a range are read in full. "
        "Missing, unreadable, or binary (non-UTF-8) files are returned in errors (with error_code) "
        "alongside successful reads in files. "
        "Response includes total_lines and, when a range is applied, the applied start and end. "
        "When to use: Inspect known files."
    ))(read_files)
