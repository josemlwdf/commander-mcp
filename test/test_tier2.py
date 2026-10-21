import json
import os
import shlex
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.extras import (
    browser,
    code_format_lint,
    debugger,
    lsp_lookup,
    semantic_search,
    web_fetch,
)


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, description):
        def register(function):
            self.tools[function.__name__] = function
            return function
        return register


class Tier2ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.configure(str(Path(__file__).resolve().parents[1]))
        cls.tools = FakeMCP()
        for module in (lsp_lookup, semantic_search, code_format_lint, web_fetch, debugger, browser):
            module.register(cls.tools)

    def test_registers_six_tier_two_tools(self):
        self.assertEqual(
            set(self.tools.tools),
            {
                "lsp_symbol_lookup",
                "vector_search_codebase",
                "code_format_lint",
                "web_fetch_docs",
                "debug_session_step",
                "browser_action",
            },
        )

    def test_lsp_reports_unconfigured_backend(self):
        with patch.dict(os.environ, {"HANDY_CODER_LSP_COMMAND": ""}):
            result = self.tools.tools["lsp_symbol_lookup"](
                "main", "README.md", lsp_lookup.Position(line=0, character=0)
            )
        self.assertFalse(result["supported"])

    def test_lsp_rejects_negative_position(self):
        with self.assertRaises(ValueError):
            self.tools.tools["lsp_symbol_lookup"](
                "main", "README.md", lsp_lookup.Position(line=-1, character=0)
            )

    def test_lsp_uses_configured_server_for_definition_request(self):
        server = """
import json, sys
while True:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line in (b"\\r\\n", b"\\n"):
            break
        key, _, value = line.decode().partition(":")
        headers[key.lower()] = value.strip()
    message = json.loads(sys.stdin.buffer.read(int(headers["content-length"])))
    if message.get("method") == "textDocument/definition":
        body = json.dumps({"jsonrpc":"2.0","id":message["id"],"result":None}).encode()
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode() + body)
        sys.stdout.buffer.flush()
        break
    if "id" in message:
        body = json.dumps({"jsonrpc":"2.0","id":message["id"],"result":{}}).encode()
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode() + body)
        sys.stdout.buffer.flush()
"""
        command = shlex.join([sys.executable, "-c", server])
        with patch.dict(os.environ, {"HANDY_CODER_LSP_COMMAND": command}):
            result = self.tools.tools["lsp_symbol_lookup"](
                "main", "README.md", lsp_lookup.Position(line=0, character=0)
            )
        self.assertTrue(result["supported"])
        self.assertEqual(result["definitions"], [])

    def test_semantic_search_reports_missing_model(self):
        with patch.dict(os.environ, {"HANDY_CODER_EMBEDDING_MODEL": ""}):
            result = self.tools.tools["vector_search_codebase"]("where is auth initialized?")
        self.assertFalse(result["supported"])

    def test_web_search_reports_unconfigured_provider(self):
        with patch.dict(os.environ, {"HANDY_CODER_SEARCH_URL": ""}):
            result = self.tools.tools["web_fetch_docs"](search_query="python docs")
        self.assertFalse(result["supported"])

    def test_web_fetch_blocks_local_network(self):
        with self.assertRaises(ValueError):
            self.tools.tools["web_fetch_docs"](url="http://127.0.0.1/")

    def test_web_fetch_extracts_html_and_omits_scripts(self):
        with patch.object(web_fetch, "_request", return_value=(
            b"<html><title>Docs</title><script>secret()</script><p>Useful text</p></html>",
            "https://example.com/docs", "text/html",
        )):
            result = web_fetch._fetch("https://example.com/docs")
        self.assertEqual(result["title"], "Docs")
        self.assertIn("Useful text", result["content"])
        self.assertNotIn("secret", result["content"])

    def test_debugger_reports_missing_adapter(self):
        with patch.dict(os.environ, {"HANDY_CODER_DAP_PORT": ""}):
            result = self.tools.tools["debug_session_step"]("start")
        self.assertFalse(result["supported"])

    def test_debugger_rejects_non_loopback_adapter(self):
        with patch.dict(os.environ, {
            "HANDY_CODER_DAP_HOST": "192.168.1.10",
            "HANDY_CODER_DAP_PORT": "5678",
        }):
            with self.assertRaises(ValueError):
                debugger._configured_endpoint()

    def test_debugger_drives_local_dap_session(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def serve():
            connection, _ = listener.accept()
            stream = connection.makefile("rb")
            response_seq = 1
            try:
                while True:
                    headers = {}
                    while True:
                        line = stream.readline()
                        if line in (b"\r\n", b"\n"):
                            break
                        key, _, value = line.decode().partition(":")
                        headers[key.lower()] = value.strip()
                    message = json.loads(stream.read(int(headers["content-length"])))
                    if message["command"] == "initialize":
                        event = {"seq": response_seq, "type": "event", "event": "initialized"}
                        response_seq += 1
                        body = json.dumps(event).encode()
                        connection.sendall(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                    response = {
                        "seq": response_seq, "type": "response",
                        "request_seq": message["seq"], "success": True,
                        "command": message["command"], "body": {},
                    }
                    response_seq += 1
                    body = json.dumps(response).encode()
                    connection.sendall(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                    if message["command"] == "disconnect":
                        return
            finally:
                stream.close()
                connection.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        tool = self.tools.tools["debug_session_step"]
        env = {
            "HANDY_CODER_DAP_HOST": "127.0.0.1",
            "HANDY_CODER_DAP_PORT": str(port),
            "HANDY_CODER_DAP_START_REQUEST": '{"command":"launch","arguments":{}}',
        }
        try:
            with patch.dict(os.environ, env):
                started = tool("start")
                stopped = tool("stop")
            self.assertTrue(started["supported"])
            self.assertTrue(stopped["supported"])
        finally:
            listener.close()
            thread.join(timeout=2)

    def test_browser_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            browser._safe_url("file:///etc/passwd")

    def test_format_lint_requires_paths_and_confines_them(self):
        with self.assertRaises(ValueError):
            self.tools.tools["code_format_lint"]([])
        with self.assertRaises(ValueError):
            self.tools.tools["code_format_lint"](["/etc/passwd"])

    def test_format_lint_reports_missing_local_tool(self):
        with patch("lib.extras.code_format_lint.shutil.which", return_value=None):
            result = self.tools.tools["code_format_lint"](
                ["test/test_tier2.py"], language="python"
            )
        self.assertFalse(result["supported"])


if __name__ == "__main__":
    unittest.main()
