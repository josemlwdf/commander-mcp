import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.core import commit_nudge
from lib.core import read_files
from lib.subagents import loop as subagent_loop


class CommitNudgeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        support.configure(str(self.root))
        subprocess.run(["git", "init", "--quiet"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.root, check=True)
        (self.root / "a.txt").write_text("1\n2\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=self.root, check=True)
        commit_nudge._reset_for_tests()

    def tearDown(self):
        self.temp_dir.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))
        commit_nudge._reset_for_tests()

    def test_measure_diff_counts_tracked_and_untracked_lines(self):
        (self.root / "a.txt").write_text("1\n2\n3\n4\n", encoding="utf-8")
        (self.root / "b.txt").write_text("x\ny\nz\n", encoding="utf-8")

        metrics = commit_nudge.measure_diff()

        self.assertTrue(metrics.git_available)
        self.assertEqual(metrics.lines, 5)
        self.assertEqual(metrics.files, 2)
        self.assertIn("a.txt", metrics.changed_files)
        self.assertIn("b.txt", metrics.changed_files)

    def test_preexisting_deleted_files_are_excluded_from_nudge_metrics(self):
        (self.root / "preexisting-delete.txt").write_text("already removed\n")
        subprocess.run(["git", "add", "preexisting-delete.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "add file", "--quiet"], cwd=self.root, check=True)
        (self.root / "preexisting-delete.txt").unlink()
        commit_nudge._reset_for_tests()
        commit_nudge._ensure_cycle_initialized(commit_nudge.STATE)
        metrics = commit_nudge.measure_diff(exclude_paths=commit_nudge.STATE.baseline_deleted_files)
        self.assertNotIn("preexisting-delete.txt", metrics.changed_files)
        self.assertEqual(metrics.lines, 0)

    def test_soft_nudge_added_after_threshold(self):
        lines = "\n".join(f"line {i}" for i in range(90)) + "\n"
        (self.root / "a.txt").write_text(lines, encoding="utf-8")

        result = commit_nudge.after_tool_call(
            "apply_edits",
            {"edits": [{"path": "a.txt"}]},
            {"edits": [{"path": "a.txt", "diff": ""}], "dry_run": False},
        )

        self.assertIn("commit_nudge", result)
        self.assertEqual(result["commit_nudge"]["level"], "soft")
        self.assertGreaterEqual(result["commit_nudge"]["lines"], 80)
        self.assertIn("Review .gitignore", result["commit_nudge"]["message"])
        nudge = commit_nudge._format_nudge(
            "soft", "threshold", commit_nudge.measure_diff(), commit_nudge.STATE
        )
        self.assertIn("Review .gitignore", nudge)

    def test_dry_run_does_not_advance_tracking_or_attach_nudge(self):
        lines = "\n".join(f"line {i}" for i in range(90)) + "\n"
        (self.root / "a.txt").write_text(lines, encoding="utf-8")
        args = {"edits": [{"path": "a.txt"}], "dry_run": True}
        result = commit_nudge.after_tool_call(
            "apply_edits", args, {"edits": [{"path": "a.txt", "diff": "large"}], "dry_run": True}
        )
        self.assertNotIn("commit_nudge", result)
        self.assertEqual(commit_nudge.STATE.write_calls, 0)
        self.assertEqual(commit_nudge.STATE.payload_chars, 0)

        written = commit_nudge.after_tool_call(
            "apply_edits", {"edits": [{"path": "a.txt"}]},
            {"edits": [{"path": "a.txt", "diff": "large"}], "dry_run": False},
        )
        self.assertEqual(commit_nudge.STATE.write_calls, 1)
        self.assertIn("commit_nudge", written)

    def test_parent_message_is_not_modified_by_nudge(self):
        lines = "\n".join(f"line {i}" for i in range(90)) + "\n"
        (self.root / "a.txt").write_text(lines, encoding="utf-8")

        result = commit_nudge.after_tool_call(
            "apply_edits",
            {"edits": [{"path": "a.txt"}]},
            {"edits": [{"path": "a.txt", "diff": ""}], "dry_run": False, "message": "edits applied"},
        )

        self.assertIn("commit_nudge", result)
        self.assertEqual(result["message"], "edits applied")
        self.assertNotIn("[commit-nudge", result["message"])

    def test_nudge_text_is_policy_plus_state_for_every_reason(self):
        metrics = commit_nudge.DiffMetrics(
            lines=120, files=3, changed_files=["a.txt", "b.txt", "c.txt"], git_available=True
        )
        cases = (
            ("soft", "threshold", "[commit-nudge]"),
            ("soft", "switch", "[commit-nudge]"),
            ("soft", "green", "[commit-nudge]"),
            ("firm", "firm", "[commit-nudge: firm]"),
        )
        for level, reason, prefix in cases:
            with self.subTest(level=level, reason=reason):
                text = commit_nudge._format_nudge(level, reason, metrics, commit_nudge.STATE)
                self.assertTrue(text.startswith(prefix), text)
                self.assertIn("Checkpoint commit policy", text)
                self.assertIn("git_manage", text)
                self.assertIn("defer_commit", text)
                self.assertIn("120 lines", text)
                self.assertIn("3 files", text)
                self.assertIn("Review .gitignore", text)
                self.assertIn("Further tool calls, including reads, are blocked", text)
                self.assertIn("once per commit cycle", text)
                self.assertNotIn("Please call", text)

    def _trigger_nudge(self):
        (self.root / "a.txt").write_text(
            "\n".join(f"line {index}" for index in range(90)) + "\n",
            encoding="utf-8",
        )
        result = commit_nudge.after_tool_call(
            "apply_edits",
            {"edits": [{"path": "a.txt"}]},
            {"edits": [{"path": "a.txt", "diff": ""}], "dry_run": False},
        )
        self.assertIn("commit_nudge", result)

    def test_nudge_blocks_every_non_recovery_tool_call(self):
        self._trigger_nudge()

        for tool_name, args in (
            ("read_files", {"paths": ["a.txt"]}),
            ("run_tests", {}),
            ("git_manage", {"action": "status"}),
        ):
            with self.subTest(tool=tool_name):
                blocked = commit_nudge.before_tool_call(tool_name, args)
                self.assertEqual(blocked["error_code"], "commit_gate_blocked")
                self.assertIn("successful", blocked["message"])
                self.assertIn("defer_commit", blocked["message"])

        self.assertIsNone(commit_nudge.before_tool_call("git_manage", {"action": "commit"}))
        self.assertIsNone(commit_nudge.before_tool_call("defer_commit", {"action": "defer"}))

    def test_deferral_reopens_tools_for_bounded_writes_then_blocks_again(self):
        self._trigger_nudge()
        deferred = subagent_loop._call_tool(
            commit_nudge.defer_commit,
            {"reason": "finish this small refactor", "calls": 2},
            tool_name="defer_commit",
        )
        self.assertEqual(deferred["deferred_calls"], 2)
        self.assertIsNone(commit_nudge.before_tool_call("read_files", {"paths": ["a.txt"]}))

        for index in range(2):
            commit_nudge.after_tool_call(
                "create_file",
                {"path": f"deferred-{index}.txt"},
                {"created": True},
            )

        self.assertEqual(commit_nudge.STATE.defer_remaining_calls, 0)
        blocked = commit_nudge.before_tool_call("read_files", {"paths": ["a.txt"]})
        self.assertEqual(blocked["error_code"], "commit_gate_blocked")
        repeated = commit_nudge.defer_commit(
            reason="another attempt",
            calls=2,
        )
        self.assertEqual(repeated["error_code"], "defer_already_used")

    def test_failed_commit_keeps_gate_and_successful_commit_clears_it(self):
        self._trigger_nudge()

        commit_nudge.after_tool_call(
            "git_manage",
            {"action": "commit"},
            {"exit_code": 1},
        )
        self.assertTrue(commit_nudge.STATE.commit_required)

        commit_nudge.after_tool_call(
            "git_manage",
            {"action": "commit"},
            {"exit_code": 0},
        )
        self.assertFalse(commit_nudge.STATE.commit_required)
        self.assertIsNone(commit_nudge.before_tool_call("read_files", {"paths": ["a.txt"]}))

    def test_loop_backend_uses_the_same_commit_gate(self):
        self._trigger_nudge()
        result = subagent_loop._call_tool(
            read_files.read_files,
            {"paths": ["a.txt"]},
            tool_name="read_files",
        )
        self.assertEqual(result["error_code"], "commit_gate_blocked")

    def test_enforcement_off_bypasses_an_active_commit_gate(self):
        self._trigger_nudge()
        with patch.object(commit_nudge.CONFIG, "enforcement", "off"):
            self.assertIsNone(
                commit_nudge.before_tool_call("read_files", {"paths": ["a.txt"]})
            )


if __name__ == "__main__":
    unittest.main()
