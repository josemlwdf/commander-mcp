import importlib
import unittest
from pathlib import Path

from lib import support
from lib.subagents import tools as subagent_tools


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, description):
        def register(function):
            self.tools[function.__name__] = function
            return function

        return register


class SubagentIsolationSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.configure(str(Path(__file__).resolve().parents[1]))

    def setUp(self):
        importlib.reload(subagent_tools)
        self.mcp = FakeMCP()
        subagent_tools.register(self.mcp)
        subagent_tools._BACKEND = "cooperative"
        subagent_tools._COOP_EXPLICIT = True

    def test_subagent_scope_isolated_from_parent(self):
        run = self.mcp.tools["subagent_run"](role="explorer", task="inspect README")
        self.assertEqual(run["status"], "delegated_to_host")
        task_id = run["task_id"]

        # Inside subagent scope: role-allowed tools pass, disallowed tools are blocked.
        self.assertIsNone(subagent_tools.before_tool_call("read_files", {"paths": ["README.md"]}))
        blocked = subagent_tools.before_tool_call("apply_edits", {"edits": []})
        self.assertEqual(blocked["error_code"], "subagent_scope_violation")

        # Completing the subagent frame returns control to the parent scope.
        done = self.mcp.tools["subagent_complete"](task_id=task_id, result="- finding: README checked")
        self.assertEqual(done["state"], "done")
        self.assertIsNone(subagent_tools.before_tool_call("apply_edits", {"edits": []}))


if __name__ == "__main__":
    unittest.main()
