import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

from lib import error_handler, support, tool_details, worker_supervisor
from lib.core import (
    apply_edits, commit_nudge, create_file, find_files, git_manage,
    html_syntax_check, orchestrator_tasks, read_files, terminal_exec, web_search_tool,
)
from lib.subagents import tools as subagent_tools
from lib.subagents.manager import SubagentManager


def _build_server():
    mcp = FastMCP("acceptance")
    tool_details.install(mcp)
    for module in (
        read_files, create_file, apply_edits, terminal_exec, find_files, git_manage,
        commit_nudge, html_syntax_check, web_search_tool, orchestrator_tasks,
    ):
        module.register(mcp)
    worker_supervisor.supervise_fastmcp_tools(mcp)
    error_handler.install(mcp)
    return mcp


def _call(mcp, name, arguments, convert_result=False):
    return asyncio.run(mcp._tool_manager.call_tool(name, arguments, convert_result=convert_result))


class RepeatedWritesLeaveNoSubagentFrameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        support.configure(str(self.root))
        for cmd in (
            ["git", "init", "--quiet"],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "T"],
        ):
            subprocess.run(cmd, cwd=self.root, check=True)
        (self.root / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "seed.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=self.root, check=True)
        commit_nudge._reset_for_tests()

        self.manager = SubagentManager()
        for patcher in (
            patch.object(subagent_tools, "_MANAGER", self.manager),
            patch.object(subagent_tools, "_BACKEND", "cooperative"),
            patch.dict(os.environ, {}, clear=False),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("HANDY_CODER_COMMIT_SUBAGENT", None)
        self.mcp = _build_server()

    def tearDown(self):
        self.temp.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))
        commit_nudge._reset_for_tests()

    def test_five_writes_in_a_row_leave_no_frame_and_html_check_runs(self):
        body = "\n".join(f"line {i}" for i in range(40)) + "\n"
        results = []
        for index in range(3):
            results.append(_call(self.mcp, "create_file", {"path": f"f{index}.txt", "content": body}))
        for index in range(2):
            results.append(_call(self.mcp, "apply_edits", {
                "edits": [{"path": f"f{index}.txt", "pattern": "line 1\\b", "replacement": "changed"}],
            }))
        for result in results:
            self.assertNotIn("error_code", result, result)
        nudges = [r["commit_nudge"] for r in results if "commit_nudge" in r]
        self.assertTrue(nudges, "expected at least one commit nudge on the real call path")
        for nudge in nudges:
            self.assertIsNone(nudge["task_id"])
            self.assertEqual(nudge["task_status"], "disabled")
        self.assertIsNone(self.manager.open_frame())

        (self.root / "page.html").write_text("<p>ok</p>\n", encoding="utf-8")
        check = _call(self.mcp, "html_syntax_check", {"path": "page.html"})
        self.assertNotEqual(check.get("error_code"), "subagent_scope_violation", check)
        self.assertNotIn("error_code", check, check)


class InvalidCallsAreStructuredTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        support.configure(str(Path(cls.temp.name).resolve()))
        cls.mcp = _build_server()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))

    CASES = (
        ("defer_commit", {"action": "defer", "calls": 999}),
        ("web_search", {"query": ["not", "a", "string"]}),
        ("orchestrator_update_task_status", {"status": "bogus", "task_contains": "x"}),
        ("git_manage", {"action": 123}),
        ("get_tool_details", {"tool_name": ""}),
        ("read_files", {"paths": ["a.txt"], "line_ranges": [
            {"path": "a.txt", "start_line": 1, "end_line": 2, "bogus": 1}]}),
        ("find_files", {"glob_pattern": "*", "sort_by": 5}),
        ("terminal_exec", {}),
    )

    def test_each_invalid_call_returns_error_code_and_message(self):
        for name, arguments in self.CASES:
            with self.subTest(tool=name):
                result = _call(self.mcp, name, arguments)
                self.assertIsInstance(result, dict, result)
                self.assertIn("error_code", result, result)
                self.assertTrue(result.get("message"), result)
                self.assertNotIn("pydantic.dev", json.dumps(result))


if __name__ == "__main__":
    unittest.main()