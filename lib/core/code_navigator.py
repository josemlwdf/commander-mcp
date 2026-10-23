"""MCP endpoints for refreshing and querying the local C# index."""
from typing import Annotated

from pydantic import Field

from .. import code_index
from ..support import describe


def index_codebase(exclude_dirs: list[str] | None = None) -> dict:
    """Incrementally scan C# files into the per-workspace index."""
    try:
        return code_index.index_codebase(exclusions=exclude_dirs)
    except code_index.IndexErrorBase as exc:
        return {"error_code": "index_unavailable", "message": str(exc)}


def code_navigator(
    action: Annotated[str, Field(description="search_code, get_symbol, or find_references")],
    query: Annotated[str | None, Field(description="Natural language or identifier query for search_code.")] = None,
    symbol: Annotated[str | None, Field(description="Symbol name for get_symbol or find_references.")] = None,
    file_path: Annotated[str | None, Field(description="Optional workspace-relative path to disambiguate get_symbol.")] = None,
    limit: Annotated[int, Field(ge=1, le=50, description="Maximum results (1 to 50).")] = 10,
) -> dict:
    """Search local C# symbols, retrieve current source, or find likely usages."""
    try:
        return code_index.code_navigator(action, query, symbol, file_path, limit)
    except code_index.IndexErrorBase as exc:
        return {"error_code": "index_unavailable", "message": str(exc)}
    except (ValueError, TypeError) as exc:
        return {"error_code": "invalid_input", "message": str(exc)}


def register(mcp):
    mcp.tool(description=describe(
        "Incrementally index local C# source files in .handy-coder/code_index.sqlite3. "
        "Optional exclude_dirs adds directory names to the default exclusions. "
        "Reports scanned, parsed, skipped, removed, and failed files."
    ))(index_codebase)
    mcp.tool(description=describe(
        "Navigate the local C# symbol index. action=search_code searches identifiers and lexical source text; "
        "get_symbol returns current source; find_references reports likely syntactic usages, not compiler-resolved calls. "
        "The index is built locally with Tree-sitter and SQLite FTS5."
    ))(code_navigator)
