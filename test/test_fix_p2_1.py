import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP

from lib import error_handler, support, tool_details, worker_supervisor
from lib.core import (
    commit_nudge, find_files, git_manage, orchestrator_tasks, read_files,
    terminal_exec, web_search_tool,
)


class StructuredErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        support.configure(str(Path(cls.temp_dir.name).resolve()))
        cls.mcp = FastMCP("t")
        tool_details.install(cls.mcp)
        for module in (
            read_files, terminal_exec, find_files, git_manage,
            commit_nudge, web_search_tool, orchestrator_tasks,
        ):
            module.register(cls.mcp)
        worker_supervisor.supervise_fastmcp_tools(cls.mcp)
        cls.installed = error_handler.install(cls.mcp)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))

    def call(self, name, arguments, convert_result=False):
        return asyncio.run(
            self.mcp._tool_manager.call_tool(name, arguments, convert_result=convert_result)
        )

    def assert_structured(self, result):
        self.assertIsInstance(result, dict, result)
        self.assertIn("error_code", result)
        self.assertIn("message", result)
        self.assertTrue(result["message"])
        self.assertNotIn("pydantic.dev", json.dumps(result))
        self.assertNotIn("input_type", json.dumps(result))

    def test_install_succeeded_once_and_is_idempotent(self):
        self.assertTrue(self.installed)
        self.assertFalse(error_handler.install(self.mcp))

    def test_install_is_noop_without_tool_manager(self):
        self.assertFalse(error_handler.install(SimpleNamespace()))
        self.assertFalse(error_handler.install(SimpleNamespace(_tool_manager=SimpleNamespace(_tools={}))))

    def test_defer_commit_validation_error(self):
        result = self.call("defer_commit", {"action": "defer", "calls": -3})
        self.assert_structured(result)
        self.assertEqual(result["error_code"], "invalid_argument")
        self.assertEqual(result.get("field"), "calls")

    def test_web_search_wrong_type(self):
        self.assert_structured(self.call("web_search", {"query": 123}))

    def test_orchestrator_update_task_status_invalid_status(self):
        self.assert_structured(
            self.call("orchestrator_update_task_status", {"status": "bogus", "line_number": 1})
        )

    def test_git_manage_invalid_action(self):
        result = self.call("git_manage", {"action": "bogus"})
        self.assert_structured(result)
        self.assertEqual(result["error_code"], "invalid_argument")

    def test_get_tool_details_unknown_tool(self):
        result = self.call("get_tool_details", {"tool_name": "nope"})
        self.assert_structured(result)
        self.assertEqual(result["error_code"], "unknown_tool")

    def test_read_files_invalid_line_range(self):
        result = self.call(
            "read_files",
            {"paths": ["a.txt"], "line_ranges": [{"path": "a.txt", "start_line": 0, "end_line": 1}]},
        )
        self.assert_structured(result)

    def test_find_files_invalid_sort_by(self):
        self.assert_structured(self.call("find_files", {"glob_pattern": "*", "sort_by": "bogus"}))

    def test_terminal_exec_empty_command(self):
        result = self.call("terminal_exec", {"command": "  "})
        self.assert_structured(result)
        self.assertIn("empty", result["message"])

    def test_unknown_tool_name_is_structured(self):
        result = self.call("does_not_exist", {})
        self.assert_structured(result)
        self.assertEqual(result["error_code"], "unknown_tool")

    def test_valid_call_still_returns_normal_result(self):
        result = self.call("get_tool_details", {"tool_name": "read_files"})
        self.assertEqual(result["tool_name"], "read_files")
        self.assertNotIn("error_code", result)

    def test_convert_result_path_returns_content_without_pydantic_text(self):
        converted = self.call("defer_commit", {"action": "defer", "calls": -3}, convert_result=True)
        self.assertNotIn("pydantic.dev", str(converted))
        self.assertIn("invalid_argument", str(converted))


if __name__ == "__main__":
    unittest.main()