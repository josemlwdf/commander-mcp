import json, tomllib
from pathlib import Path
from .support import inside, run_process, describe

def register(mcp):
    @mcp.tool(description=describe("Detect and run an available project diagnostics check from package.json, pyproject.toml, or common Makefile targets."))
    def code_get_diagnostics(path: str | None = None) -> dict:
        base=inside(path or ".",must_exist=True); candidates=[]
        pkg=base/"package.json"
        if pkg.is_file():
            data=json.loads(pkg.read_text(encoding="utf-8")); scripts=data.get("scripts",{})
            for key in ("typecheck","check","lint"): 
                if key in scripts: candidates.append((key,["npm","run",key]))
        py=base/"pyproject.toml"
        if py.is_file():
            data=tomllib.loads(py.read_text(encoding="utf-8")); cfg=data.get("tool",{})
            if "ruff" in cfg: candidates.append(("ruff",["ruff","check","."]))
            if "mypy" in cfg: candidates.append(("mypy",["mypy","."]))
        mk=base/"Makefile"
        if mk.is_file():
            targets=set()
            for line in mk.read_text(encoding="utf-8").splitlines():
                if line and not line[0].isspace() and ":" in line and not line.startswith("#"): targets.add(line.split(":",1)[0])
            for t in ("check","lint","typecheck","validate"): 
                if t in targets: candidates.append((t,["make",t]))
        if not candidates: return {"supported":False,"message":f"No supported diagnostics check found in {base} (looked for package scripts, Ruff/mypy configuration, and Makefile check/lint/typecheck/validate targets)."}
        name,cmd=candidates[0]; res=run_process(cmd,cwd=base)
        return {"supported":True,"check":name,**res}
