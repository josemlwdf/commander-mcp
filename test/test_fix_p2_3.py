import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from lib import support
from lib.core import git_manage

WORDING = "Path escapes authorized workspace root: "


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=Tester", "-c", "user.email=tester@example.com", *args],
        cwd=root, check=True, capture_output=True, text=True,
    )


@unittest.skipUnless(shutil.which("git"), "git is required")
class GitManageFixP23Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        support.configure(str(self.root))
        _git(self.root, "init", "-q")
        for index in range(5):
            (self.root / f"f{index}.txt").write_text(f"{index}\n", encoding="utf-8")
            _git(self.root, "add", "-A")
            _git(self.root, "commit", "-q", "-m", f"commit {index}")

    def tearDown(self):
        support.configure(str(Path(__file__).resolve().parents[1]))
        self.temp.cleanup()

    def test_log_numeric_shorthand_limits_history(self):
        result = git_manage.git_manage("log", args=["--oneline", "-3"], repo_path=".")
        self.assertNotIn("error_code", result)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(len(result["recent_commits"]), 3)
        self.assertEqual(result["recent_commits"][0]["subject"], "commit 4")

    def test_log_n_flag_still_works(self):
        result = git_manage.git_manage("log", args=["-n", "2"], repo_path=".")
        self.assertEqual(len(result["recent_commits"]), 2)

    def test_log_error_explains_separator_and_shorthand(self):
        with self.assertRaises(ValueError) as ctx:
            git_manage.git_manage("log", args=["--format=%H"], repo_path=".")
        message = str(ctx.exception)
        self.assertIn("--format=%H", message)
        self.assertIn("Allowed", message)
        self.assertIn("-<count>", message)
        self.assertIn("separator", message)

    def test_status_keeps_untracked_files_and_count_only(self):
        (self.root / "new.txt").write_text("x\n", encoding="utf-8")
        result = git_manage.git_manage("status", repo_path=".")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["untracked_files"], ["new.txt"])
        self.assertEqual(result["untracked_summary"], {"count": 1})
        self.assertNotIn("files", result["untracked_summary"])

    def test_path_escape_wording_is_unified(self):
        cases = [
            git_manage.git_manage("status", repo_path="../outside"),
            git_manage.git_manage("status", repo_path=str(self.root.parent)),
            git_manage.git_manage("add", args=["../outside.txt"], repo_path="."),
            git_manage.git_manage("init", repo_path="../outside"),
        ]
        for result in cases:
            self.assertEqual(result["error_code"], "path_escape")
            self.assertTrue(result["message"].startswith(WORDING), result["message"])
            self.assertIn(WORDING + result["path"], json.dumps(result))


if __name__ == "__main__":
    unittest.main()