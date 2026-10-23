"""Local, per-workspace C# symbol index backed by SQLite FTS5."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from . import support

SCHEMA_VERSION = 1
DEFAULT_EXCLUDES = frozenset({".git", ".handy-coder", "Library", "Temp", "obj", "bin", "Build", "Builds", "Logs", "UserSettings", *support.GENERATED_DIRS})


class IndexErrorBase(RuntimeError):
    pass


def _parser():
    try:
        import tree_sitter_c_sharp
        from tree_sitter import Language, Parser
    except ImportError as exc:
        raise IndexErrorBase("Tree-sitter C# parser unavailable; install project dependencies") from exc
    language = Language(tree_sitter_c_sharp.language())
    try:
        return Parser(language)
    except TypeError:  # tree-sitter 0.23 API
        parser = Parser()
        parser.language = language
        return parser


def _db():
    root = Path(support.ROOT).resolve()
    path = root / ".handy-coder" / "code_index.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_check USING fts5(value)")
        con.execute("DROP TABLE _fts_check")
    except sqlite3.OperationalError as exc:
        con.close()
        raise IndexErrorBase(f"SQLite FTS5 is unavailable: {exc}") from exc
    con.executescript("""
      CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL,
        sha256 TEXT NOT NULL, size INTEGER NOT NULL, mtime REAL NOT NULL,
        indexed_at REAL NOT NULL, error TEXT);
      CREATE TABLE IF NOT EXISTS symbols(id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        name TEXT NOT NULL, qualified_name TEXT NOT NULL, kind TEXT NOT NULL, namespace TEXT,
        containing_type TEXT, signature TEXT, docs TEXT, start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL, source TEXT NOT NULL, searchable TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS symbols_file ON symbols(file_id);
      CREATE INDEX IF NOT EXISTS symbols_qualified ON symbols(qualified_name);
      CREATE TABLE IF NOT EXISTS references_(id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
        source_symbol_id INTEGER, target TEXT NOT NULL, line INTEGER NOT NULL, kind TEXT NOT NULL, snippet TEXT NOT NULL,
        enclosing_type TEXT, enclosing_method TEXT);
      CREATE INDEX IF NOT EXISTS references_target ON references_(target);
      CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5(name, qualified_name, signature, docs, searchable, path,
        tokenize='unicode61');
    """)
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("INSERT OR REPLACE INTO metadata VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    con.execute("INSERT OR REPLACE INTO metadata VALUES('project_root', ?)", (str(root),))
    con.commit()
    return con


def _tokens(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", text)
    return " ".join(re.findall(r"[A-Za-z0-9]+", text)).lower()


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _children(node):
    return list(node.children)


def _extract(source: bytes):
    parser = _parser()
    tree = parser.parse(source)
    root = tree.root_node
    symbols, refs = [], []
    types = {"class_declaration": "class", "struct_declaration": "struct", "interface_declaration": "interface", "record_declaration": "record"}
    kinds = {**types, "method_declaration": "method", "constructor_declaration": "constructor", "property_declaration": "property", "field_declaration": "field"}
    def walk(node, namespace="", owners=(), active=None):
        active = active or []
        kind = kinds.get(node.type)
        name_node = node.child_by_field_name("name") if kind else None
        name = _node_text(name_node, source) if name_node else ""
        if node.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
            n = node.child_by_field_name("name")
            if n: namespace = _node_text(n, source)
        new_owners = owners + ((name,) if kind in types.values() else ())
        if kind and name:
            line1, line2 = node.start_point.row + 1, node.end_point.row + (0 if node.end_point.column == 0 else 1)
            txt = _node_text(node, source)
            signature = txt.split("{", 1)[0].strip() if kind != "field" else txt.rstrip(";").strip()
            qname = ".".join(x for x in (namespace, *owners, name) if x)
            docs = ""
            siblings = node.parent.children if node.parent else []
            try:
                i = siblings.index(node)
                if i and siblings[i-1].type == "comment":
                    prev = _node_text(siblings[i-1], source)
                    if prev.startswith("///"): docs = prev
            except (ValueError, AttributeError): pass
            symbols.append({"name": name, "qualified_name": qname, "kind": kind, "namespace": namespace,
                "containing_type": ".".join(owners) or None, "signature": signature, "docs": docs,
                "start_line": line1, "end_line": line2, "source": txt,
                "searchable": _tokens(txt)})
            active = active + [(kind, name)]
        if node.type == "invocation_expression":
            target = node.child_by_field_name("function")
            if target:
                t = _node_text(target, source)
                refs.append({"target": t.split(".")[-1], "kind": "method_call", "line": node.start_point.row + 1,
                    "snippet": _node_text(node, source).splitlines()[0][:240], "enclosing_type": ".".join(owners) or None,
                    "enclosing_method": next((n for k,n in reversed(active) if k in ("method", "constructor")), None)})
        elif node.type == "member_access_expression":
            name_child = node.child_by_field_name("name")
            if name_child:
                refs.append({"target": _node_text(name_child, source), "kind": "member_access", "line": node.start_point.row + 1,
                    "snippet": _node_text(node, source)[:240], "enclosing_type": ".".join(owners) or None,
                    "enclosing_method": next((n for k,n in reversed(active) if k in ("method", "constructor")), None)})
        elif node.type == "identifier" and node.parent and node.parent.type not in {
            "member_access_expression", "invocation_expression", "variable_declarator",
            "parameter", "method_declaration", "constructor_declaration",
            "class_declaration", "struct_declaration", "interface_declaration",
            "record_declaration", "property_declaration", "qualified_name",
        }:
            refs.append({"target": _node_text(node, source), "kind": "identifier_usage",
                "line": node.start_point.row + 1, "snippet": _node_text(node.parent, source).splitlines()[0][:240],
                "enclosing_type": ".".join(owners) or None,
                "enclosing_method": next((n for k,n in reversed(active) if k in ("method", "constructor")), None)})
        for child in node.children:
            walk(child, namespace, new_owners, active)
    walk(root)
    if root.has_error:
        raise IndexErrorBase("C# syntax tree contains parse errors; file was not replaced in the index")
    return symbols, refs


def _files(root: Path, exclusions=None):
    excluded = DEFAULT_EXCLUDES | set(exclusions or ())
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not (Path(base) / d).is_symlink())
        for name in sorted(names):
            if name.endswith(".cs"):
                path = Path(base) / name
                try:
                    path.resolve(strict=True).relative_to(root)
                except (OSError, ValueError):
                    continue
                yield path


def index_codebase(exclusions=None) -> dict:
    root = Path(support.ROOT).resolve(strict=True)
    parser = _parser()  # fail clearly before presenting an empty index
    del parser
    con = _db()
    stats = {"scanned": 0, "parsed": 0, "skipped": 0, "removed": 0, "failed": 0, "errors": []}
    present = set()
    try:
        known = {r["path"]: r for r in con.execute("SELECT * FROM files")}
        for path in _files(root, exclusions):
            rel = path.relative_to(root).as_posix(); present.add(rel); stats["scanned"] += 1
            try:
                data = path.read_bytes(); digest = hashlib.sha256(data).hexdigest(); st = path.stat()
                if rel in known and known[rel]["sha256"] == digest:
                    con.execute("UPDATE files SET size=?,mtime=? WHERE path=?", (st.st_size, st.st_mtime, rel)); stats["skipped"] += 1; continue
                symbols, refs = _extract(data)
                with con:
                    con.execute("DELETE FROM symbol_fts WHERE rowid IN (SELECT s.id FROM symbols s JOIN files f ON s.file_id=f.id WHERE f.path=?)", (rel,))
                    old = con.execute("SELECT id FROM files WHERE path=?", (rel,)).fetchone()
                    if old:
                        con.execute("DELETE FROM references_ WHERE file_id=?", (old["id"],))
                        con.execute("DELETE FROM symbols WHERE file_id=?", (old["id"],))
                        con.execute("DELETE FROM files WHERE id=?", (old["id"],))
                    cur = con.execute("INSERT INTO files(path,sha256,size,mtime,indexed_at) VALUES(?,?,?,?,?)", (rel,digest,st.st_size,st.st_mtime,time.time()))
                    fid = cur.lastrowid
                    for s in symbols:
                        cur = con.execute("INSERT INTO symbols(file_id,name,qualified_name,kind,namespace,containing_type,signature,docs,start_line,end_line,source,searchable) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (fid,s["name"],s["qualified_name"],s["kind"],s["namespace"],s["containing_type"],s["signature"],s["docs"],s["start_line"],s["end_line"],s["source"],s["searchable"]))
                        sid = cur.lastrowid
                        con.execute("INSERT INTO symbol_fts(rowid,name,qualified_name,signature,docs,searchable,path) VALUES(?,?,?,?,?,?,?)", (sid,s["name"],s["qualified_name"],_tokens(s["signature"]),_tokens(s["docs"]),s["searchable"],_tokens(rel)))
                    con.executemany("INSERT INTO references_(file_id,target,line,kind,snippet,enclosing_type,enclosing_method) VALUES(?,?,?,?,?,?,?)",
                        [(fid,r["target"],r["line"],r["kind"],r["snippet"],r["enclosing_type"],r["enclosing_method"]) for r in refs])
                stats["parsed"] += 1
            except Exception as exc:
                stats["failed"] += 1; stats["errors"].append({"path": rel, "message": str(exc)})
                print(f"[handy-coder-mcp] C# index failed for {rel}: {exc}", file=sys.stderr)
                # Preserve old rows on parse/read failure; record diagnostic when possible.
                if rel not in known:
                    with con: con.execute("INSERT OR IGNORE INTO files(path,sha256,size,mtime,indexed_at,error) VALUES(?,?,?,?,?,?)", (rel,"",0,0,time.time(),str(exc)))
        stale = set(known) - present
        with con:
            for rel in stale:
                con.execute("DELETE FROM symbol_fts WHERE rowid IN (SELECT s.id FROM symbols s JOIN files f ON s.file_id=f.id WHERE f.path=?)", (rel,))
                old = con.execute("SELECT id FROM files WHERE path=?", (rel,)).fetchone()
                if old:
                    con.execute("DELETE FROM references_ WHERE file_id=?", (old["id"],))
                    con.execute("DELETE FROM symbols WHERE file_id=?", (old["id"],))
                con.execute("DELETE FROM files WHERE path=?", (rel,))
            stats["removed"] = len(stale)
            con.execute("INSERT OR REPLACE INTO metadata VALUES('last_indexed_at',?)", (str(time.time()),))
        return stats
    finally:
        con.close()


def _fts_query(query):
    terms = list(dict.fromkeys(_tokens(query).split()))
    return " OR ".join('"' + t.replace('"','""') + '"' for t in terms)


def _limit(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ValueError("limit must be an integer from 1 to 50")


def code_navigator(action: str, query: str | None = None, symbol: str | None = None,
                   file_path: str | None = None, limit: int = 10) -> dict:
    _limit(limit)
    if action not in {"search_code", "get_symbol", "find_references"}:
        return {"error": "invalid_action", "message": "action must be search_code, get_symbol, or find_references"}
    if action == "search_code" and not (query and query.strip()): return {"error":"missing_parameter","message":"query is required and cannot be empty"}
    if action != "search_code" and not (symbol and symbol.strip()): return {"error":"missing_parameter","message":"symbol is required and cannot be empty"}
    con = _db()
    try:
        if action == "search_code":
            expr = _fts_query(query or "")
            if not expr: return {"results": []}
            rows = con.execute("SELECT s.*,f.path,bm25(symbol_fts,8.0,6.0,5.0,2.0,1.0,0.7) rank FROM symbol_fts JOIN symbols s ON s.id=symbol_fts.rowid JOIN files f ON f.id=s.file_id WHERE symbol_fts MATCH ? ORDER BY CASE WHEN lower(s.name)=lower(?) THEN 0 WHEN lower(s.qualified_name)=lower(?) THEN 1 ELSE 2 END, rank LIMIT ?", (expr,query.strip().split(".")[-1],query.strip(),limit)).fetchall()
            return {"results":[{"name":r["name"],"qualified_name":r["qualified_name"],"kind":r["kind"],"path":r["path"],"start_line":r["start_line"],"end_line":r["end_line"],"signature":r["signature"],"snippet":r["source"][:300],"score":round(-r["rank"],4)} for r in rows]}
        if action == "find_references":
            rows = con.execute("SELECT r.*,f.path FROM references_ r JOIN files f ON f.id=r.file_id WHERE lower(r.target)=lower(?) ORDER BY f.path,r.line LIMIT ?", (symbol.split(".")[-1],limit)).fetchall()
            return {"reference_type":"likely syntactic usages; not compiler-resolved","symbol":symbol,"references":[dict(r) for r in rows]}
        exact = symbol.strip()
        if "." in exact:
            sql = "SELECT s.*,f.path,f.id file_id FROM symbols s JOIN files f ON f.id=s.file_id WHERE lower(s.qualified_name)=lower(?)"
            params = [exact]
        else:
            sql = "SELECT s.*,f.path,f.id file_id FROM symbols s JOIN files f ON f.id=s.file_id WHERE lower(s.name)=lower(?)"
            params = [exact]
        if file_path:
            try: rel = support.inside(file_path, must_exist=True).relative_to(Path(support.ROOT).resolve()).as_posix()
            except (ValueError,OSError) as exc: return {"error":"inaccessible_file","message":str(exc)}
            sql += " AND f.path=?"; params.append(rel)
        rows = con.execute(sql + " ORDER BY f.path,s.start_line", params).fetchall()
        if len(rows) > 1: return {"error":"ambiguous_symbol","message":"Multiple symbols match; provide a qualified name or file_path","matches":[{"qualified_name":r["qualified_name"],"path":r["path"],"kind":r["kind"]} for r in rows[:20]]}
        if not rows: return {"error":"symbol_not_found","message":f"No indexed symbol matches {exact}"}
        row=rows[0]
        try:
            source_path = support.inside(row["path"], must_exist=True)
            data=source_path.read_bytes(); digest=hashlib.sha256(data).hexdigest()
        except (OSError,ValueError) as exc: return {"error":"inaccessible_file","message":str(exc)}
        indexed_digest = con.execute("SELECT sha256 FROM files WHERE id=?", (row["file_id"],)).fetchone()[0]
        con.close()
        if digest != indexed_digest:
            index_codebase()
            return code_navigator(action,symbol=symbol,file_path=file_path,limit=limit)
        lines=data.decode("utf-8", "replace").splitlines()
        # Use fresh indexed range; stale-content equality was verified above.
        return {"name":row["name"],"qualified_name":row["qualified_name"],"kind":row["kind"],"path":row["path"],"start_line":row["start_line"],"end_line":row["end_line"],"signature":row["signature"],"source":"\n".join(lines[row["start_line"]-1:row["end_line"]]),"containing_type":row["containing_type"]}
    finally:
        try: con.close()
        except Exception: pass
