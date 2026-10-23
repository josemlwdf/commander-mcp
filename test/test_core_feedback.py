import errno
import os
import shutil
import subprocess
import tempfile
import unittest
from typing import get_args, get_type_hints
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.core import apply_edits, code_search, find_files, git_manage, read_files, run_tests
from lib.core import diagnostics, terminal_exec
from lib.extras import web_fetch


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


class CoreFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        support.configure(str(self.root))
        self.mcp = FakeMCP()
        for module in (apply_edits, code_search, find_files, git_manage, read_files, run_tests, diagnostics, terminal_exec):
            module.register(self.mcp)

    def tearDown(self):
        self.temp_dir.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))

    def test_scans_keep_partial_results_and_report_broken_directories(self):
        (self.root / "good.py").write_text("needle\n", encoding="utf-8")
        (self.root / "broken").mkdir()
        original_scandir = os.scandir

        def fail_broken(path):
            if Path(path).name == "broken":
                raise OSError(errno.ENODEV, "No such device", str(path))
            return original_scandir(path)

        with patch("lib.support.os.scandir", side_effect=fail_broken):
            files = self.mcp.tools["find_files"]("*.py")
            matches = self.mcp.tools["search_text"]("needle")

        self.assertEqual(files["files"], ["good.py"])
        self.assertEqual([error["path"] for error in files["errors"]], ["broken"])
        self.assertEqual([match["path"] for match in matches["matches"]], ["good.py"])
        self.assertEqual([error["path"] for error in matches["errors"]], ["broken"])

    def test_search_glob_prunes_unmatched_directory_branches(self):
        target = self.root / "src" / "nested"
        target.mkdir(parents=True)
        (target / "hit.py").write_text("needle\n", encoding="utf-8")
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        (unrelated / "hit.py").write_text("needle\n", encoding="utf-8")

        original_scandir = os.scandir
        visited = []

        def record_scandir(path):
            visited.append(Path(path).relative_to(self.root).as_posix())
            return original_scandir(path)

        with patch("lib.support.os.scandir", side_effect=record_scandir):
            result = self.mcp.tools["search_text"](
                "needle", path_glob="src/**/*.py"
            )

        self.assertEqual([match["path"] for match in result["matches"]], ["src/nested/hit.py"])
        self.assertNotIn("unrelated", visited)

    def test_read_and_edit_responses_include_canonical_paths(self):
        file_path = self.root / "sample.txt"
        file_path.write_text("old value\n", encoding="utf-8")

        read_result = self.mcp.tools["read_files"]([str(file_path)])
        edit_result = self.mcp.tools["apply_edits"]([apply_edits.Edit(
            path=str(file_path), old_str="old", new_str="new"
        )])

        # read_files: relative path is always present; no host-leaking resolved_path.
        self.assertEqual(read_result["files"][0]["path"], "sample.txt")
        self.assertNotIn("resolved_path", read_result["files"][0],
                         "resolved_path must not be exposed (security: leaks host paths)")
        # apply_edits: the path echoed back is the caller-supplied value.
        self.assertEqual(edit_result["edits"][0]["path"], str(file_path))
        self.assertIn("Use relative paths.", self.mcp.descriptions["read_files"])

    def test_generic_pyproject_does_not_trigger_unfiltered_pytest(self):
        (self.root / "pyproject.toml").write_text(
            "[project]\nname = 'sample'\n", encoding="utf-8"
        )

        with patch("lib.core.run_tests.run_process") as run_process:
            result = self.mcp.tools["run_tests"](test_framework="pytest")

        self.assertFalse(result["supported"])
        run_process.assert_not_called()

    def test_filtered_pytest_runs_without_workspace_wide_test_discovery(self):
        (self.root / "pyproject.toml").write_text(
            "[project]\nname = 'sample'\n", encoding="utf-8"
        )
        process_result = {
            "stdout": "", "stderr": "", "exit_code": 0,
            "timed_out": False, "duration_ms": 1,
        }

        with patch("lib.core.run_tests.run_process", return_value=process_result) as run_process:
            result = self.mcp.tools["run_tests"](
                test_framework="pytest", filter_path="test_one.py"
            )

        self.assertTrue(result["supported"])
        run_process.assert_called_once()

    def test_pytest_configuration_enables_unfiltered_test_run(self):
        (self.root / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\naddopts = '-q'\n", encoding="utf-8"
        )
        process_result = {
            "stdout": "", "stderr": "", "exit_code": 0,
            "timed_out": False, "duration_ms": 1,
        }

        with patch("lib.core.run_tests.run_process", return_value=process_result) as run_process:
            result = self.mcp.tools["run_tests"]()

        self.assertTrue(result["supported"])
        self.assertEqual(result["framework"], "pytest")
        run_process.assert_called_once()

    def test_git_unavailable_returns_normalized_error(self):
        with patch("lib.core.git_manage.shutil.which", return_value=None):
            result = self.mcp.tools["git_manage"]("status")

        self.assertFalse(result["supported"])
        self.assertEqual(result["error_code"], "git_unavailable")
        self.assertEqual(result["exit_code"], 127)

    def test_non_repository_git_error_is_normalized(self):
        check_result = {
            "stdout": "",
            "stderr": "fatal: not a git repository (or any parent directory)",
            "exit_code": 128,
        }
        with (
            patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"),
            patch("lib.core.git_manage.run_process", return_value=check_result) as run_process,
        ):
            result = self.mcp.tools["git_manage"]("diff")

        self.assertTrue(result["supported"])
        self.assertEqual(result["error_code"], "not_a_git_repository")
        self.assertNotIn("usage:", result["message"].lower())
        run_process.assert_called_once()

    def test_push_action_runs_git_push_with_valid_args(self):
        check_result = {
            "stdout": "true",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 1,
        }
        push_result = {
            "stdout": "pushed",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 2,
        }
        with (
            patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"),
            patch("lib.core.git_manage.run_process", side_effect=[check_result, push_result]) as run_process,
        ):
            result = self.mcp.tools["git_manage"]("push", args=["origin", "main"])

        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(run_process.call_args_list[1].args[0], ["/usr/bin/git", "push", "origin", "main"])

    def test_push_rejects_unsupported_options(self):
        with self.assertRaisesRegex(ValueError, "Unsupported push option"):
            self.mcp.tools["git_manage"]("push", args=["--force", "origin", "main"])

    def test_git_description_suggests_initialization_when_no_repo_is_found(self):
        self.mcp.tools["git_manage"]("status")
        description = self.mcp.descriptions["git_manage"]

        self.assertIn("No Git repository was found", description)
        self.assertIn("ask whether", description)
        self.assertIn("action='init'", description)

    def test_git_arguments_have_schema_descriptions(self):
        annotations = get_type_hints(
            self.mcp.tools["git_manage"], include_extras=True
        )

        for name in ("action", "args", "repo_path"):
            with self.subTest(argument=name):
                metadata = get_args(annotations[name])[1:]
                descriptions = [
                    item.description
                    for item in metadata
                    if getattr(item, "description", None)
                ]
                self.assertTrue(descriptions, f"{name} must have a schema description")

    def test_git_discovery_is_deferred_until_first_tool_call(self):
        mcp = FakeMCP()
        with patch("lib.core.git_manage.discover_repositories") as discover:
            git_manage.register(mcp)

        discover.assert_not_called()
        self.assertIn("discovery runs when this tool is first called", mcp.descriptions["git_manage"])

    def test_discovery_lists_descendant_repositories_but_stops_at_root_repo(self):
        first = self.root / "projects" / "first"
        second = self.root / "projects" / "second"
        (first / ".git").mkdir(parents=True)
        (second / ".git").mkdir(parents=True)

        repositories, errors = git_manage.discover_repositories()
        self.assertEqual(repositories, ("projects/first", "projects/second"))
        self.assertEqual(errors, ())

        (self.root / ".git").mkdir()
        repositories, errors = git_manage.discover_repositories()
        self.assertEqual(repositories, (".",))
        self.assertEqual(errors, ())

    def test_multiple_repositories_require_a_matching_repo_path(self):
        first = self.root / "first"
        second = self.root / "second"
        (first / ".git").mkdir(parents=True)
        (second / ".git").mkdir(parents=True)
        mcp = FakeMCP()
        git_manage.register(mcp)

        with self.assertRaisesRegex(ValueError, "repo_path is required"):
            mcp.tools["git_manage"]("status")

        self.assertIn("'first'\n- 'second'", mcp.descriptions["git_manage"])
        self.assertIn("For every action, set repo_path", mcp.descriptions["git_manage"])

        result = {
            "stdout": "true",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 1,
        }
        with (
            patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"),
            patch("lib.core.git_manage.run_process", return_value=result) as run_process,
        ):
            mcp.tools["git_manage"]("status", repo_path="second")

        self.assertEqual(run_process.call_args_list[0].kwargs["cwd"], second)
        self.assertEqual(run_process.call_args_list[1].kwargs["cwd"], second)

    def test_tool_description_updates_fastmcp_tool_registry(self):
        tool = type("Tool", (), {"description": "before"})()
        manager = type("ToolManager", (), {"_tools": {"git_manage": tool}})()
        mcp = type("MCP", (), {"_tool_manager": manager})()

        git_manage._update_tool_description(mcp, "after")

        self.assertEqual(manager._tools["git_manage"].description, "after")

    def test_init_creates_root_repo_and_updates_description(self):
        (self.root / "node_modules").mkdir()
        (self.root / "custom").mkdir()
        (self.root / "custom" / "__pycache__").mkdir()
        (self.root / ".gitignore").write_text("# keep me\ncustom-rule\n", encoding="utf-8")
        result = {
            "stdout": "Initialized empty Git repository",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 1,
        }

        def initialize(command, *, cwd):
            (cwd / ".git").mkdir()
            return result

        with (
            patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"),
            patch("lib.core.git_manage.run_process", side_effect=initialize),
        ):
            initialized = self.mcp.tools["git_manage"]("init")

        self.assertEqual(initialized["exit_code"], 0)
        self.assertIn("Git repository available", self.mcp.descriptions["git_manage"])
        self.assertNotIn("ask whether", self.mcp.descriptions["git_manage"])
        self.assertEqual(git_manage._REPOSITORIES, (".",))
        self.assertTrue((self.root / "test").is_dir())
        self.assertTrue((self.root / "tmp").is_dir())
        ignore_text = (self.root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("# keep me\ncustom-rule", ignore_text)
        self.assertIn(".handy-coder/", ignore_text)
        self.assertIn("tmp/", ignore_text)
        self.assertIn("node_modules/", ignore_text)
        self.assertIn("__pycache__/", ignore_text)
        git_manage._maintain_gitignore(self.root)
        self.assertEqual((self.root / ".gitignore").read_text(encoding="utf-8"), ignore_text)

    @unittest.skipUnless(shutil.which("git"), "Git is not installed")
    def test_add_creates_ignore_file_before_staging(self):
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        (self.root / "node_modules").mkdir()
        (self.root / "new.txt").write_text("stage me\n", encoding="utf-8")

        result = self.mcp.tools["git_manage"]("add", args=["new.txt"], repo_path=".")

        self.assertEqual(result["exit_code"], 0, result)
        ignore_text = (self.root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".handy-coder/", ignore_text)
        self.assertIn("tmp/", ignore_text)
        self.assertIn("node_modules/", ignore_text)
        staged = subprocess.run(
            ["git", "ls-files", "--", "new.txt"], cwd=self.root,
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(staged.stdout.strip(), "new.txt")

    def test_init_is_rejected_when_a_repository_is_already_available(self):
        (self.root / "project" / ".git").mkdir(parents=True)
        mcp = FakeMCP()
        git_manage.register(mcp)

        with patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"):
            with self.assertRaisesRegex(ValueError, "already available"):
                mcp.tools["git_manage"]("init")

    @unittest.skipUnless(shutil.which("git"), "Git is not installed")
    def test_git_status_still_runs_inside_a_repository(self):
        subprocess.run(
            ["git", "init", "--quiet", str(self.root)],
            check=True,
            capture_output=True,
        )

        result = self.mcp.tools["git_manage"]("status")

        self.assertNotIn("error_code", result)
        self.assertEqual(result["exit_code"], 0)

    # ------------------------------------------------------------------
    # New hardening tests
    # ------------------------------------------------------------------

    def test_atomic_edit_second_invalid_edit_leaves_first_unapplied(self):
        """A failed validation in a later edit must leave all earlier files unchanged."""
        file_a = self.root / "a.txt"
        file_a.write_text("hello world\n", encoding="utf-8")

        result = self.mcp.tools["apply_edits"]([
            apply_edits.Edit(path="a.txt", old_str="hello", new_str="goodbye"),
            apply_edits.Edit(path="a.txt", old_str="DOES_NOT_EXIST", new_str="x"),
        ])
        self.assertEqual(result.get("error_code"), "no_match")

        # File a.txt must be unchanged because the batch was rejected
        self.assertEqual(file_a.read_text(encoding="utf-8"), "hello world\n")

    def test_read_files_returns_total_lines_and_applied_range(self):
        f = self.root / "lines.txt"
        f.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")

        result = self.mcp.tools["read_files"](
            ["lines.txt"],
            line_ranges=[read_files.LineRange(path="lines.txt", start=2, end=4)],
        )

        entry = result["files"][0]
        self.assertEqual(entry["total_lines"], 5)
        self.assertEqual(entry["applied_start"], 2)
        self.assertEqual(entry["applied_end"], 4)
        self.assertEqual(entry["content"], "two\nthree\nfour")

    def test_read_files_total_lines_without_range(self):
        f = self.root / "simple.txt"
        f.write_text("a\nb\nc\n", encoding="utf-8")

        result = self.mcp.tools["read_files"](["simple.txt"])

        entry = result["files"][0]
        self.assertEqual(entry["total_lines"], 3)
        self.assertNotIn("applied_start", entry)

    def test_code_search_returns_error_code_for_invalid_regex(self):
        result = self.mcp.tools["search_text"]("(unclosed", is_regex=True)

        self.assertEqual(result.get("error_code"), "invalid_regex")
        self.assertIn("matches", result)
        self.assertEqual(result["matches"], [])

    def test_find_files_excludes_generated_dirs_by_default(self):
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "cached.py").write_text("x", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("x", encoding="utf-8")

        result = self.mcp.tools["find_files"]("*.py")

        paths = result["files"]
        self.assertIn("src/main.py", paths)
        self.assertNotIn("__pycache__/cached.py", paths)
        self.assertFalse(result["incomplete"])

    def test_find_files_includes_generated_dirs_when_requested(self):
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "cached.py").write_text("x", encoding="utf-8")

        result = self.mcp.tools["find_files"]("*.py", include_generated=True)

        self.assertIn("__pycache__/cached.py", result["files"])

    def test_run_tests_parses_pytest_output(self):
        (self.root / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\naddopts = '-q'\n", encoding="utf-8"
        )
        process_result = {
            "stdout": "5 passed, 1 failed in 0.3s",
            "stderr": "",
            "exit_code": 1,
            "timed_out": False,
            "duration_ms": 300,
        }

        with patch("lib.core.run_tests.run_process", return_value=process_result):
            result = self.mcp.tools["run_tests"]()

        self.assertIn("parsed", result)
        self.assertEqual(result["parsed"]["passed"], 5)
        self.assertEqual(result["parsed"]["failed"], 1)

    def test_diagnostics_returns_exit_code(self):
        (self.root / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length = 88\n", encoding="utf-8"
        )
        process_result = {
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_ms": 50,
        }

        with patch("lib.support.run_process", return_value=process_result), \
             patch("lib.core.diagnostics.run_process", return_value=process_result):
            result = self.mcp.tools["code_get_diagnostics"]()

        self.assertTrue(result["supported"])
        self.assertIn("exit_code", result)

    def test_web_fetch_returns_http_status_and_truncated(self):
        with patch.object(web_fetch, "_request", return_value=(
            b"<html><title>T</title><p>Hello</p></html>",
            "https://example.com/", "text/html", 200, False,
        )):
            result = web_fetch._fetch("https://example.com/")

        self.assertEqual(result["http_status"], 200)
        self.assertIn("truncated", result)

    # ------------------------------------------------------------------
    # Audit fix tests
    # ------------------------------------------------------------------

    def test_run_tests_parser_failures_only_line(self):
        """'1 failed in 0.13s' must produce parsed.failed=1, parsed.passed=0."""
        result = run_tests._parse_pytest_output("1 failed in 0.13s")
        self.assertIsNotNone(result)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["passed"], 0)

    def test_run_tests_parser_reversed_order(self):
        """'1 failed, 3 passed' (failed before passed) must give correct counts."""
        result = run_tests._parse_pytest_output("1 failed, 3 passed in 0.3s")
        self.assertIsNotNone(result)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["passed"], 3)

    def test_run_tests_parser_no_summary_returns_none(self):
        """Output with no recognisable summary line must return None."""
        self.assertIsNone(run_tests._parse_pytest_output("collected 0 items"))

    def test_js_test_parser_counts_and_failure_details(self):
        output = "Tests: 1 failed, 2 passed, 1 skipped, 4 total\n  ✕ suite › rejects input\n"
        parsed = run_tests._parse_js_test_output(output)
        self.assertEqual((parsed["passed"], parsed["failed"], parsed["skipped"]), (2, 1, 1))
        self.assertEqual(parsed["failures"][0]["test_id"], "suite › rejects input")
        vitest = run_tests._parse_js_test_output("❯ 1 failed | 2 passed | 1 skipped (4)\n❯ src/a.test.js (1)\n")
        self.assertEqual((vitest["passed"], vitest["failed"], vitest["skipped"]), (2, 1, 1))
        self.assertIsNone(run_tests._parse_js_test_output("runner crashed"))

    def test_create_file_octal_permission_strings(self):
        from lib.core import create_file
        self.assertEqual(create_file.create_file("mode.txt", permissions="0755")["created"], True)
        self.assertEqual((self.root / "mode.txt").stat().st_mode & 0o777, 0o755)
        invalid = create_file.create_file("bad-mode.txt", permissions="0899")
        self.assertEqual(invalid["error_code"], "invalid_permissions")

    def test_git_transition_actions_schema_and_clean_guard(self):
        hints = get_type_hints(git_manage.git_manage, include_extras=True)
        self.assertIn("pull", str(hints["action"]))
        self.assertIn("merge", str(hints["action"]))
        self.assertIn("rebase", str(hints["action"]))
        dirty = {"exit_code": 0, "stdout": "?? untracked\n", "stderr": ""}
        with patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"), \
             patch("lib.core.git_manage.run_process", side_effect=[{"exit_code": 0, "stdout": "true\n", "stderr": ""}, dirty]) as run:
            result = git_manage.git_manage("pull")
        self.assertEqual(result["error_code"], "dirty_worktree")
        self.assertEqual(run.call_count, 2)

    def test_git_manage_rediscovers_newly_created_repo(self):
        """A repo created after the first git_manage call must be accepted."""
        # First call populates the cache with an empty repo list.
        with patch("lib.core.git_manage.shutil.which", return_value=None):
            self.mcp.tools["git_manage"]("status")

        # Now create a repo under the workspace.
        repo = self.root / "project"
        (repo / ".git").mkdir(parents=True)

        ok_result = {
            "stdout": "true", "stderr": "", "exit_code": 0,
            "timed_out": False, "duration_ms": 1,
        }
        with (
            patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"),
            patch("lib.core.git_manage.run_process", return_value=ok_result),
        ):
            result = self.mcp.tools["git_manage"]("status", repo_path="project")

        # Should succeed (re-discovered the new repo), not raise ValueError.
        self.assertNotIn("error_code", result)

    def test_git_manage_log_error_names_the_flag(self):
        """Unsupported log option error must name the offending flag."""
        (self.root / ".git").mkdir()
        mcp = FakeMCP()
        git_manage.register(mcp)
        ok_result = {
            "stdout": "true", "stderr": "", "exit_code": 0,
            "timed_out": False, "duration_ms": 1,
        }
        with (
            patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"),
            patch("lib.core.git_manage.run_process", return_value=ok_result),
        ):
            with self.assertRaises(ValueError) as ctx:
                mcp.tools["git_manage"]("log", args=["--format=%H"])
        self.assertIn("--format=%H", str(ctx.exception))
        self.assertIn("Allowed", str(ctx.exception))

    def test_read_files_returns_per_file_errors_for_missing_files(self):
        """A missing file must appear in errors; valid files must still appear in files."""
        good = self.root / "good.txt"
        good.write_text("hello\n", encoding="utf-8")

        result = self.mcp.tools["read_files"](["good.txt", "missing.txt"])

        file_paths = [f["path"] for f in result["files"]]
        error_paths = [e["path"] for e in result["errors"]]
        self.assertIn("good.txt", file_paths)
        self.assertIn("missing.txt", error_paths)

    def test_read_files_all_missing_returns_empty_files_and_non_empty_errors(self):
        """All-missing batch produces files=[] and non-empty errors."""
        result = self.mcp.tools["read_files"](["nope1.txt", "nope2.txt"])
        self.assertEqual(result["files"], [])
        self.assertEqual(len(result["errors"]), 2)

    def test_terminal_exec_division_operator_not_rejected(self):
        """A shell command containing bare '/' (division) must not be rejected."""
        from lib.support import validate_shell_command
        # Should not raise — '/' is the division operator here, not a path.
        validate_shell_command("echo $((10 / 2))", self.root)

    def test_terminal_exec_error_message_names_bad_token(self):
        """The path-guard error message must include the rejected token."""
        from lib.support import validate_shell_command
        with self.assertRaises(ValueError) as ctx:
            validate_shell_command("cat /etc/passwd", self.root)
        self.assertIn("/etc/passwd", str(ctx.exception))

    def test_code_search_truncation_reason_max_results(self):
        """Hitting max_results must set truncation_reason='max_results'."""
        for i in range(5):
            (self.root / f"f{i}.txt").write_text("needle\n", encoding="utf-8")

        result = self.mcp.tools["search_text"]("needle", max_results=3)

        self.assertTrue(result["truncated"])
        self.assertEqual(result.get("truncation_reason"), "max_results")

    def test_code_search_truncation_reason_time_limit(self):
        """A time-limit truncation must set truncation_reason='time_limit'."""
        import time as _time
        (self.root / "f.txt").write_text("needle\n", encoding="utf-8")

        call_count = [0]
        real_monotonic = _time.monotonic

        def fake_monotonic():
            call_count[0] += 1
            # After the first call (start_time) always return a value far in
            # the future so the 15 s guard fires immediately.
            if call_count[0] > 1:
                return real_monotonic() + 100.0
            return real_monotonic()

        with patch("lib.core.code_search.time.monotonic", side_effect=fake_monotonic):
            result = self.mcp.tools["search_text"]("needle")

        self.assertTrue(result["truncated"])
        self.assertEqual(result.get("truncation_reason"), "time_limit")

    def test_find_files_globstar_matches_zero_intermediate_dirs(self):
        """src/**/*.py must match src/foo.py (zero intermediate directories)."""
        src = self.root / "src"
        src.mkdir()
        (src / "foo.py").write_text("x", encoding="utf-8")
        (src / "sub").mkdir()
        (src / "sub" / "bar.py").write_text("x", encoding="utf-8")

        result = self.mcp.tools["find_files"]("src/**/*.py")

        files = result["files"]
        self.assertIn("src/foo.py", files, "zero-dir case not matched")
        self.assertIn("src/sub/bar.py", files, "one-dir case not matched")

    def test_diagnostics_tool_not_installed_error_code(self):
        """When the tool binary cannot start, error_code='tool_not_installed' is returned."""
        (self.root / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length = 88\n", encoding="utf-8"
        )
        process_result = {
            "stdout": "",
            "stderr": "Failed to start process: [Errno 2] No such file or directory: 'ruff'",
            "exit_code": 1,
            "timed_out": False,
            "duration_ms": 1,
        }

        with patch("lib.core.diagnostics.run_process", return_value=process_result):
            result = self.mcp.tools["code_get_diagnostics"]()

        self.assertTrue(result["supported"])
        self.assertEqual(result.get("error_code"), "tool_not_installed")

    # ------------------------------------------------------------------
    # Remediation tests (audit fixes round 2)
    # ------------------------------------------------------------------

    def test_read_files_binary_file_returns_per_file_error_code(self):
        """A non-UTF-8 file must produce error_code='binary_file' and not crash the batch."""
        good = self.root / "good.txt"
        good.write_text("hello\n", encoding="utf-8")
        bad = self.root / "bad.bin"
        bad.write_bytes(b"\xff\xfe binary junk \x00\x01")

        result = self.mcp.tools["read_files"](["good.txt", "bad.bin"])

        file_paths = [f["path"] for f in result["files"]]
        self.assertIn("good.txt", file_paths)
        error_codes = {e["path"]: e.get("error_code") for e in result["errors"]}
        self.assertIn("bad.bin", error_codes)
        self.assertEqual(error_codes["bad.bin"], "binary_file")

    def test_apply_edits_error_includes_edit_index(self):
        """A failed edit must mention the zero-based [edit N] index in its message."""
        f = self.root / "target.txt"
        f.write_text("hello world\n", encoding="utf-8")

        result = self.mcp.tools["apply_edits"]([
            apply_edits.Edit(path="target.txt", old_str="hello", new_str="hi"),
            apply_edits.Edit(path="target.txt", old_str="NOPE", new_str="x"),
        ])
        self.assertIn("[edit 1]", result.get("message", ""))
        self.assertEqual(result.get("edit_index"), 1)

    def test_apply_edits_returns_invalid_regex_error_code(self):
        """An invalid regex must yield a structured error, not an exception, and write nothing."""
        f = self.root / "rx.txt"
        f.write_text("hello world\n", encoding="utf-8")

        result = self.mcp.tools["apply_edits"]([
            apply_edits.Edit(path="rx.txt", old_str="hello", new_str="hi"),
            apply_edits.Edit(path="rx.txt", old_str="(unclosed", new_str="x"),
        ])

        self.assertEqual(result.get("error_code"), "invalid_regex")
        self.assertEqual(result.get("edit_index"), 1)
        self.assertIn("[edit 1]", result.get("message", ""))
        self.assertEqual(result.get("edits"), [])
        self.assertEqual(f.read_text(encoding="utf-8"), "hello world\n")

    def test_apply_edits_returns_no_match_error_code(self):
        """A pattern that matches nothing must return no_match and write nothing."""
        f = self.root / "nm.txt"
        f.write_text("hello world\n", encoding="utf-8")

        result = self.mcp.tools["apply_edits"]([
            apply_edits.Edit(path="nm.txt", old_str="hello", new_str="hi"),
            apply_edits.Edit(path="nm.txt", old_str="NOPE", new_str="x"),
        ])

        self.assertEqual(result.get("error_code"), "no_match")
        self.assertEqual(result.get("edit_index"), 1)
        self.assertIn("[edit 1]", result.get("message", ""))
        self.assertEqual(result.get("edits"), [])
        self.assertEqual(f.read_text(encoding="utf-8"), "hello world\n")

    def test_apply_edits_returns_path_error_codes(self):
        """Bad paths must return structured codes without leaking host paths."""
        (self.root / "adir").mkdir()
        (self.root / "bin.dat").write_bytes(b"\xff\xfe\x00\x80")
        cases = [
            ("missing.txt", "not_found"),
            ("../outside.txt", "path_escape"),
            ("adir", "not_a_file"),
            ("bin.dat", "binary_file"),
        ]
        for path, code in cases:
            with self.subTest(path=path):
                result = self.mcp.tools["apply_edits"](
                    [apply_edits.Edit(path=path, old_str="x", new_str="y")]
                )
                self.assertEqual(result.get("error_code"), code)
                self.assertEqual(result.get("edit_index"), 0)
                self.assertIn("[edit 0]", result.get("message", ""))
                self.assertNotIn(str(self.root), result.get("message", ""))
                self.assertEqual(result.get("edits"), [])

    def test_apply_edits_expand_groups_flag(self):
        """expand_groups=True expands \\1 / \\g<name>; the default stays literal."""
        f = self.root / "grp.txt"
        f.write_text("john smith\n", encoding="utf-8")
        edit = apply_edits.Edit(
            path="grp.txt", old_str=r"(?P<first>\w+) (\w+)", new_str=r"\2, \g<first>"
        )

        literal = self.mcp.tools["apply_edits"]([edit], dry_run=True)
        self.assertIn(r"+\2, \g<first>", literal["edits"][0]["diff"])

        self.mcp.tools["apply_edits"]([edit], expand_groups=True)
        self.assertEqual(f.read_text(encoding="utf-8"), "smith, john\n")

        # A reference to a nonexistent group is a structured error, not an exception.
        f.write_text("john smith\n", encoding="utf-8")
        bad = apply_edits.Edit(path="grp.txt", old_str=r"(\w+)", new_str=r"\5")
        result = self.mcp.tools["apply_edits"]([bad], expand_groups=True)
        self.assertEqual(result.get("error_code"), "invalid_replacement")
        self.assertEqual(result.get("edit_index"), 0)
        self.assertEqual(f.read_text(encoding="utf-8"), "john smith\n")

    def test_apply_edits_returns_match_count(self):
        """Each result entry must include matches with the replacement count."""
        f = self.root / "multi.txt"
        f.write_text("a a a\n", encoding="utf-8")

        result = self.mcp.tools["apply_edits"](
            [apply_edits.Edit(path="multi.txt", old_str="a", new_str="b")],
            replace_all=True,
        )
        self.assertEqual(result["edits"][0]["matches"], 3)

    def test_apply_edits_dry_run_does_not_write(self):
        """dry_run=True must compute diffs but leave the file unchanged."""
        f = self.root / "dry.txt"
        f.write_text("original\n", encoding="utf-8")

        result = self.mcp.tools["apply_edits"](
            [apply_edits.Edit(path="dry.txt", old_str="original", new_str="changed")],
            dry_run=True,
        )
        self.assertTrue(result["dry_run"])
        self.assertIn("diff", result["edits"][0])
        self.assertEqual(f.read_text(encoding="utf-8"), "original\n")

    def test_apply_edits_dry_run_false_writes(self):
        """dry_run=False (default) must write changes to disk."""
        f = self.root / "write.txt"
        f.write_text("before\n", encoding="utf-8")

        self.mcp.tools["apply_edits"](
            [apply_edits.Edit(path="write.txt", old_str="before", new_str="after")],
        )
        self.assertEqual(f.read_text(encoding="utf-8"), "after\n")

    def test_terminal_exec_quoted_slash_not_rejected(self):
        """A token like '// comment' inside quotes must not trigger the path guard."""
        from lib.support import validate_shell_command
        validate_shell_command('echo "// changed by script"', self.root)

    def test_code_search_total_matches_seen_on_truncation(self):
        """When truncated by max_results, total_matches_seen must be populated."""
        for i in range(10):
            (self.root / f"f{i}.txt").write_text("needle\n", encoding="utf-8")

        result = self.mcp.tools["search_text"]("needle", max_results=3)

        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["matches"]), 3)
        self.assertEqual(result["total_matches_seen"], 10)
        self.assertEqual(result["omitted_count"], 7)

    def test_code_search_no_truncation_has_total_matches(self):
        """Non-truncated results must still include total_matches_seen=len(matches)."""
        (self.root / "one.txt").write_text("needle\n", encoding="utf-8")

        result = self.mcp.tools["search_text"]("needle", max_results=100)

        self.assertFalse(result["truncated"])
        self.assertEqual(result["total_matches_seen"], len(result["matches"]))

    def test_diagnostics_ok_field_true_on_success(self):
        """ok must be True when exit_code is 0."""
        (self.root / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length = 88\n", encoding="utf-8"
        )
        process_result = {
            "stdout": "", "stderr": "", "exit_code": 0,
            "timed_out": False, "duration_ms": 10,
        }
        with patch("lib.core.diagnostics.run_process", return_value=process_result):
            result = self.mcp.tools["code_get_diagnostics"]()
        self.assertTrue(result["ok"])

    def test_diagnostics_ok_field_false_on_failure(self):
        """ok must be False when exit_code is non-zero."""
        (self.root / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length = 88\n", encoding="utf-8"
        )
        process_result = {
            "stdout": "E501 line too long", "stderr": "", "exit_code": 1,
            "timed_out": False, "duration_ms": 10,
        }
        with patch("lib.core.diagnostics.run_process", return_value=process_result):
            result = self.mcp.tools["code_get_diagnostics"]()
        self.assertFalse(result["ok"])

    def test_diagnostics_falls_back_to_next_candidate_on_tool_not_installed(self):
        """When the first tool is not installed, the next candidate must be tried."""
        (self.root / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length = 88\n[tool.mypy]\nstrict = true\n",
            encoding="utf-8",
        )
        ruff_missing = {
            "stdout": "", "exit_code": 1,
            "stderr": "Failed to start process: [Errno 2] No such file or directory: 'ruff'",
            "timed_out": False, "duration_ms": 1,
        }
        mypy_ok = {
            "stdout": "Success: no issues found", "stderr": "", "exit_code": 0,
            "timed_out": False, "duration_ms": 50,
        }

        call_results = [ruff_missing, mypy_ok]
        with patch("lib.core.diagnostics.run_process", side_effect=call_results):
            result = self.mcp.tools["code_get_diagnostics"]()

        self.assertTrue(result["supported"])
        self.assertEqual(result["check"], "mypy")
        self.assertTrue(result["ok"])

    def test_diagnostics_malformed_config_returns_invalid_config(self):
        """A malformed config file must yield a structured error, not a decode exception."""
        (self.root / "package.json").write_text("{not json", encoding="utf-8")
        result = self.mcp.tools["code_get_diagnostics"]()
        self.assertEqual(result.get("error_code"), "invalid_config")
        self.assertFalse(result.get("ok"))
        self.assertEqual([e["file"] for e in result["config_errors"]], ["package.json"])

        (self.root / "package.json").unlink()
        (self.root / "pyproject.toml").write_text("[tool.ruff\n", encoding="utf-8")
        result = self.mcp.tools["code_get_diagnostics"]()
        self.assertEqual(result.get("error_code"), "invalid_config")
        self.assertEqual([e["file"] for e in result["config_errors"]], ["pyproject.toml"])
        self.assertNotIn(str(self.root), str(result["config_errors"]))

    def test_diagnostics_reports_skipped_candidates_and_config_errors(self):
        """Fallback history must be visible: skipped tools and unusable config files."""
        (self.root / "package.json").write_text("{not json", encoding="utf-8")
        (self.root / "pyproject.toml").write_text(
            "[tool.ruff]\nline-length = 88\n[tool.mypy]\nstrict = true\n", encoding="utf-8"
        )
        ruff_missing = {
            "stdout": "", "stderr": "Failed to start process: [Errno 2] 'ruff'",
            "exit_code": 1, "timed_out": False, "duration_ms": 1,
        }
        mypy_ok = {
            "stdout": "ok", "stderr": "", "exit_code": 0,
            "timed_out": False, "duration_ms": 5,
        }
        with patch("lib.core.diagnostics.run_process", side_effect=[ruff_missing, mypy_ok]):
            result = self.mcp.tools["code_get_diagnostics"]()
        self.assertEqual(result["check"], "mypy")
        self.assertEqual(
            result["skipped"], [{"check": "ruff", "error_code": "tool_not_installed"}]
        )
        self.assertEqual([e["file"] for e in result["config_errors"]], ["package.json"])

    def test_run_tests_parser_extracts_failures_list(self):
        """FAILED lines must be parsed into parsed.failures with test_id and file."""
        output = (
            "FAILED test/test_foo.py::TestBar::test_baz - AssertionError: 0 != 1\n"
            "1 failed in 0.5s\n"
        )
        result = run_tests._parse_pytest_output(output)
        self.assertIsNotNone(result)
        self.assertIn("failures", result)
        self.assertEqual(len(result["failures"]), 1)
        self.assertEqual(result["failures"][0]["test_id"], "test/test_foo.py::TestBar::test_baz")
        self.assertEqual(result["failures"][0]["file"], "test/test_foo.py")
        self.assertIn("message", result["failures"][0])

    def test_run_tests_parser_no_failures_key_when_all_pass(self):
        """When no FAILED lines are present, failures key must be absent."""
        result = run_tests._parse_pytest_output("5 passed in 0.1s")
        self.assertIsNotNone(result)
        self.assertNotIn("failures", result)

    def test_git_manage_fast_path_skips_discovery_when_git_marker_present(self):
        """With a .git marker and explicit repo_path, no discovery scan should run."""
        repo = self.root / "myrepo"
        (repo / ".git").mkdir(parents=True)
        mcp = FakeMCP()
        git_manage.register(mcp)

        ok_result = {
            "stdout": "true", "stderr": "", "exit_code": 0,
            "timed_out": False, "duration_ms": 1,
        }
        with (
            patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"),
            patch("lib.core.git_manage.run_process", return_value=ok_result),
            patch("lib.core.git_manage.discover_repositories") as discover,
        ):
            mcp.tools["git_manage"]("status", repo_path="myrepo")

        discover.assert_not_called()

    def test_git_manage_init_accepts_repo_path_subdirectory(self):
        """init with repo_path must initialize that subdirectory, not just workspace root."""
        mcp = FakeMCP()
        git_manage.register(mcp)
        sub = self.root / "subproject"
        sub.mkdir()

        def initialize(command, *, cwd):
            (cwd / ".git").mkdir()
            return {"stdout": "Initialized", "stderr": "", "exit_code": 0,
                    "timed_out": False, "duration_ms": 1}

        with (
            patch("lib.core.git_manage.shutil.which", return_value="/usr/bin/git"),
            patch("lib.core.git_manage.run_process", side_effect=initialize),
        ):
            result = mcp.tools["git_manage"]("init", repo_path="subproject")

        self.assertEqual(result["exit_code"], 0)
        self.assertTrue((sub / ".git").exists())


if __name__ == "__main__":
    unittest.main()
