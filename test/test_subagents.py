import os
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.subagents import tools as subagent_tools
from lib.subagents.resolve import resolve_backend


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, description):
        def register(function):
            self.tools[function.__name__] = function
            return function

        return register


class SubagentToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.configure(str(Path(__file__).resolve().parents[1]))

    def setUp(self):
        # reset module-level singleton state by reloading in-process
        import importlib

        importlib.reload(subagent_tools)
        self.mcp = FakeMCP()
        subagent_tools.register(self.mcp)

    def test_registers_five_subagent_tools(self):
        self.assertEqual(
            set(self.mcp.tools),
            {
                "subagent_run",
                "subagent_status",
                "subagent_result",
                "subagent_complete",
                "subagent_cancel",
            },
        )

    def test_cooperative_lifecycle(self):
        run = self.mcp.tools["subagent_run"](role="explorer", task="inspect README")
        self.assertEqual(run["status"], "delegated_to_host")
        task_id = run["task_id"]

        status = self.mcp.tools["subagent_status"](task_id=task_id)
        self.assertEqual(status["state"], "delegated")
        self.assertEqual(status["backend"], "cooperative")

        result = self.mcp.tools["subagent_complete"](
            task_id=task_id,
            result="- finding: README exists\n- conclusion: docs are present",
        )
        self.assertEqual(result["status"], "completed")
        page = self.mcp.tools["subagent_result"](task_id=task_id)
        self.assertIn("README", page["result"])

    def test_scope_guard_blocks_out_of_role_tool(self):
        run = self.mcp.tools["subagent_run"](role="explorer", task="inspect")
        self.assertEqual(run["status"], "delegated_to_host")
        blocked = subagent_tools.before_tool_call("apply_edits", {})
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["error_code"], "subagent_scope_violation")

    def test_reminder_trailer_is_appended(self):
        self.mcp.tools["subagent_run"](role="explorer", task="inspect")
        wrapped = subagent_tools.after_tool_call("read_files", {"message": "ok"})
        self.assertIn("subagent_reminder", wrapped)
        self.assertIn("subagent_complete", wrapped["subagent_reminder"])


class ResolveBackendTests(unittest.TestCase):
    def test_resolve_backend_env_matrix(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_backend({}), "cooperative")

        with patch.dict(os.environ, {"OPENAI_API_KEY": "x"}, clear=True):
            self.assertEqual(resolve_backend({}), "direct:openai")

        with patch.dict(os.environ, {"SUBAGENT_BACKEND": "cooperative"}, clear=True):
            self.assertEqual(resolve_backend({"sampling": True}), "cooperative")

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_backend({"sampling": True}), "sampling")


if __name__ == "__main__":
    unittest.main()
