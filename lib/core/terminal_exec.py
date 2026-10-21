from typing import Annotated

from pydantic import Field
from ..support import run_process, inside, describe

def register(mcp):
    @mcp.tool(description=describe("Run a shell command in the workspace or a confined subdirectory. Output is limited to 1 MiB per stream; timeout defaults to 30 seconds.", include_working_directory=True))
    def terminal_exec(
        command: Annotated[str, Field(description="Shell command to execute. Must not be empty; runs with the configured shell.")],
        cwd: Annotated[str | None, Field(description="Optional workspace-relative working directory.")]=None,
        timeout_ms: Annotated[int, Field(description="Command timeout in milliseconds, from 1 through 600000; defaults to 30000.")]=30000,
    ) -> dict:
        work=inside(cwd or ".",must_exist=True)
        if not command.strip(): raise ValueError("command cannot be empty")
        return run_process(command,cwd=work,timeout_ms=timeout_ms,shell=True)
