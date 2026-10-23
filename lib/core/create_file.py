from typing import Annotated
import os

from pydantic import Field

from .. import support
from ..support import inside, describe, MAX_READ_BYTES


def _error(code: str, path: str, message: str) -> dict:
    return {"error_code": code, "path": path, "message": message, "created": False}


def create_file(
    path: Annotated[
        str,
        Field(min_length=1, description="Workspace-relative path of the file to create."),
    ],
    content: Annotated[
        str,
        Field(description="Full UTF-8 text content to write to the new file; may be empty."),
    ] = "",
    overwrite: Annotated[
        bool,
        Field(description="If true, replace the file when it already exists; otherwise an existing file returns error_code='already_exists'. Defaults to false."),
    ] = False,
    create_parents: Annotated[
        bool,
        Field(description="If true (default), create any missing parent directories inside the workspace."),
    ] = True,
    permissions: Annotated[int | str | None, Field(description="Optional POSIX mode as a decimal integer (0 to 511, e.g. 420) or octal string ('0755' or '0o755'). Ignored on Windows.")] = None,
    backup: Annotated[bool, Field(description="When overwriting, save the prior bytes to a sibling .bak file before writing.")] = False,
    format_content: Annotated[bool, Field(description="When true, run an available formatter on supported Python or HTML files after writing. Defaults to false to preserve submitted content.")] = False,
    post_process_python: Annotated[bool, Field(description="When true, run Python source post-processing after writing a .py file. Defaults to false.")] = False,
) -> dict:
    if isinstance(permissions, str):
        try:
            if not (permissions.lower().startswith("0o") or (permissions and all(c in "01234567" for c in permissions))):
                raise ValueError
            permissions = int(permissions, 8)
            if permissions > 0o777:
                raise ValueError
        except ValueError:
            return _error("invalid_permissions", path, "permissions must be a decimal integer from 0 to 511 or an octal string such as '0755' or '0o755'")
    elif permissions is not None and (permissions < 0 or permissions > 511):
        return _error("invalid_permissions", path, "decimal permissions must be between 0 and 511")
    try:
        p = inside(path)
    except ValueError:
        return _error("path_escape", path, f"Path escapes authorized workspace root: {path}")
    except OSError as exc:
        # strerror only: str(exc) embeds the absolute host path.
        return _error("io_error", path, f"{exc.strerror or 'Path could not be resolved'}: {path}")

    if p == support.ROOT:
        return _error("not_a_file", path, f"Path is the workspace root, not a file: {path}")

    encoded = content.encode("utf-8")
    if len(encoded) > MAX_READ_BYTES:
        return _error("file_too_large", path, f"Content exceeds 2 MiB limit: {path}")

    if p.is_dir():
        return _error("not_a_file", path, f"Path is a directory: {path}")
    existed = p.exists()
    if existed and not overwrite:
        return _error("already_exists", path, f"File already exists (pass overwrite=true to replace it): {path}")

    try:
        if existed and backup:
            backup_path = inside(str(p) + ".bak")
            backup_path.write_bytes(p.read_bytes())
        if not p.parent.exists():
            if not create_parents:
                return _error("parent_missing", path, f"Parent directory does not exist: {path}")
            p.parent.mkdir(parents=True, exist_ok=True)
        elif not p.parent.is_dir():
            return _error("not_a_directory", path, f"Parent path is not a directory: {path}")
        # Re-validate after mkdir in case a symlinked parent resolves elsewhere.
        p = inside(p)
        # "x" fails atomically if the file appeared since the check above.
        # Binary mode keeps newlines exactly as supplied on every platform.
        with open(p, "wb" if overwrite else "xb") as handle:
            handle.write(encoded)
        if permissions is not None and hasattr(p, "chmod") and os.name != "nt":
            p.chmod(permissions)
    except ValueError:
        return _error("path_escape", path, f"Path escapes authorized workspace root: {path}")
    except FileExistsError:
        return _error("already_exists", path, f"File already exists (pass overwrite=true to replace it): {path}")
    except (NotADirectoryError, IsADirectoryError):
        return _error("not_a_directory", path, f"A path component is not a directory: {path}")
    except OSError as exc:
        return _error("io_error", path, f"{exc.strerror or 'File could not be written'}: {path}")

    result = {
        "path": str(p.relative_to(support.ROOT)),
        "created": not existed,
        "overwritten": existed,
        "bytes": len(encoded),
        "lines": len(content.splitlines()),
    }
    if p.suffix.lower() == ".py" and (post_process_python or format_content):
        from ..utils.python_postprocess import process_python_file
        from ..extras.code_format_lint import code_format_lint

        processing = {"ok": True, "changes": [], "uncertain": []}
        formatting = {"ok": True, "changed": False}
        if post_process_python:
            try:
                processing = process_python_file(p)
            except Exception as exc:
                processing = {"path": str(p.relative_to(support.ROOT)), "ok": False,
                              "error": str(exc), "changes": [], "uncertain": []}
        if format_content:
            try:
                formatting = code_format_lint([str(p.relative_to(support.ROOT))], fix=True, language="python")
            except Exception as exc:
                formatting = {"supported": False, "ok": False, "error": str(exc), "reports": [],
                              "tools_run": [], "tools_skipped": []}
        if post_process_python: result["python_processing"] = processing
        if format_content: result["formatter"] = formatting
        result["post_processing_ok"] = bool(processing.get("ok")) and bool(formatting.get("ok"))
    if p.suffix.lower() == ".py":
        result["dependency_hint"] = "Review requirements.txt and add any new third-party dependencies introduced by this Python file."
    if p.suffix.lower() in {".html", ".htm"}:
        from . import html_syntax_check
        if format_content:
            from ..utils.html_prettify import prettify_html
            final_text = p.read_bytes().decode("utf-8")
            formatted_text, formatting = prettify_html(final_text)
            if formatting["ok"] and formatted_text != final_text:
                try:
                    p.write_text(formatted_text, encoding="utf-8")
                except OSError as exc:
                    formatting = {"ok": False, "changed": False,
                                  "error": f"Could not save formatted content: {exc.strerror or 'write failed'}"}
            result["formatter"] = formatting
        result["syntax_check"] = html_syntax_check.check_html(p.read_bytes().decode("utf-8"))
        result["guidance"] = "For web projects, consider organizing HTML, JavaScript, and CSS under static/html, static/js, and static/css. These directories are not created automatically."
    final_content = p.read_bytes()
    result["bytes"] = len(final_content)
    result["lines"] = len(final_content.decode("utf-8").splitlines())
    return result


def register(mcp):
    return mcp.tool(description=describe(
        "Create a new UTF-8 text file in the workspace (max 2 MiB), optionally with initial content. "
        "Fails with error_code='already_exists' instead of replacing an existing file unless overwrite=true. "
        "Missing parent directories are created unless create_parents=false. "
        "Optional permissions sets POSIX mode bits; backup=true saves the prior file as a sibling .bak before overwrite. "
        "Failures return error_code (already_exists, path_escape, not_a_file, not_a_directory, parent_missing, file_too_large, io_error) "
        "with a message instead of raising; nothing is written. "
        "Response includes the workspace-relative path, created, overwritten, bytes and lines; counts describe final on-disk content. Submitted content is preserved by default; format_content=true opts into formatting, and post_process_python=true opts into Python source processing. Formatting and processing failures are reported and the file is kept. Python file responses remind the agent to update requirements.txt if the file introduces third-party dependencies. "
        "HTML files receive a syntax check of their final saved content and suggest organizing web content under static/html, static/js, and static/css; these directories are not created automatically. "
        "Successful creates and overwrites can trigger the configured commit nudge, which may start a commit subagent task. "
        "When to use: Add a new file; use apply_edits to modify existing files."
    ))(create_file)
