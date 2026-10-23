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
        with patch.dict(os.environ, {"HANDY-CODER_LSP_COMMAND": ""}):
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
        with patch.dict(os.environ, {"HANDY-CODER_LSP_COMMAND": command}):
            result = self.tools.tools["lsp_symbol_lookup"](
                "main", "README.md", lsp_lookup.Position(line=0, character=0)
            )
        self.assertTrue(result["supported"])
        self.assertEqual(result["definitions"], [])

    def test_semantic_search_reports_missing_model(self):
        with patch.dict(os.environ, {"HANDY-CODER_EMBEDDING_MODEL": ""}):
            result = self.tools.tools["vector_search_codebase"]("where is auth initialized?")
        self.assertFalse(result["supported"])

    def test_web_search_reports_unconfigured_provider(self):
        with patch.dict(os.environ, {"HANDY-CODER_SEARCH_URL": ""}):
            result = self.tools.tools["web_fetch_docs"](search_query="python docs")
        self.assertFalse(result["supported"])

    def test_web_fetch_blocks_local_network(self):
        with self.assertRaises(ValueError):
            self.tools.tools["web_fetch_docs"](url="http://127.0.0.1/")

    def test_web_fetch_extracts_html_and_omits_scripts(self):
        with patch.object(web_fetch, "_request", return_value=(
            b"<html><title>Docs</title><script>secret()</script><p>Useful text</p></html>",
            "https://example.com/docs", "text/html", 200, False,
        )):
            result = web_fetch._fetch("https://example.com/docs")
        self.assertEqual(result["title"], "Docs")
        self.assertIn("Useful text", result["content"])
        self.assertNotIn("secret", result["content"])

    def test_debugger_reports_missing_adapter(self):
        with patch.dict(os.environ, {"HANDY-CODER_DAP_PORT": ""}):
            result = self.tools.tools["debug_session_step"]("start")
        self.assertFalse(result["supported"])

    def test_debugger_rejects_non_loopback_adapter(self):
        with patch.dict(os.environ, {
            "HANDY-CODER_DAP_HOST": "192.168.1.10",
            "HANDY-CODER_DAP_PORT": "5678",
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
            "HANDY-CODER_DAP_HOST": "127.0.0.1",
            "HANDY-CODER_DAP_PORT": str(port),
            "HANDY-CODER_DAP_START_REQUEST": '{"command":"launch","arguments":{}}',
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

    def test_format_lint_text_is_noop_even_with_fix(self):
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as tmp:
            from lib import support as _support
            _support.configure(tmp)
            try:
                target = Path(tmp) / "notes.txt"
                target.write_text("keep this text\n", encoding="utf-8")
                with patch("lib.extras.code_format_lint.run_process") as run_process, \
                     patch("lib.extras.code_format_lint.process_python_file") as process_python:
                    result = self.tools.tools["code_format_lint"](
                        ["notes.txt"], language="text", fix=True
                    )
                self.assertEqual(target.read_text(encoding="utf-8"), "keep this text\n")
                run_process.assert_not_called()
                process_python.assert_not_called()
            finally:
                _support.configure(str(Path(__file__).resolve().parents[1]))
        self.assertTrue(result["supported"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["tools_run"], [])
        self.assertEqual(result["tools_skipped"], [])

    def test_format_lint_auto_infers_python_and_javascript(self):
        pass_result = {"stdout": "", "stderr": "", "exit_code": 0,
                       "timed_out": False, "duration_ms": 1}
        with patch("lib.extras.code_format_lint.shutil.which", return_value="/usr/bin/tool"), \
             patch("lib.extras.code_format_lint.run_process", return_value=pass_result):
            py = self.tools.tools["code_format_lint"](["test/test_tier2.py"])
            js = self.tools.tools["code_format_lint"](["README.md"])
        self.assertIn("ruff", py["tools_run"])
        self.assertIn("eslint", js["tools_run"])

    def test_jsbeautifier_checks_javascript_with_two_space_indent(self):
        import tempfile as _tempfile

        class Options:
            indent_size = 4

        class Formatter:
            @staticmethod
            def default_options():
                return Options()

            @staticmethod
            def beautify(source, options):
                self.assertEqual(options.indent_size, 2)
                return "function f() {\n  return 1;\n}\n"

        with _tempfile.TemporaryDirectory() as tmp:
            from lib import support as _support
            _support.configure(tmp)
            try:
                (Path(tmp) / "script.js").write_text("function f(){return 1;}", encoding="utf-8")
                (Path(tmp) / "clean.js").write_text("function f() {\n  return 1;\n}\n", encoding="utf-8")
                with patch("lib.extras.code_format_lint._jsbeautifier_module", return_value=Formatter), \
                     patch("lib.extras.code_format_lint._tool_path", return_value=None):
                    result = self.tools.tools["code_format_lint"](["script.js"], language="javascript")
                    clean = self.tools.tools["code_format_lint"](["clean.js"], language="javascript")
            finally:
                _support.configure(str(Path(__file__).resolve().parents[1]))
        self.assertFalse(result["ok"])
        report = next(item for item in result["reports"] if item["tool"] == "jsbeautifier")
        self.assertEqual(report["exit_code"], 1)
        clean_report = next(item for item in clean["reports"] if item["tool"] == "jsbeautifier")
        self.assertEqual(clean_report["exit_code"], 0)

    def test_jsbeautifier_fix_writes_file_and_returns_diff(self):
        import tempfile as _tempfile

        class Options:
            indent_size = 4

        class Formatter:
            @staticmethod
            def default_options():
                return Options()

            @staticmethod
            def beautify(source, options):
                return "const value = 1;\n"

        with _tempfile.TemporaryDirectory() as tmp:
            from lib import support as _support
            _support.configure(tmp)
            try:
                target = Path(tmp) / "script.js"
                target.write_text("const value=1", encoding="utf-8")
                with patch("lib.extras.code_format_lint._jsbeautifier_module", return_value=Formatter), \
                     patch("lib.extras.code_format_lint._tool_path", return_value=None):
                    result = self.tools.tools["code_format_lint"](["script.js"], fix=True, language="javascript")
                self.assertEqual(target.read_text(encoding="utf-8"), "const value = 1;\n")
            finally:
                _support.configure(str(Path(__file__).resolve().parents[1]))
        self.assertTrue(result["ok"])
        self.assertIn("script.js", result["diffs"])
        self.assertIn("jsbeautifier", result["tools_run"])

    def test_jsbeautifier_missing_is_reported_independently_of_eslint(self):
        with patch("lib.extras.code_format_lint._jsbeautifier_module", return_value=None), \
             patch("lib.extras.code_format_lint._tool_path", return_value=None):
            result = self.tools.tools["code_format_lint"](["README.md"], language="javascript")
        self.assertFalse(result["supported"])
        self.assertIn({"tool": "jsbeautifier", "reason": "not_installed"}, result["tools_skipped"])

    def test_format_lint_reports_missing_local_tool(self):
        with patch("lib.extras.code_format_lint.shutil.which", return_value=None):
            result = self.tools.tools["code_format_lint"](
                ["test/test_tier2.py"], language="python"
            )
        self.assertFalse(result["supported"])

    def test_web_fetch_returns_http_status_field(self):
        with patch.object(web_fetch, "_request", return_value=(
            b"<html><title>T</title><p>Hello</p></html>",
            "https://example.com/", "text/html", 200, False,
        )):
            result = self.tools.tools["web_fetch_docs"](url="https://example.com/")
        self.assertIn("http_status", result)
        self.assertEqual(result["http_status"], 200)

    def test_format_lint_returns_tools_run_and_tools_skipped(self):
        with patch("lib.extras.code_format_lint.shutil.which", return_value=None):
            result = self.tools.tools["code_format_lint"](
                ["test/test_tier2.py"], language="python"
            )
        self.assertIn("tools_run", result)
        self.assertIn("tools_skipped", result)
        self.assertIsInstance(result["tools_run"], list)
        self.assertIsInstance(result["tools_skipped"], list)

    def test_format_lint_reports_tools_run_when_tool_found(self):
        import shutil as _shutil
        ruff_path = _shutil.which("ruff")
        if not ruff_path:
            self.skipTest("ruff not installed")
        result = self.tools.tools["code_format_lint"](
            ["test/test_tier2.py"], language="python"
        )
        self.assertTrue(result["supported"])
        self.assertIn("ruff", result["tools_run"])
        self.assertIn("tools_skipped", result)

    # ------------------------------------------------------------------
    # Audit fix tests — tier-2 tools
    # ------------------------------------------------------------------

    def test_format_lint_tools_skipped_entries_have_reason(self):
        """tools_skipped entries must be dicts with 'tool' and 'reason' keys."""
        with patch("lib.extras.code_format_lint.shutil.which", return_value=None):
            result = self.tools.tools["code_format_lint"](
                ["test/test_tier2.py"], language="python"
            )
        self.assertFalse(result["supported"])
        for entry in result["tools_skipped"]:
            with self.subTest(entry=entry):
                self.assertIsInstance(entry, dict)
                self.assertIn("tool", entry)
                self.assertIn("reason", entry)

    def test_format_lint_fix_returns_diffs_for_changed_files(self):
        """fix=True must return a 'diffs' dict; unchanged files are omitted."""
        import tempfile as _tempfile
        import shutil as _shutil

        black_path = _shutil.which("black")
        ruff_path = _shutil.which("ruff")
        if not black_path and not ruff_path:
            self.skipTest("neither black nor ruff installed")

        with _tempfile.TemporaryDirectory() as tmp:
            from lib import support as _support
            _support.configure(tmp)
            ugly = (Path(tmp) / "ugly.py")
            ugly.write_text("x=1\n", encoding="utf-8")
            result = self.tools.tools["code_format_lint"](
                ["ugly.py"], fix=True, language="python"
            )
            _support.configure(str(Path(__file__).resolve().parents[1]))

        self.assertTrue(result["supported"])
        self.assertIn("diffs", result)
        self.assertIsInstance(result["diffs"], dict)

    def test_format_lint_mixed_languages_gives_clear_error(self):
        """Mixing .py and .js paths must give a message about mixed languages."""
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as tmp:
            from lib import support as _support
            _support.configure(tmp)
            try:
                (Path(tmp) / "a.py").write_text("x=1", encoding="utf-8")
                (Path(tmp) / "b.js").write_text("var x=1;", encoding="utf-8")
                result = self.tools.tools["code_format_lint"](["a.py", "b.js"])
            finally:
                _support.configure(str(Path(__file__).resolve().parents[1]))
        self.assertEqual(result.get("error_code"), "mixed_languages")
        self.assertIn("Python", result["message"])

    def test_format_lint_unsupported_extension_gives_clear_error(self):
        """An unsupported extension (e.g. .txt) must give a message listing extensions."""
        import tempfile as _tempfile
        with _tempfile.TemporaryDirectory() as tmp:
            from lib import support as _support
            _support.configure(tmp)
            try:
                (Path(tmp) / "notes.txt").write_text("hello", encoding="utf-8")
                result = self.tools.tools["code_format_lint"](["notes.txt"])
            finally:
                _support.configure(str(Path(__file__).resolve().parents[1]))
        self.assertEqual(result.get("error_code"), "unsupported_language")
        msg = result["message"]
        self.assertIn(".txt", msg)
        self.assertNotIn("Python", msg)  # not a mixed-language error

    def test_lsp_config_check_runs_before_argument_validation(self):
        """When HANDY-CODER_LSP_COMMAND is unset, supported:false is returned
        even before argument validation would run."""
        # README.md exists in the project root; position is valid.
        with patch.dict(os.environ, {"HANDY-CODER_LSP_COMMAND": ""}):
            result = self.tools.tools["lsp_symbol_lookup"](
                "main", "README.md", lsp_lookup.Position(line=0, character=0)
            )
        self.assertFalse(result["supported"])
        self.assertNotIn("error", result)

    def test_debugger_config_check_runs_before_cross_field_validation(self):
        """When HANDY-CODER_DAP_PORT is unset, supported:false is returned
        even if breakpoint_file/line cross-field constraint would trigger."""
        with patch.dict(os.environ, {"HANDY-CODER_DAP_PORT": ""}):
            # line without breakpoint_file would normally raise ValueError;
            # with no port configured we expect supported:false instead.
            result = self.tools.tools["debug_session_step"]("start", line=10)
        self.assertFalse(result["supported"])

    def test_web_fetch_offset_pages_through_content(self):
        """offset parameter must slice the extracted text at the right position."""
        long_html = b"<html><p>" + b"A" * 25000 + b"</p></html>"
        with patch.object(web_fetch, "_request", return_value=(
            long_html, "https://example.com/", "text/html", 200, False,
        )):
            first = web_fetch._fetch("https://example.com/", offset=0)
            second = web_fetch._fetch("https://example.com/", offset=20000)

        # The window is 20 000 characters; first page should be exactly 20 000.
        self.assertEqual(len(first["content"]), 20000)
        # Second page should be the remainder (total_length - 20000).
        total = first["total_length"]
        expected_second = total - 20000
        self.assertEqual(len(second["content"]), expected_second)
        self.assertNotEqual(first["content"], second["content"])
        # next_offset is populated when there is more content.
        self.assertEqual(first.get("next_offset"), 20000)
        # No more content after second window.
        self.assertIsNone(second.get("next_offset"))

    def test_web_fetch_no_duplicate_status_field(self):
        """The response must have http_status but not the duplicate status field."""
        with patch.object(web_fetch, "_request", return_value=(
            b"<html><title>T</title><p>Hi</p></html>",
            "https://example.com/", "text/html", 200, False,
        )):
            result = web_fetch._fetch("https://example.com/")
        self.assertIn("http_status", result)
        self.assertNotIn("status", result)

    def test_web_fetch_preserves_pre_and_code_blocks(self):
        """HTML <pre> and <code> blocks should be marked with markdown code fences."""
        html = b"<html><p>Normal text</p><pre>def foo():\n    return 42</pre></html>"
        with patch.object(web_fetch, "_request", return_value=(
            html, "https://example.com/code", "text/html", 200, False,
        )):
            result = web_fetch._fetch("https://example.com/code")
        self.assertIn("```", result["content"])
        self.assertIn("def foo():", result["content"])

    def _fetch_html(self, html: bytes) -> str:
        with patch.object(web_fetch, "_request", return_value=(
            html, "https://example.com/x", "text/html", 200, False,
        )):
            return web_fetch._fetch("https://example.com/x")["content"]

    def test_web_fetch_inline_code_uses_backticks_not_fences(self):
        content = self._fetch_html(b"<html><p>Use <code>foo()</code> here.</p></html>")
        self.assertIn("Use `foo()` here.", content)
        self.assertNotIn("```", content)

    def test_web_fetch_pre_code_gets_a_single_fence(self):
        content = self._fetch_html(b"<html><pre><code>x = 1</code></pre></html>")
        self.assertEqual(content.count("```"), 2)
        self.assertIn("x = 1", content)

    def test_web_fetch_strips_permalink_pilcrow(self):
        content = self._fetch_html(
            b'<html><h2>Install<a class="headerlink" href="#install">\xc2\xb6</a></h2></html>'
        )
        self.assertIn("Install", content)
        self.assertNotIn("\u00b6", content)

    def test_web_fetch_collapses_blank_lines_outside_code_only(self):
        content = self._fetch_html(
            b"<html><p>One</p>\n\n\n<div></div><div></div><p>a    b\n   c</p>"
            b"<pre>x\n\n\n\ny</pre></html>"
        )
        outside, _, rest = content.partition("```")
        self.assertNotIn("\n\n\n", outside)
        self.assertIn("a b c", outside)
        self.assertIn("x\n\n\n\ny", rest)  # fenced whitespace is preserved

    def test_format_lint_language_errors_return_error_codes(self):
        """Language selection failures must return distinct structured codes."""
        import tempfile as _tempfile
        from lib import support as _support
        with _tempfile.TemporaryDirectory() as tmp:
            _support.configure(tmp)
            try:
                (Path(tmp) / "a.py").write_text("x=1\n", encoding="utf-8")
                (Path(tmp) / "b.js").write_text("var x=1;\n", encoding="utf-8")
                (Path(tmp) / "notes.txt").write_text("hi\n", encoding="utf-8")
                cases = [
                    (["a.py", "b.js"], "auto", "mixed_languages"),
                    (["notes.txt"], "auto", "unsupported_language"),
                    (["b.js"], "python", "language_mismatch"),
                ]
                for paths, language, code in cases:
                    with self.subTest(code=code):
                        result = self.tools.tools["code_format_lint"](paths, language=language)
                        self.assertEqual(result.get("error_code"), code)
                        self.assertFalse(result.get("ok"))
                        self.assertTrue(result.get("message"))
                        self.assertNotIn(tmp, result["message"])
            finally:
                _support.configure(str(Path(__file__).resolve().parents[1]))

    def test_format_lint_ok_true_when_reports_pass(self):
        """ok must be True when all tools run and exit with code 0."""
        pass_result = {
            "stdout": "", "stderr": "", "exit_code": 0,
            "timed_out": False, "duration_ms": 10,
        }
        with patch("lib.extras.code_format_lint.shutil.which", return_value="/usr/bin/ruff"), \
             patch("lib.extras.code_format_lint.run_process", return_value=pass_result):
            result = self.tools.tools["code_format_lint"](["test/test_tier2.py"], language="python")
        self.assertTrue(result["supported"])
        self.assertTrue(result.get("ok"))

    def test_format_lint_ok_false_when_report_fails(self):
        """ok must be False when any tool exits with non-zero code."""
        fail_result = {
            "stdout": "error", "stderr": "", "exit_code": 1,
            "timed_out": False, "duration_ms": 10,
        }
        with patch("lib.extras.code_format_lint.shutil.which", return_value="/usr/bin/ruff"), \
             patch("lib.extras.code_format_lint.run_process", return_value=fail_result):
            result = self.tools.tools["code_format_lint"](["test/test_tier2.py"], language="python")
        self.assertTrue(result["supported"])
        self.assertFalse(result.get("ok"))

    def test_format_lint_report_tool_not_installed(self):
        """When a tool binary fails to start, report should have error_code='tool_not_installed'."""
        fail_to_start = {
            "stdout": "", "stderr": "Failed to start process: not found", "exit_code": 1,
            "timed_out": False, "duration_ms": 1,
        }
        with patch("lib.extras.code_format_lint.shutil.which", return_value="/usr/bin/ruff"), \
             patch("lib.extras.code_format_lint.run_process", return_value=fail_to_start):
            result = self.tools.tools["code_format_lint"](["test/test_tier2.py"], language="python")
        self.assertTrue(result["supported"])
        self.assertFalse(result.get("ok"))
        self.assertTrue(any(r.get("error_code") == "tool_not_installed" for r in result["reports"]))

    def _assert_unavailable(self, result, code, **fields):
        self.assertFalse(result["supported"])
        self.assertEqual(result.get("error_code"), code)
        self.assertTrue(result.get("message"))
        for key, value in fields.items():
            self.assertEqual(result.get(key), value)
            self.assertIn(value, result["message"])

    def test_unavailable_optional_tools_return_error_code_and_remedy(self):
        """Unconfigured vs missing-package must be distinguishable without parsing prose."""
        import sys
        from unittest.mock import MagicMock

        with patch.dict(os.environ, {"HANDY-CODER_LSP_COMMAND": ""}):
            result = self.tools.tools["lsp_symbol_lookup"](
                "main", "README.md", lsp_lookup.Position(line=0, character=0)
            )
        self._assert_unavailable(result, "not_configured", env_var="HANDY-CODER_LSP_COMMAND")

        with patch.dict(os.environ, {"HANDY-CODER_DAP_PORT": ""}):
            result = self.tools.tools["debug_session_step"]("start")
        self._assert_unavailable(result, "not_configured", env_var="HANDY-CODER_DAP_PORT")

        with patch.dict(os.environ, {"HANDY-CODER_EMBEDDING_MODEL": ""}):
            result = self.tools.tools["vector_search_codebase"]("anything")
        self._assert_unavailable(result, "not_configured", env_var="HANDY-CODER_EMBEDDING_MODEL")

        with patch.dict(os.environ, {"HANDY-CODER_EMBEDDING_MODEL": "some-model"}), \
             patch.dict(sys.modules, {"sentence_transformers": None}):
            result = self.tools.tools["vector_search_codebase"]("anything")
        self._assert_unavailable(result, "dependency_missing", package="sentence-transformers")

        with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
            result = self.tools.tools["browser_action"]("get_html")
        self._assert_unavailable(result, "dependency_missing", package="playwright")

        fake_api = MagicMock()
        fake_api.sync_playwright.return_value.start.return_value.chromium.launch.side_effect = (
            Exception("Executable doesn't exist")
        )
        with patch.dict(sys.modules, {"playwright": MagicMock(), "playwright.sync_api": fake_api}):
            result = self.tools.tools["browser_action"]("get_html")
        self.assertFalse(result["supported"])
        self.assertEqual(result.get("error_code"), "browser_launch_failed")
        self.assertIn("playwright install chromium", result["message"])

    def test_capabilities_workspace_root_tracks_configured_root(self):
        """workspace_root must reflect support.configure(), not the import-time ROOT."""
        import tempfile as _tempfile
        from lib import support as _support
        from lib.core import system_info
        with _tempfile.TemporaryDirectory() as tmp:
            _support.configure(tmp)
            try:
                caps = system_info.get_capabilities()
            finally:
                _support.configure(str(Path(__file__).resolve().parents[1]))
        self.assertEqual(caps["workspace_root"], str(Path(tmp).resolve()))

    def test_system_info_returns_expected_structure(self):
        """system_info.get_capabilities must return workspace_root, tools, and env."""
        from lib.core import system_info
        caps = system_info.get_capabilities()
        self.assertIn("workspace_root", caps)
        self.assertIn("tools", caps)
        self.assertIn("env", caps)
        self.assertIn("read_files", caps["tools"])
        self.assertIn("apply_edits", caps["tools"])
        self.assertIn("terminal_exec", caps["tools"])
        self.assertIn("jsbeautifier", caps["tools"]["code_format_lint"]["tools"])
        self.assertNotIn("prettier", caps["tools"]["code_format_lint"]["tools"])


if __name__ == "__main__":
    unittest.main()
