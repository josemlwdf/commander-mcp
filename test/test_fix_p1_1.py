import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.core import commit_nudge
from lib.subagents import tools as subagent_tools
from lib.subagents.manager import SubagentManager


class CommitNudgeTextOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        support.configure(str(self.root))
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.root, check=True)
        (self.root / "seed.txt").write_text("1\n", encoding="utf-8")
        subprocess.run(["git", "add", "seed.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=self.root, check=True)
        commit_nudge._reset_for_tests()

        self.manager = SubagentManager()
        for target, value in (
            (patch.object(subagent_tools, "_MANAGER", self.manager), None),
            (patch.object(subagent_tools, "_BACKEND", "cooperative"), None),
            (patch.object(subagent_tools, "_COOP_EXPLICIT", True), None),
            (patch.dict(os.environ, {}, clear=False), None),
        ):
            target.start()
            self.addCleanup(target.stop)
        os.environ.pop("HANDY_CODER_COMMIT_SUBAGENT", None)

    def tearDown(self):
        self.temp_dir.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))
        commit_nudge._reset_for_tests()

    def _write(self, index: int) -> dict:
        name = f"file{index}.txt"
        (self.root / name).write_text("\n".join(f"line {i}" for i in range(40)) + "\n", encoding="utf-8")
        return commit_nudge.after_tool_call(
            "create_file",
            {"path": name},
            {"path": name, "created": True, "bytes": 1, "lines": 40},
        )

    def test_repeated_writes_never_open_a_subagent_frame(self):
        nudges = []
        for index in range(6):
            result = self._write(index)
            if "commit_nudge" in result:
                nudges.append(result["commit_nudge"])
        self.assertTrue(nudges, "expected at least one text nudge")
        for nudge in nudges:
            self.assertIsNone(nudge["task_id"])
            self.assertEqual(nudge["task_status"], "disabled")
            self.assertIn("Review .gitignore", nudge["message"])
            for key in ("level", "reason", "lines", "files", "changed_files"):
                self.assertIn(key, nudge)
        self.assertIsNone(self.manager.open_frame())
        self.assertIsNone(subagent_tools.before_tool_call("html_syntax_check", {}))

    def test_generated_artifacts_are_ignored_in_metrics(self):
        (self.root / ".handy-coder").mkdir(exist_ok=True)
        (self.root / ".handy-coder" / "code_index.sqlite3").write_bytes(b"x\n" * 100)
        (self.root / "cache.sqlite3").write_bytes(b"x\n" * 100)
        metrics = commit_nudge.measure_diff()
        self.assertEqual(metrics.changed_files, [])
        self.assertEqual(metrics.lines, 0)

    def test_opt_in_env_var_still_starts_committer_task(self):
        with patch.dict(os.environ, {"HANDY_CODER_COMMIT_SUBAGENT": "1"}):
            results = [self._write(index) for index in range(3)]
        nudge = next(r["commit_nudge"] for r in results if "commit_nudge" in r)
        self.assertIsNotNone(nudge["task_id"])
        self.assertEqual(nudge["task_status"], "delegated_to_host")
        frame = self.manager.open_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.role.name, "committer")

    def test_scope_violation_message_explains_recovery(self):
        with patch.dict(os.environ, {"HANDY_CODER_COMMIT_SUBAGENT": "1"}):
            for index in range(3):
                self._write(index)
        blocked = subagent_tools.before_tool_call("html_syntax_check", {})
        self.assertEqual(blocked["error_code"], "subagent_scope_violation")
        self.assertIn("subagent_cancel", blocked["message"])
        self.assertIn("confirm=true", blocked["message"])


    def test_unavailable_backend_falls_back_to_text_only_nudge(self):
        with patch.object(subagent_tools, "_COOP_EXPLICIT", False):
            with patch.dict(os.environ, {"HANDY_CODER_COMMIT_SUBAGENT": "1"}):
                results = [self._write(index) for index in range(3)]
        nudge = next(r["commit_nudge"] for r in results if "commit_nudge" in r)
        self.assertIsNone(nudge["task_id"])
        self.assertIn("Review .gitignore", nudge["message"])
        self.assertIsNone(self.manager.open_frame())


if __name__ == "__main__":
    unittest.main()