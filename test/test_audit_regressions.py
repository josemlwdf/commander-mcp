import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lib import code_index, support, tool_details, worker_supervisor
from lib.core import apply_edits, build_project, create_file, diagnostics, find_files, git_manage, orchestrator_tasks, read_files, run_tests, skills, system_info, web_search_tool
from lib.extras import code_format_lint, web_fetch
from lib.subagents import tools as subagent_tools


class AuditRegressionTests(unittest.TestCase):
    def setUp(self):
        self.old_root = support.ROOT
        self.temp = tempfile.TemporaryDirectory()
        support.configure(self.temp.name)
        self.root = Path(self.temp.name)

    def tearDown(self):
        support.ROOT = self.old_root
        self.temp.cleanup()

    def test_read_files_accepts_canonical_and_legacy_range_keys(self):
        (self.root / "lines.txt").write_text("one\ntwo\nthree\nfour\n")
        for keys in ({"start_line": 2, "end_line": 3}, {"start": 2, "end": 3}):
            result = read_files.read_files(["lines.txt"], line_ranges=[{"path": "lines.txt", **keys}])
            self.assertEqual(result["files"][0]["content"], "two\nthree")

    def test_shell_redirection_guard_ignores_quoted_greater_than(self):
        support.validate_shell_command("printf 'x > y'", self.root)
        with self.assertRaisesRegex(ValueError, "Redirection target escapes"):
            support.validate_shell_command("printf x > /tmp/escape.txt", self.root)

    def test_missing_diagnostic_and_lint_paths_are_structured(self):
        diag = diagnostics.code_get_diagnostics("missing.py")
        lint = code_format_lint.code_format_lint(["missing.py"])
        self.assertEqual(diag["error_code"], "not_found")
        self.assertEqual(lint["error_code"], "invalid_paths")
        self.assertEqual(lint["errors"][0]["error_code"], "not_found")
        self.assertNotIn(str(self.root), diag["message"])

    def test_missing_test_filter_returns_not_found_without_running(self):
        with patch("lib.core.run_tests.run_process") as run:
            result = run_tests.run_tests(test_framework="pytest", filter_path="missing_test.py")
        self.assertEqual(result["error_code"], "not_found")
        run.assert_not_called()

    def test_build_project_missing_selected_configuration_is_structured(self):
        result = build_project.build_project(path=".")
        self.assertEqual(result["error_code"], "project_not_found")
        self.assertFalse(result["success"])

    def test_task_checkbox_tracks_status(self):
        state_dir = self.root / ".handy-coder"
        state_dir.mkdir(exist_ok=True)
        tasks = state_dir / "tasks.md"
        tasks.write_text("- [ ] [status: non_started] task\n")
        complete = orchestrator_tasks.orchestrator_update_task_status("complete", line_number=1)
        self.assertIn("- [x] [status: complete]", tasks.read_text())
        incomplete = orchestrator_tasks.orchestrator_update_task_status("failed", line_number=1)
        self.assertIn("- [ ] [status: failed]", tasks.read_text())
        self.assertTrue(complete["updated"] and incomplete["updated"])

    def test_capabilities_follow_registered_tool_names_and_correct_env_note(self):
        previous_mcp = system_info._MCP
        self.addCleanup(setattr, system_info, "_MCP", previous_mcp)
        system_info._MCP = SimpleNamespace(_tool_manager=SimpleNamespace(_tools={
            "get_capabilities": object(), "get_tool_details": object(), "index_codebase": object(),
            "skills_manage": object(),
        }))
        result = system_info.get_capabilities()
        self.assertEqual(set(result["tools"]), {"get_capabilities", "get_tool_details", "index_codebase", "skills_manage"})
        self.assertIn("HANDY_CODER_SKILLS_DIR", result["tools"]["skills_manage"]["note"])

    def test_get_tool_details_smoke_lookup(self):
        class RuntimeTool:
            def __init__(self, function, description):
                self.fn = function
                self.description = description
                self.parameters = {"properties": {"line_ranges": {"type": "array"}}}

        class FakeMCP:
            def __init__(self):
                self._tool_manager = SimpleNamespace(_tools={})

            def tool(self, *, name=None, description=None):
                def register(function):
                    tool_name = name or function.__name__
                    self._tool_manager._tools[tool_name] = RuntimeTool(function, description or "")
                    return function
                return register

        mcp = FakeMCP()
        tool_details.install(mcp)
        read_files.register(mcp)
        worker_supervisor.supervise_fastmcp_tools(mcp)
        handler = mcp._tool_manager._tools["get_tool_details"].fn
        with patch("lib.worker_supervisor.run_tool_in_worker") as run_worker:
            details = handler(tool_name="read_files")
        run_worker.assert_not_called()
        self.assertEqual(details["tool_name"], "read_files")
        self.assertIn("line_ranges", details["arguments"])

    def test_code_navigator_deduplicates_call_sites_and_hides_database_ids(self):
        con = code_index._db()
        with con:
            file_id = con.execute(
                "INSERT INTO files(path,sha256,size,mtime,indexed_at) VALUES(?,?,?,?,?)",
                ("A.cs", "digest", 1, 1.0, 1.0),
            ).lastrowid
            for kind in ("method_call", "member_access"):
                con.execute(
                    "INSERT INTO references_(file_id,target,line,kind,snippet,enclosing_type,enclosing_method) VALUES(?,?,?,?,?,?,?)",
                    (file_id, "Add", 8, kind, "c.Add(1, 2)", "C", "Run"),
                )
        result = code_index.code_navigator("find_references", symbol="Add")
        self.assertEqual(len(result["references"]), 1)
        self.assertNotIn("id", result["references"][0])
        self.assertNotIn("file_id", result["references"][0])
        con.close()

    def test_code_navigator_errors_use_error_code(self):
        missing = code_index.code_navigator("search_code", query=" ")
        self.assertEqual(missing["error_code"], "missing_parameter")
        self.assertNotIn("error", missing)

    def test_empty_code_index_returns_indexing_hint_without_score(self):
        result = code_index.code_navigator("search_code", query="Widget")
        self.assertEqual(result["results"], [])
        self.assertIn("run index_codebase", result["hint"])

    def test_git_status_does_not_label_untracked_as_staged(self):
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        (self.root / "new.txt").write_text("new\n")
        git_manage._REPOSITORIES = None
        result = git_manage.git_manage("status")
        self.assertEqual(result["staged"], [])
        self.assertEqual(result["untracked_files"], ["new.txt"])

    def test_git_recent_commits_use_requested_limit(self):
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "audit@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Audit"], check=True)
        for name in ("first", "second", "third"):
            (self.root / "file.txt").write_text(name)
            subprocess.run(["git", "-C", str(self.root), "add", "file.txt"], check=True)
            subprocess.run(["git", "-C", str(self.root), "commit", "--quiet", "-m", name], check=True)
        git_manage._REPOSITORIES = None
        result = git_manage.git_manage("log", args=["-n", "1", "--oneline"])
        self.assertEqual(len(result["recent_commits"]), 1)
        self.assertIn("third", result["recent_commits"][0]["subject"])

    def test_find_files_type_file_and_invalid_value(self):
        (self.root / "a.py").write_text("x")
        (self.root / "sub").mkdir()
        result = find_files.find_files("*", file_type="file")
        self.assertEqual(result["files"], ["a.py"])
        with self.assertRaisesRegex(ValueError, "file_type"):
            find_files.find_files("*", file_type="folder")
        with self.assertRaisesRegex(ValueError, "file_type"):
            find_files.find_files("*", file_type="directory")

    def test_apply_edits_conditional_dependency_hint_and_ignored_limit_warning(self):
        (self.root / "a.py").write_text("value = 1\n")
        plain = apply_edits.apply_edits([apply_edits.Edit(path="a.py", pattern="1", replacement="2")])
        self.assertNotIn("dependency_hint", plain)
        limited = apply_edits.apply_edits([apply_edits.Edit(path="a.py", pattern="2", replacement="3")], max_replacements=1)
        self.assertIn("warning", limited)
        imported = apply_edits.apply_edits([apply_edits.Edit(path="a.py", pattern="value", replacement="import os\nvalue")])
        self.assertIn("dependency_hint", imported)
        invalid = apply_edits.apply_edits([apply_edits.Edit(path="a.py", pattern=".", replacement="x", position={"line": 90, "col": 1})])
        self.assertEqual(invalid["error_code"], "invalid_range")

    def test_skills_save_and_confirmed_delete_in_temp_directory(self):
        import os
        with patch.dict(os.environ, {skills.SKILLS_DIR_ENV: str(self.root / "skills")}):
            saved = skills.skills_manage(action="save", name="regression", description="A test skill", content="Body")
            self.assertTrue(saved["saved"])
            pending = skills.skills_manage(action="delete", name="regression")
            self.assertEqual(pending["error_code"], "confirmation_required")
            deleted = skills.skills_manage(action="delete", name="regression", confirm=True)
            self.assertTrue(deleted["deleted"])
            self.assertTrue((self.root / "skills/regression.md.bak").is_file())
            self.assertEqual(skills.skills_manage(action="delete", name="plan", confirm=True)["error_code"], "protected_skill")

    def test_build_project_success_with_mocked_process(self):
        (self.root / "package.json").write_text('{"scripts":{"build":"rollup"}}')
        with patch("lib.core.build_project._build_command", return_value=(["fake-build"], None)), \
             patch("lib.core.build_project.run_process", return_value={"exit_code": 0, "stdout": "ok", "stderr": "", "duration_ms": 1}) as process:
            result = build_project.build_project(path=".")
        self.assertTrue(result["success"])
        process.assert_called_once()

    def test_structured_escape_and_diagnostic_unknown_check(self):
        self.assertEqual(build_project.build_project(path="../outside")["error_code"], "path_escape")
        (self.root / "pyproject.toml").write_text("[tool.ruff]\nline-length=88\n")
        result = diagnostics.code_get_diagnostics(check="madeup")
        self.assertEqual(result["error_code"], "check_not_found")
        self.assertEqual(result["available_checks"], ["ruff"])
        self.assertTrue(result["detection_trace"])
        lint = code_format_lint.code_format_lint(["bad.py", "../escape.py"])
        self.assertEqual(len(lint["errors"]), 2)
        self.assertTrue(lint["supported"])

    def test_web_fetch_reports_offset_and_ssrf_errors(self):
        with patch("lib.extras.web_fetch._request", return_value=(b"plain", "https://example.test", "text/plain", 200, False)):
            offset = web_fetch.web_fetch_docs("https://example.test", offset=8)
        self.assertEqual(offset["error_code"], "invalid_range")
        with patch("lib.extras.web_fetch._public_http_url", side_effect=ValueError("Requests to local, private, or reserved network addresses are not allowed")):
            blocked = web_fetch.web_fetch_docs("http://127.0.0.1")
        self.assertEqual(blocked["error_code"], "ssrf_blocked")

    def test_web_search_omits_internal_optimized_query(self):
        with patch.object(web_search_tool, "_fetch", return_value="page"), \
             patch.object(web_search_tool, "parse_results", return_value=[{"title": "t", "snippet": "s", "link": "https://example.test"}]):
            results = web_search_tool.search("question")
        self.assertNotIn("_optimized_query", results[0])

    def test_git_diff_is_returned_once_and_escaped_paths_are_structured(self):
        (self.root / ".git").mkdir()
        git_manage._REPOSITORIES = None
        calls = [
            {"exit_code": 0, "stdout": "true", "stderr": ""},
            {"exit_code": 0, "stdout": "staged", "stderr": ""},
            {"exit_code": 0, "stdout": "unstaged", "stderr": ""},
        ]
        with patch("lib.core.git_manage.shutil.which", return_value="git"), \
             patch("lib.core.git_manage.run_process", side_effect=calls):
            diff = git_manage.git_manage("diff", repo_path=".", mode="both")
        self.assertEqual(diff["stdout"], "stagedunstaged")
        self.assertNotIn("raw_diff", diff)
        self.assertNotIn("staged_diff", diff)
        self.assertNotIn("unstaged_diff", diff)

        with patch("lib.core.git_manage.shutil.which", return_value="git"), \
             patch("lib.core.git_manage.run_process", return_value={"exit_code": 0, "stdout": "true", "stderr": ""}):
            escaped = git_manage.git_manage("diff", repo_path=".", file_paths=["../escape.txt"])
        self.assertEqual(escaped["error_code"], "path_escape")

    def test_subagent_complete_requires_active_cooperative_task(self):
        manager = subagent_tools.SubagentManager()
        with patch.object(subagent_tools, "_MANAGER", manager):
            unknown = subagent_tools.subagent_complete("missing", "result")
            self.assertEqual(unknown["error_code"], "unknown_task_id")
            self.assertEqual(subagent_tools.subagent_status("missing")["error_code"], "unknown_task_id")
            self.assertEqual(subagent_tools.subagent_result("missing")["error_code"], "unknown_task_id")
            self.assertEqual(subagent_tools.subagent_cancel("missing")["error_code"], "unknown_task_id")
            task = manager.create_task("generalist", "do work", "cooperative", None, None)
            inactive = subagent_tools.subagent_complete(task.task_id, "result")
            self.assertEqual(inactive["error_code"], "task_not_active")
            role = subagent_tools.get_role("generalist")
            manager.mark_delegated(task.task_id, manager.make_frame(task.task_id, role, "goal"))
            completed = subagent_tools.subagent_complete(task.task_id, "Done.")
            self.assertEqual(completed["state"], "done")
            invalid_page = subagent_tools.subagent_result(task.task_id, offset=100)
            self.assertEqual(invalid_page["error_code"], "invalid_range")


if __name__ == "__main__":
    unittest.main()
