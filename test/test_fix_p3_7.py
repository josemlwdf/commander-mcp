import json
import tempfile
import unittest
from pathlib import Path

from lib import support
from lib.core import (
    apply_edits, build_project, create_file, diagnostics, orchestrator_tasks,
    read_files, run_tests,
)
from lib.extras import code_format_lint

WORDING = "Path escapes authorized workspace root: "


class PathEscapeWordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        support.configure(str(self.root))
        (self.root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    def tearDown(self):
        support.configure(str(Path(__file__).resolve().parents[1]))
        self.temp.cleanup()

    def assert_wording(self, result, path):
        self.assertIn(WORDING + path, json.dumps(result, default=str))

    def test_helper_wording(self):
        self.assertEqual(support.path_escape_message("x"), WORDING + "x")
        self.assertEqual(support.PATH_ESCAPE_MESSAGE + ": x", WORDING + "x")

    def test_inside_raises_canonical_message(self):
        with self.assertRaises(ValueError) as ctx:
            support.inside("../outside.txt")
        self.assertEqual(str(ctx.exception), WORDING + "../outside.txt")

    def test_read_files(self):
        self.assert_wording(read_files.read_files(paths=["../outside.txt"]), "../outside.txt")

    def test_apply_edits(self):
        edit = apply_edits.Edit(path="../outside.txt", pattern="x", replacement="y")
        self.assert_wording(apply_edits.apply_edits(edits=[edit]), "../outside.txt")

    def test_create_file(self):
        result = create_file.create_file(path="../outside.txt", content="x")
        self.assertEqual(result["error_code"], "path_escape")
        self.assert_wording(result, "../outside.txt")

    def test_orchestrator_update_task_status(self):
        result = orchestrator_tasks.orchestrator_update_task_status(
            status="complete", line_number=1, tasks_path="../outside.md",
        )
        self.assertEqual(result["error_code"], "path_escape")
        self.assert_wording(result, "../outside.md")

    def test_diagnostics(self):
        result = diagnostics.code_get_diagnostics(path="../outside")
        self.assertEqual(result["error_code"], "path_escape")
        self.assert_wording(result, "../outside")

    def test_build_project(self):
        result = build_project.build_project(path="../outside")
        self.assertEqual(result["error_code"], "path_escape")
        self.assert_wording(result, "../outside")

    def test_run_tests(self):
        result = run_tests.run_tests(test_framework="pytest", filter_path="../outside")
        self.assertEqual(result["error_code"], "path_escape")
        self.assert_wording(result, "../outside")

    def test_code_format_lint(self):
        result = code_format_lint.code_format_lint(paths=["../outside.py"])
        self.assertEqual(result["error_code"], "invalid_paths")
        self.assert_wording(result, "../outside.py")


if __name__ == "__main__":
    unittest.main()
