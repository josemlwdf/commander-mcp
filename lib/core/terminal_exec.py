from typing import Annotated

from pydantic import Field
from ..support import run_process, inside, describe


def terminal_exec(
    command: Annotated[str, Field(description="Shell command to execute. Must not be empty; runs with the configured shell.")],
    cwd: Annotated[str | None, Field(description="Optional workspace-relative working directory.")] = None,
    timeout_ms: Annotated[int, Field(ge=1, le=600000, description="Command timeout in milliseconds, from 1 through 600000 (10 minutes); defaults to 30000 (30 s). Note: MCP clients typically enforce their own shorter timeouts that may cut off a command.")] = 30000,
    env: Annotated[dict[str, str] | None, Field(description='Optional environment variables added to the inherited process environment as an object of string values, for example {"MODE": "test"}.')] = None,
    stdin: Annotated[str | None, Field(description="Optional text sent to standard input, then closed.")] = None,
    shell: Annotated[str | None, Field(description="Optional shell executable override for this call; defaults to the configured shell.")] = None,
) -> dict:
    if not command.strip():
        raise ValueError("command cannot be empty")
    work = inside(cwd or ".", must_exist=True)
    return run_process(command, cwd=work, timeout_ms=timeout_ms, shell=True, env=env, stdin=stdin, shell_executable=shell)


def register(mcp):
    return mcp.tool(description=describe(
        "Run a shell command in the workspace or a confined subdirectory. "
        "Output is limited to 1 MiB per stream; timeout defaults to 30 seconds (up to 600s). "
        'Accepts optional env as an object of string values (for example {"MODE": "test"}) and stdin; response exit_code is the process exit status (124 on timeout). Uses heuristic lexical checks to prevent recognizable path escapes, but this is not a complete sandbox; checks can be bypassed and are not OS isolation. Shell selection defaults to configured shell.',
        include_working_directory=True,
    ))(terminal_exec)
