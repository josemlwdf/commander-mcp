import re
from pathlib import Path
from .support import inside, describe

def register(mcp):
    @mcp.tool(description=describe("Search workspace text files using Python regular expressions (or literal text when is_regex is false)."))
    def code_search_ripgrep(query: str, is_regex: bool = True, path_glob: str | None = None, max_results: int = 100) -> dict:
        if max_results < 1 or max_results > 10000: raise ValueError("max_results must be 1..10000")
        rx=re.compile(query if is_regex else re.escape(query)); hits=[]
        for p in inside(".").rglob("*"):
            if not p.is_file() or ".git" in p.parts: continue
            rel=str(p.relative_to(__import__('lib.support',fromlist=['ROOT']).ROOT))
            if path_glob and not __import__('fnmatch').fnmatch(rel,path_glob): continue
            try:
                if p.stat().st_size > 2*1024*1024: continue
                for n,line in enumerate(p.read_text(encoding="utf-8").splitlines(),1):
                    if rx.search(line):
                        hits.append({"path":rel,"line":n,"text":line})
                        if len(hits)>=max_results: return {"matches":hits,"truncated":True}
            except (UnicodeDecodeError,OSError): continue
        return {"matches":hits,"truncated":False}
