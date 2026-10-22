"""Regression tests for Phase 1 security/safety issues and the search truncation bug.

Convention
----------
* Tests that pin down CURRENT, KNOWN-BROKEN behaviour assert the DESIRED behaviour and
  are decorated with ``@unittest.expectedFailure``. The suite stays green today; when a
  fix lands the test reports an "unexpected success" (a failure), which tells you to
  delete the decorator.
* Tests WITHOUT the decorator are controls: behaviour that works today and must not
  regress while the fixes are made.

Sandbox tests are implementation-agnostic: they place a sentinel file OUTSIDE the
workspace and assert its contents never reach the command output. They pass whether the
fix is a stricter lexical guard or real process isolation.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.core import code_search, git_manage, read_files, terminal_exec

SECRET = "TOPSECRET-OUTSIDE-WORKSPACE"


class FakeMCP:
    def __init__(self):
        self.tools = {}
        self.descriptions = {}

    def tool(self, description):
        def register(function):
            self.tools[function.__name__] = function
            self.descriptions[function.__name__] = description
            return function
        return register


class _WorkspaceTestCase(unittest.TestCase):
    """Workspace is <tmp>/workspace; <tmp>/secret.txt lives outside it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self._tmp.name).resolve()
        self.root = self.parent / "workspace"
        self.root.mkdir()
        self.secret = self.parent / "secret.txt"
        self.secret.write_text(SECRET, encoding="utf-8")
        support.configure(str(self.root))

    def tearDown(self):
        self._tmp.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))


# ----------------------------------------------------------------------
# terminal_exec: sandbox escape
# ----------------------------------------------------------------------
class TerminalSandboxTests(_WorkspaceTestCase):
    def _assert_contained(self, command: str) -> None:
        """Pass if the command is rejected OR the outside secret never appears."""
        try:
            result = terminal_exec.terminal_exec(command)
        except ValueError:
            return
        self.assertNotIn(
            SECRET, result["stdout"] + result["stderr"],
            f"sandbox escape: {command!r} read a file outside the workspace",
        )

    # --- controls: already blocked, must stay blocked -------------------
    def test_control_absolute_path_is_blocked(self):
        self._assert_contained(f"cat {self.secret}")

    def test_control_dotdot_slash_path_is_blocked(self):
        self._assert_contained("cat ../secret.txt")

    def test_control_in_workspace_command_still_works(self):
        (self.root / "ok.txt").write_text("inside\n", encoding="utf-8")
        result = terminal_exec.terminal_exec("cat ok.txt")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("inside", result["stdout"])

    def test_control_quoted_comment_slashes_not_rejected(self):
        # Guards against "fixing" the quote carve-out by over-blocking.
        support.validate_shell_command('echo "// changed by script"', self.root)

    def test_control_division_operator_not_rejected(self):
        support.validate_shell_command("echo $((10 / 2))", self.root)

    # --- known escapes ---------------------------------------------------
    def test_cd_dotdot_escapes_workspace(self):
        self._assert_contained("cd .. && cat secret.txt")

    def test_bare_dotdot_argument_escapes_workspace(self):
        self._assert_contained("cat $(ls -d ..)/secret.txt")

    def test_tilde_home_escapes_workspace(self):
        with patch.dict(os.environ, {"HOME": str(self.parent)}):
            self._assert_contained("cd ~ && cat secret.txt")

    def test_double_quoted_dotdot_path_escapes_workspace(self):
        self._assert_contained('cat "../secret.txt"')

    def test_double_quoted_absolute_path_escapes_workspace(self):
        self._assert_contained(f'cat "{self.secret}"')

    def test_single_quoted_absolute_path_escapes_workspace(self):
        self._assert_contained(f"cat '{self.secret}'")

    def test_subshell_sh_c_escapes_workspace(self):
        self._assert_contained(f"sh -c 'cat {self.secret}'")

    def test_unquoted_newline_acts_as_command_separator(self):
        self._assert_contained(f"echo hi\ncat {self.secret}")

    def test_backslash_newline_continuation_cannot_hide_path(self):
        self._assert_contained(f"cat \\\n{self.secret}")

    def test_command_outside_workspace_is_rejected(self):
        with self.assertRaises(ValueError):
            support.validate_shell_command("/usr/bin/env true", self.root)

    def test_control_workspace_relative_launcher_not_rejected(self):
        support.validate_shell_command("./tool --flag", self.root)


