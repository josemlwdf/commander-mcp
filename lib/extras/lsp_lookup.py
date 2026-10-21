"""Workspace-local LSP symbol lookup."""
from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field

from .. import support
from ..support import inside, describe


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line: int = Field(ge=0, description="0-based source line containing the symbol position.")
    character: int = Field(ge=0, description="0-based character offset within the source line.")


def _reader(stream, messages: queue.Queue) -> None:
    try:
        while True:
            headers = {}
            while True:
                line = stream.readline()
                if not line:
                    return
                if line in (b"\r\n", b"\n"):
                    break
                key, _, value = line.decode("ascii").partition(":")
                headers[key.lower()] = value.strip()
            length = int(headers["content-length"])
            if not 1 <= length <= 4 * 1024 * 1024:
                raise RuntimeError("LSP server response exceeds the 4 MiB limit")
            chunks = []
            remaining = length
            while remaining:
                chunk = stream.read(remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            body = b"".join(chunks)
            if len(body) != length:
                raise RuntimeError("LSP server closed during a response")
            messages.put(json.loads(body))
    except Exception as exc:
        messages.put(exc)


def _lsp_request(process: subprocess.Popen, messages: queue.Queue, seq: int,
                 method: str, params: dict, timeout: float = 10.0) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": seq, "method": method, "params": params}).encode()
    process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    process.stdin.flush()
    while True:
        try:
            message = messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"LSP server did not respond to {method} within {timeout:g}s") from exc
        if isinstance(message, Exception):
            raise RuntimeError(f"Could not read LSP response: {message}") from message
        if message.get("id") != seq:
            continue
        if "error" in message:
            raise RuntimeError(f"LSP {method} failed: {message['error']}")
        return message


def _send_notification(process: subprocess.Popen, method: str, params: dict) -> None:
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params}).encode()
    process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    process.stdin.flush()


def _language_id(path: Path) -> str:
    return {
        ".py": "python", ".js": "javascript", ".jsx": "javascriptreact",
        ".ts": "typescript", ".tsx": "typescriptreact", ".go": "go",
        ".rs": "rust", ".java": "java", ".c": "c", ".h": "c",
        ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    }.get(path.suffix.lower(), "plaintext")


def _locations(result: object) -> list[dict]:
    if result is None:
        return []
    locations = result if isinstance(result, list) else [result]
    output = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        uri = location.get("uri") or location.get("targetUri")
        range_data = location.get("range") or location.get("targetSelectionRange") or location.get("targetRange")
        if not uri or not isinstance(range_data, dict):
            continue
        parsed = urlparse(uri)
        path = unquote(parsed.path) if parsed.scheme == "file" else None
        output.append({
            "uri": uri,
            "path": str(Path(path).resolve().relative_to(support.ROOT))
            if path and Path(path).resolve().is_relative_to(support.ROOT) else None,
            "range": range_data,
        })
    return output


def lsp_symbol_lookup(
    symbol_name: Annotated[str, Field(description="Name of the symbol to look up; used to label the response.")],
    path: Annotated[str, Field(description="Workspace-relative path to the source file.")],
    position: Annotated[Position, Field(description="0-based line and character position used to request the definition from the language server.")],
) -> dict:
        if not symbol_name.strip():
            raise ValueError("symbol_name cannot be empty")
        source = inside(path, must_exist=True)
        if not source.is_file():
            raise ValueError(f"Not a file: {path}")
        if position.line < 0 or position.character < 0:
            raise ValueError("LSP line and character positions are 0-based and must be non-negative")
        configured = os.environ.get("HANDY_CODER_LSP_COMMAND", "").strip()
        if not configured:
            return {"supported": False, "message": "Set HANDY_CODER_LSP_COMMAND to a local language-server command to enable LSP lookup."}
        command = shlex.split(configured, posix=os.name != "nt")
        if not command:
            raise ValueError("HANDY_CODER_LSP_COMMAND must contain an executable")

        process = subprocess.Popen(
            command, cwd=support.ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
        messages: queue.Queue = queue.Queue()
        reader = threading.Thread(target=_reader, args=(process.stdout, messages), daemon=True)
        reader.start()
        uri = source.as_uri()
        seq = 1
        try:
            _lsp_request(process, messages, seq, "initialize", {
                "processId": os.getpid(),
                "rootUri": support.ROOT.as_uri(),
                "capabilities": {},
            })
            seq += 1
            _send_notification(process, "initialized", {})
            content = source.read_text(encoding="utf-8")
            _send_notification(process, "textDocument/didOpen", {
                "textDocument": {
                    "uri": uri, "languageId": _language_id(source),
                    "version": 1, "text": content,
                }
            })
            response = _lsp_request(process, messages, seq, "textDocument/definition", {
                "textDocument": {"uri": uri},
                "position": {"line": position.line, "character": position.character},
            })
            return {
                "supported": True,
                "symbol_name": symbol_name,
                "definitions": _locations(response.get("result")),
            }
        finally:
            if process.poll() is None:
                try:
                    _send_notification(process, "exit", {})
                    process.terminate()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait()
            process.stdin.close()
            process.stdout.close()


def register(mcp):
    return mcp.tool(description=describe(
        "Look up a definition at a 0-based source position using HANDY_CODER_LSP_COMMAND (no shell). "
        "Requires HANDY_CODER_LSP_COMMAND set to a local language server command. "
        "When to use: Navigate to a symbol's definition."
    ))(lsp_symbol_lookup)

