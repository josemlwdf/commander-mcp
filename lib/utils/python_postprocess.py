"""Conservative source level cleanup for Python files."""
from __future__ import annotations

import ast
import io
from pathlib import Path
import tokenize


def _literal_type(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None:
            return "None"
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "float"
        if isinstance(value, str):
            return "str"
        if isinstance(value, bytes):
            return "bytes"
    if isinstance(node, (ast.List, ast.ListComp)):
        return "list"
    if isinstance(node, ast.Tuple):
        return "tuple"
    if isinstance(node, (ast.Dict, ast.DictComp)):
        return "dict"
    if isinstance(node, (ast.Set, ast.SetComp)):
        return "set"
    return None


def _summary(name: str, kind: str) -> str:
    words = name.replace("_", " ").strip() or kind
    return f"{words[0].upper() + words[1:]} {kind}."


def _infer_return(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and child is not node:
            continue
        if isinstance(child, ast.Return):
            if child.value is None:
                found.add("None")
            else:
                inferred = _literal_type(child.value)
                if inferred is None:
                    return None
                found.add(inferred)
    if not found:
        return "None"
    return next(iter(found)) if len(found) == 1 else None


def _signature_colon(text: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    depth = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.start[0] < node.lineno:
                continue
            if token.type == tokenize.OP:
                if token.string in "([{":
                    depth += 1
                elif token.string in ")]}":
                    depth -= 1
                elif token.string == ":" and depth == 0:
                    return offsets[token.start[0] - 1] + token.start[1]
    except tokenize.TokenError:
        return None
    return None


def process_python_file(path: str | Path) -> dict:
    """Normalize line endings and add safe, obvious docs and annotations."""
    file_path = Path(path)
    before_bytes = file_path.read_bytes()
    text = before_bytes.decode("utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    report: dict = {"path": str(file_path), "ok": True, "changes": [], "uncertain": [], "error": None}
    if normalized != text:
        report["changes"].append("unix_line_endings")
    text = normalized
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        report.update(ok=False, error=f"Python syntax error at line {exc.lineno}: {exc.msg}")
        if text != normalized or normalized != before_bytes.decode("utf-8"):
            file_path.write_text(text, encoding="utf-8", newline="")
        return report

    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits: list[tuple[int, str]] = []
    if not ast.get_docstring(tree):
        module_pos = 0
        for index, line in enumerate(lines[:2]):
            if (index == 0 and line.startswith("#!")) or "coding" in line:
                module_pos = offsets[index + 1]
        edits.append((module_pos, '"""Module documentation."""\n'))
        report["changes"].append("module_docstring")

    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in nodes:
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        if not ast.get_docstring(node):
            body = node.body[0]
            indent = " " * body.col_offset
            pos = offsets[body.lineno - 1]
            edits.append((pos, f'{indent}"""{_summary(node.name, kind)}"""\n'))
            report["changes"].append(f"docstring:{node.name}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if node.args.vararg:
                args.append(node.args.vararg)
            if node.args.kwarg:
                args.append(node.args.kwarg)
            defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
            # Only positional default alignment is safe; keyword-only defaults are handled separately.
            for arg, default in zip([*node.args.posonlyargs, *node.args.args], defaults):
                if arg.annotation is None:
                    inferred = _literal_type(default)
                    if inferred:
                        pos = offsets[arg.lineno - 1] + arg.col_offset + len(arg.arg)
                        edits.append((pos, f": {inferred}") )
                        report["changes"].append(f"parameter:{node.name}.{arg.arg}")
                    else:
                        report["uncertain"].append(f"parameter:{node.name}.{arg.arg}")
            if node.returns is None:
                inferred = _infer_return(node)
                if inferred:
                    pos = _signature_colon(text, node)
                    if pos is not None:
                        edits.append((pos, f" -> {inferred}"))
                        report["changes"].append(f"return:{node.name}")
                else:
                    report["uncertain"].append(f"return:{node.name}")

    # Annotate simple, unannotated local assignments only when the RHS is a literal.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0]
            inferred = _literal_type(node.value)
            if inferred:
                pos = offsets[target.lineno - 1] + target.col_offset + len(target.id)
                edits.append((pos, f": {inferred}"))
                report["changes"].append(f"variable:{target.id}")
            else:
                report["uncertain"].append(f"variable:{target.id}")

    for pos, value in sorted(edits, reverse=True):
        text = text[:pos] + value + text[pos:]
    if text.encode("utf-8") != before_bytes:
        file_path.write_text(text, encoding="utf-8", newline="")
    return report