# ----------------------------------------------------------------------
# read_files: absolute path leakage in error messages
# ----------------------------------------------------------------------
class ReadFilesPathLeakTests(_WorkspaceTestCase):
    def test_control_missing_file_is_reported_under_relative_path(self):
        result = read_files.read_files(["missing.txt"])
        self.assertEqual(result["files"], [])
        self.assertEqual([e["path"] for e in result["errors"]], ["missing.txt"])

    def test_missing_file_error_does_not_leak_absolute_host_path(self):
        result = read_files.read_files(["missing.txt"])
        message = result["errors"][0]["message"]
        self.assertNotIn(str(self.root), message)
        self.assertNotIn(str(self.parent), message)

    def test_missing_file_error_has_error_code(self):
        result = read_files.read_files(["missing.txt"])
        self.assertEqual(result["errors"][0].get("error_code"), "not_found")


# ----------------------------------------------------------------------
# git_manage: checkout must not silently discard uncommitted edits
# ----------------------------------------------------------------------
@unittest.skipUnless(shutil.which("git"), "Git is not installed")
class GitCheckoutSafetyTests(_WorkspaceTestCase):
    def _git(self, *args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
             "-c", "commit.gpgsign=false", *args],
            cwd=self.root, check=True, capture_output=True,
        )

    def setUp(self):
        super().setUp()
        self._git("init", "--quiet")
        self.tracked = self.root / "tracked.txt"
        self.tracked.write_text("committed\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git("commit", "--quiet", "-m", "initial")
        git_manage.register(FakeMCP())

    def test_control_checkout_branch_still_works(self):
        self._git("branch", "feature")
        result = git_manage.git_manage("checkout", args=["feature"])
        self.assertEqual(result["exit_code"], 0)

    def test_control_checkout_commit_sha_still_works(self):
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        result = git_manage.git_manage("checkout", args=[sha])
        self.assertEqual(result["exit_code"], 0)

    def test_checkout_unknown_ref_returns_not_a_ref(self):
        result = git_manage.git_manage("checkout", args=["no-such-branch"])
        self.assertEqual(result["error_code"], "not_a_ref")
        self.assertNotEqual(result["exit_code"], 0)

    def test_checkout_file_path_does_not_discard_uncommitted_edits(self):
        self.tracked.write_text("precious uncommitted work\n", encoding="utf-8")
        result = git_manage.git_manage("checkout", args=["tracked.txt"])
        self.assertEqual(result.get("error_code"), "not_a_ref")
        self.assertEqual(
            self.tracked.read_text(encoding="utf-8"), "precious uncommitted work\n",
            "git_manage checkout silently reverted a file with uncommitted edits",
        )


# ----------------------------------------------------------------------
# code_search_ripgrep: truncation metadata and search scope
# ----------------------------------------------------------------------
class CodeSearchTruncationTests(_WorkspaceTestCase):
    def test_control_untruncated_result_has_exact_counts(self):
        (self.root / "one.txt").write_text("needle\n", encoding="utf-8")
        result = code_search.code_search_ripgrep("needle")
        self.assertFalse(result["truncated"])
        self.assertEqual(result["total_matches_seen"], 1)
        self.assertEqual(result["omitted_count"], 0)

    def test_control_truncation_flag_and_reason_are_set(self):
        for i in range(5):
            (self.root / f"f{i}.txt").write_text("needle\n", encoding="utf-8")
        result = code_search.code_search_ripgrep("needle", max_results=3)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["truncation_reason"], "max_results")
        self.assertEqual(len(result["matches"]), 3)

    def test_truncation_counts_matches_across_files_exactly(self):
        for i in range(5):
            (self.root / f"f{i}.txt").write_text("needle\n", encoding="utf-8")
        result = code_search.code_search_ripgrep("needle", max_results=3)
        self.assertEqual(result["total_matches_seen"], 5)
        self.assertEqual(result["omitted_count"], 2)

    def test_truncation_counts_matches_within_a_single_file_exactly(self):
        (self.root / "big.txt").write_text("needle\n" * 10, encoding="utf-8")
        result = code_search.code_search_ripgrep("needle", max_results=3)
        self.assertEqual(result["total_matches_seen"], 10)
        self.assertEqual(result["omitted_count"], 7)

    def test_search_skips_generated_directories_by_default(self):
        for rel in ("src/real.py", "node_modules/pkg/index.js", "__pycache__/x.py"):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("needle\n", encoding="utf-8")
        result = code_search.code_search_ripgrep("needle")
        self.assertEqual([m["path"] for m in result["matches"]], ["src/real.py"])


if __name__ == "__main__":
    unittest.main()