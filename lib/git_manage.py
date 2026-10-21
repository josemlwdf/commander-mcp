from typing import Literal
from .support import inside, run_process, describe

def register(mcp):
    @mcp.tool(description=describe("Run a validated Git action in the workspace: status, diff, log, commit, or checkout. args are action-specific positional arguments."))
    def git_manage(action: Literal["status","diff","log","commit","checkout"], args: list[str] | None = None) -> dict:
        args=args or []
        if action not in ("status","diff","log","commit","checkout"): raise ValueError("Unsupported Git action")
        if action=="status" and args: raise ValueError("status accepts no args")
        if action=="diff" and any(a.startswith("-") and a not in ("--cached","--stat","--name-only","--") for a in args): raise ValueError("Unsupported diff option")
        if action=="log" and any(a.startswith("-") and a not in ("-n","--oneline","--decorate","--all","--") and not a.isdigit() for a in args): raise ValueError("Unsupported log option")
        if action=="commit":
            if not args or "-m" not in args or args.count("-m")!=1 or args.index("-m")==len(args)-1: raise ValueError("commit requires -m followed by a commit message")
            mi=args.index("-m")
            if any(a.startswith("-") for a in args[:mi] if a!="-a") or any(a.startswith("-") for a in args[mi+2:]): raise ValueError("Only -a and -m commit options are accepted")
        if action=="checkout" and (len(args)!=1 or args[0].startswith("-")): raise ValueError("checkout requires exactly one branch or ref")
        for arg in args:
            if arg.startswith(("../","/","~/")) or "/../" in arg or arg=="..":
                try: inside(arg)
                except ValueError: raise ValueError(f"Git argument escapes workspace: {arg}")
        return run_process(["git",action,*args],cwd=inside("."))
