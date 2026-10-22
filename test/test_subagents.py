import os
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.subagents import tools as subagent_tools
from lib.subagents import loop as subagent_loop
from lib.subagents.resolve import resolve_backend
from lib.subagents.roles import get_role


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


    def test_direct_backend_wait_completes_inline(self):
        subagent_tools.configure_runtime(client_capabilities=None)
        subagent_tools._BACKEND = "direct:openai"

        with patch.object(subagent_loop, "run_loop", return_value=("- finding: quick result", 1, 42)):
            out = self.mcp.tools["subagent_run"](role="explorer", task="inspect quickly", wait=True)

        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["backend"], "direct:openai")
        self.assertIn("finding", out["result"])
        self.assertEqual(out["usage"]["turns"], 1)

    def test_direct_backend_can_return_running(self):
        subagent_tools.configure_runtime(client_capabilities=None)
        subagent_tools._BACKEND = "direct:openai"

        def slow_run(*_args, **_kwargs):
            import time

            time.sleep(0.8)
            return ("- finding: delayed", 1, 11)

        with patch.dict(os.environ, {"SUBAGENT_WAIT_SECONDS": "0.05"}, clear=False):
            with patch.object(subagent_loop, "run_loop", side_effect=slow_run):
                out = self.mcp.tools["subagent_run"](role="explorer", task="inspect slowly", wait=True)

        self.assertEqual(out["status"], "running")
        status = self.mcp.tools["subagent_status"](task_id=out["task_id"])
        self.assertIn(status["state"], {"running", "done"})

    def test_cancelled_running_task_stays_cancelled(self):
        subagent_tools.configure_runtime(client_capabilities=None)
        subagent_tools._BACKEND = "direct:openai"

        def slow_run(*_args, **_kwargs):
            import time

            time.sleep(0.6)
            return ("- finding: delayed", 1, 11)

        with patch.dict(os.environ, {"SUBAGENT_WAIT_SECONDS": "0.05"}, clear=False):
            with patch.object(subagent_loop, "run_loop", side_effect=slow_run):
                out = self.mcp.tools["subagent_run"](role="explorer", task="cancel me", wait=True)
                self.assertEqual(out["status"], "running")
                cancelled = self.mcp.tools["subagent_cancel"](task_id=out["task_id"])

        self.assertEqual(cancelled["state"], "cancelled")

        import time

        time.sleep(0.7)
        status = self.mcp.tools["subagent_status"](task_id=out["task_id"])
        self.assertEqual(status["state"], "cancelled")


class LoopBackendTests(unittest.TestCase):
    def test_run_loop_returns_final_result(self):
        role = get_role("explorer")
        self.assertIsNotNone(role)

        def sampling_fn(_system_prompt, _messages, _max_tokens):
            return '{"final":"- finding: completed"}'

        result, turns, tokens = subagent_loop.run_loop(
            "sampling",
            role,
            "inspect",
            None,
            None,
            sampling_fn=sampling_fn,
        )
        self.assertIn("finding", result)
        self.assertEqual(turns, 1)
        self.assertGreater(tokens, 0)

    def test_run_loop_executes_allowed_tool_then_finishes(self):
        role = get_role("explorer")
        self.assertIsNotNone(role)
        calls = {"n": 0}

        def sampling_fn(_system_prompt, _messages, _max_tokens):
            if calls["n"] == 0:
                calls["n"] += 1
                return '{"tool":"find_files","args":{"glob_pattern":"README.md","exclude_pattern":null,"include_generated":false}}'
            return '{"final":"- finding: done after tool"}'

        result, turns, _tokens = subagent_loop.run_loop(
            "sampling",
            role,
            "inspect",
            None,
            None,
            sampling_fn=sampling_fn,
        )
        self.assertIn("finding", result)
        self.assertEqual(turns, 2)

    def test_run_loop_rejects_disallowed_tool(self):
        role = get_role("explorer")
        self.assertIsNotNone(role)

        def sampling_fn(_system_prompt, _messages, _max_tokens):
            return '{"tool":"apply_edits","args":{}}'

        with self.assertRaises(RuntimeError) as ctx:
            subagent_loop.run_loop(
                "sampling",
                role,
                "inspect",
                None,
                None,
                sampling_fn=sampling_fn,
            )
        self.assertIn("outside role allowlist", str(ctx.exception))


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
