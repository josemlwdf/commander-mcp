import re
from typing import Annotated

from pydantic import Field
from ..support import describe, iter_workspace_files

def register(mcp):
    @mcp.tool(description=describe(
        "Search workspace text with a regex or literal query; path_glob limits the search and scan failures appear in errors. "
        "Returns matched lines and optional context lines. "
        "When to use: Find matching code or text."
    ))
    def code_search_ripgrep(
        query: Annotated[str, Field(description="Text to search for. Interpreted as a regular expression when is_regex is true, otherwise as a literal string.")],
        is_regex: Annotated[bool, Field(description="Whether query is a regular expression; defaults to true.")] = True,
        path_glob: Annotated[str | None, Field(description="Optional glob pattern restricting searched workspace-relative file paths.")] = None,
        max_results: Annotated[int, Field(description="Maximum number of matching lines to return, from 1 to 10000.")] = 100,
        context_lines: Annotated[int, Field(description="Number of surrounding context lines to include before and after each match, from 0 to 10.")] = 0,
    ) -> dict:
        if max_results < 1 or max_results > 10000:
            raise ValueError("max_results must be 1..10000")
        if context_lines < 0 or context_lines > 10:
            raise ValueError("context_lines must be 0..10")
        try:
            rx = re.compile(query if is_regex else re.escape(query))
        except re.error as exc:
            return {
                "error_code": "invalid_regex",
                "message": f"Invalid regular expression: {exc}",
                "matches": [],
                "truncated": False,
                "errors": [],
            }
        hits = []
        errors = []
        for path, relative_path in iter_workspace_files(path_glob=path_glob, errors=errors):
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    continue
                file_lines = path.read_text(encoding="utf-8").splitlines()
                for line_number, line in enumerate(file_lines, 1):
                    if rx.search(line):
                        match_entry = {"path": relative_path, "line": line_number, "text": line}
                        if context_lines > 0:
                            before_start = max(0, line_number - 1 - context_lines)
                            after_end = min(len(file_lines), line_number + context_lines)
                            match_entry["context_before"] = file_lines[before_start:line_number - 1]
                            match_entry["context_after"] = file_lines[line_number:after_end]
                        hits.append(match_entry)
                        if len(hits) >= max_results:
                            return {"matches": hits, "truncated": True, "errors": errors}
            except UnicodeDecodeError:
                continue
            except OSError as exc:
                errors.append({
                    "path": relative_path,
                    "message": exc.strerror or "File could not be read",
                })
        return {"matches": hits, "truncated": False, "errors": errors}
