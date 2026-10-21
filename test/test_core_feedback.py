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
        for module in (apply_edits, code_search, find_files, git_manage, read_files, run_tests):
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
            matches = self.mcp.tools["code_search_ripgrep"]("needle")

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
            result = self.mcp.tools["code_search_ripgrep"](
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

        self.assertEqual(read_result["files"][0]["path"], "sample.txt")
        self.assertEqual(read_result["files"][0]["resolved_path"], str(file_path))
        self.assertEqual(edit_result["edits"][0]["path"], str(file_path))
        self.assertEqual(edit_result["edits"][0]["resolved_path"], str(file_path))
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


if __name__ == "__main__":
    unittest.main()
