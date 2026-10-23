import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

from lib import support
from lib.core import apply_edits, build_project


class ApplyEditsWrittenFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        support.configure(str(self.root))

    def tearDown(self):
        self.temp.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))

    def edit(self, path, pattern, replacement):
        return apply_edits.Edit(path=path, pattern=pattern, replacement=replacement)

    def test_real_write_lists_written_file(self):
        (self.root / "a.txt").write_text("one\n", encoding="utf-8")
        result = apply_edits.apply_edits([self.edit("a.txt", "one", "two")])
        self.assertEqual(result["written_files"], ["a.txt"])
        self.assertFalse(result["dry_run"])
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "two\n")

    def test_dry_run_writes_nothing_and_lists_nothing(self):
        (self.root / "a.txt").write_text("one\n", encoding="utf-8")
        result = apply_edits.apply_edits([self.edit("a.txt", "one", "two")], dry_run=True)
        self.assertEqual(result["written_files"], [])
        self.assertTrue(result["dry_run"])
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "one\n")

    def test_multiple_files_use_relative_posix_paths(self):
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.txt").write_text("x\n", encoding="utf-8")
        (self.root / "a.txt").write_text("x\n", encoding="utf-8")
        result = apply_edits.apply_edits([
            self.edit("a.txt", "x", "y"),
            self.edit("sub/b.txt", "x", "y"),
            self.edit("a.txt", "y", "z"),
        ])
        self.assertEqual(sorted(result["written_files"]), ["a.txt", "sub/b.txt"])
        self.assertEqual(len(result["written_files"]), 2)

    def test_noop_edit_does_not_count_as_written(self):
        (self.root / "a.txt").write_text("same\n", encoding="utf-8")
        result = apply_edits.apply_edits([self.edit("a.txt", "same", "same")])
        self.assertEqual(result["written_files"], [])

    def test_failed_batch_returns_no_written_files_and_writes_nothing(self):
        (self.root / "a.txt").write_text("one\n", encoding="utf-8")
        result = apply_edits.apply_edits([
            self.edit("a.txt", "one", "two"),
            self.edit("a.txt", "missing", "x"),
        ])
        self.assertEqual(result["error_code"], "no_match")
        self.assertNotIn("written_files", result)
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "one\n")

    def test_files_field_still_only_summarizes_html(self):
        (self.root / "a.txt").write_text("one\n", encoding="utf-8")
        (self.root / "page.html").write_text("<p>old</p>", encoding="utf-8")
        result = apply_edits.apply_edits([
            self.edit("a.txt", "one", "two"),
            self.edit("page.html", "old", "new"),
        ])
        self.assertEqual([item["path"] for item in result["files"]], ["page.html"])
        self.assertEqual(sorted(result["written_files"]), ["a.txt", "page.html"])

    def test_description_documents_written_files(self):
        mcp = FastMCP("t")
        apply_edits.register(mcp)
        description = mcp._tool_manager.get_tool("apply_edits").description
        self.assertIn("written_files", description)
        self.assertIn("dry_run", description)


class BuildProjectRawOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        support.configure(str(self.root))

    def tearDown(self):
        self.temp.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))

    def test_description_documents_raw_output_order(self):
        mcp = FastMCP("t")
        build_project.register(mcp)
        description = mcp._tool_manager.get_tool("build_project").description
        self.assertIn("raw_output is stdout followed by stderr", description)

    def test_raw_output_is_stdout_followed_by_stderr(self):
        (self.root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        fake = {"exit_code": 0, "stdout": "OUT\n", "stderr": "ERR\n", "timed_out": False, "duration_ms": 1}
        with patch("lib.core.build_project._build_command", return_value=(["build"], None)), \
             patch("lib.core.build_project.run_process", return_value=fake):
            result = build_project.build_project(path=".")
        self.assertEqual(result["raw_output"], "OUT\nERR\n")
        self.assertEqual(result["raw_output"], result["stdout"] + result["stderr"])


if __name__ == "__main__":
    unittest.main()