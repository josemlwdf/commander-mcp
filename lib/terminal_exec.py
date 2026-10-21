from .support import run_process, inside, describe

def register(mcp):
    @mcp.tool(description=describe("Run a shell command in the workspace or a confined subdirectory. Output is limited to 1 MiB per stream; timeout defaults to 30 seconds."))
    def terminal_exec(command: str, cwd: str | None = None, timeout_ms: int = 30000) -> dict:
        work=inside(cwd or ".",must_exist=True)
        if not command.strip(): raise ValueError("command cannot be empty")
        return run_process(command,cwd=work,timeout_ms=timeout_ms,shell=True)
