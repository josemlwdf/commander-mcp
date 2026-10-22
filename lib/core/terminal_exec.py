from typing import Annotated

from pydantic import Field
from ..support import run_process, inside, describe


def terminal_exec(
    command: Annotated[str, Field(description="Shell command to execute. Must not be empty; runs with the configured shell.")],
    cwd: Annotated[str | None, Field(description="Optional workspace-relative working directory.")] = None,
    timeout_ms: Annotated[int, Field(ge=1, le=600000, description="Command timeout in milliseconds, from 1 through 600000 (10 minutes); defaults to 30000 (30 s). Note: MCP clients typically enforce their own shorter timeouts (often 30–120 s) that may cut off a command before this tool's maximum is reached.")] = 30000,
) -> dict:
    work = inside(cwd or ".", must_exist=True)
    if not command.strip():
        raise ValueError("command cannot be empty")
    return run_process(command, cwd=work, timeout_ms=timeout_ms, shell=True)


def register(mcp):
    return mcp.tool(description=describe(
        "Run a shell command in the workspace or a confined subdirectory. "
        "Output is limited to 1 MiB per stream; timeout defaults to 30 seconds (up to 600s). "
        "Uses heuristic lexical checks to prevent out-of-workspace escapes, but this is not a complete sandbox for untrusted input.",
        include_working_directory=True,
    ))(terminal_exec)
