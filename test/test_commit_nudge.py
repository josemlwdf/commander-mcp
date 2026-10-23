import subprocess
import tempfile
import unittest
from pathlib import Path

from lib import support
from lib.core import commit_nudge


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


if __name__ == "__main__":
    unittest.main()
