import json
from typing import Literal
from pathlib import Path
from .support import inside, run_process, describe

def register(mcp):
    @mcp.tool(description=describe("Run Jest, Vitest, or pytest when detected from project manifests. Optional filter_path is confined to the workspace."))
    def workspace_run_tests(test_framework: Literal["jest","pytest","vitest"] | None = None, filter_path: str | None = None) -> dict:
        root=inside("."); framework=test_framework
        if framework and framework not in ("jest","pytest","vitest"): raise ValueError("test_framework must be jest, pytest, or vitest")
        if not framework:
            pkg=root/"package.json"
            if pkg.exists():
                d=json.loads(pkg.read_text(encoding="utf-8")); alltext=json.dumps(d)
                framework="vitest" if "vitest" in alltext else "jest" if "jest" in alltext else None
            if not framework and ((root/"pytest.ini").exists() or (root/"pyproject.toml").exists() or any(root.glob("test_*.py"))): framework="pytest"
        if not framework: return {"supported":False,"message":"No Jest, Vitest, or pytest setup detected."}
        target=inside(filter_path) if filter_path else None
        if framework=="pytest": cmd=["pytest"]+([str(target)] if target else [])
        else: cmd=["npx","--no-install",framework,"run"]+([str(target)] if target else [])
        return {"supported":True,"framework":framework,**run_process(cmd,cwd=root)}
