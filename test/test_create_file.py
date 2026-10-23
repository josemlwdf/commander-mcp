import os
import tempfile
import unittest
from pathlib import Path

from lib import support
from lib.core import create_file
from lib.core import html_syntax_check


class CreateFileTests(unittest.TestCase):
    def setUp(self):
        self._saved_root = support.ROOT
        self._saved_wd = support.WORKING_DIRECTORY
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        support.configure(self._tmp.name)
        self.root = Path(support.ROOT)

    def tearDown(self):
        support.ROOT = self._saved_root
        support.WORKING_DIRECTORY = self._saved_wd

    def test_creates_file_with_content(self):
        result = create_file.create_file("hello.txt", "a\nb\n")
        self.assertTrue(result["created"])
        self.assertFalse(result["overwritten"])
        self.assertEqual(result["path"], "hello.txt")
        self.assertEqual(result["bytes"], 4)
        self.assertEqual(result["lines"], 2)
        self.assertEqual((self.root / "hello.txt").read_bytes(), b"a\nb\n")

    def test_creates_empty_file_by_default(self):
        result = create_file.create_file("empty.txt")
        self.assertTrue(result["created"])
        self.assertEqual((self.root / "empty.txt").read_text(), "")

    def test_creates_parent_directories(self):
        result = create_file.create_file("pkg/sub/mod.py", "x = 1\n")
        self.assertTrue(result["created"])
        written = (self.root / "pkg/sub/mod.py").read_text()
        self.assertEqual(written, "x = 1\n")
        self.assertEqual(result["bytes"], len(written.encode("utf-8")))
        self.assertEqual(result["lines"], len(written.splitlines()))

    def test_parent_missing_when_create_parents_false(self):
        result = create_file.create_file("nope/file.txt", "x", create_parents=False)
        self.assertEqual(result["error_code"], "parent_missing")
        self.assertFalse((self.root / "nope").exists())

    def test_existing_file_is_not_overwritten_by_default(self):
        (self.root / "keep.txt").write_text("original")
        result = create_file.create_file("keep.txt", "new")
        self.assertEqual(result["error_code"], "already_exists")
        self.assertFalse(result["created"])
        self.assertEqual((self.root / "keep.txt").read_text(), "original")

    def test_overwrite_replaces_existing_file(self):
        (self.root / "swap.txt").write_text("original")
        result = create_file.create_file("swap.txt", "new", overwrite=True)
        self.assertFalse(result["created"])
        self.assertTrue(result["overwritten"])
        self.assertEqual((self.root / "swap.txt").read_text(), "new")

    def test_path_escape_is_rejected(self):
        for path in ("../outside.txt", "/etc/handy-coder_escape.txt"):
            with self.subTest(path=path):
                result = create_file.create_file(path, "x")
                self.assertEqual(result["error_code"], "path_escape")

    def test_symlink_escape_is_rejected(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        try:
            os.symlink(outside.name, self.root / "link")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported")
        result = create_file.create_file("link/evil.txt", "x")
        self.assertEqual(result["error_code"], "path_escape")
        self.assertFalse((Path(outside.name) / "evil.txt").exists())

    def test_directory_target_is_rejected(self):
        (self.root / "adir").mkdir()
        result = create_file.create_file("adir", "x", overwrite=True)
        self.assertEqual(result["error_code"], "not_a_file")

    def test_workspace_root_is_rejected(self):
        result = create_file.create_file(".", "x", overwrite=True)
        self.assertEqual(result["error_code"], "not_a_file")

    def test_parent_that_is_a_file_is_rejected(self):
        (self.root / "afile").write_text("x")
        result = create_file.create_file("afile/child.txt", "x")
        self.assertIn(result["error_code"], {"not_a_directory", "io_error"})

    def test_oversized_content_is_rejected(self):
        result = create_file.create_file("big.txt", "x" * (support.MAX_READ_BYTES + 1))
        self.assertEqual(result["error_code"], "file_too_large")
        self.assertFalse((self.root / "big.txt").exists())

    def test_newlines_are_written_verbatim(self):
        create_file.create_file("crlf.txt", "a\r\nb\r\n")
        self.assertEqual((self.root / "crlf.txt").read_bytes(), b"a\r\nb\r\n")

    def test_html_write_preserves_content_checks_final_saved_content_and_creates_no_asset_folders(self):
        result = create_file.create_file("index.html", "<html><body><img src='x'></body></html>")
        saved = (self.root / "index.html").read_text(encoding="utf-8")
        self.assertEqual(saved, "<html><body><img src='x'></body></html>")
        self.assertEqual(result["bytes"], len(saved.encode("utf-8")))
        self.assertTrue(result["syntax_check"]["ok"])
        self.assertFalse((self.root / "static").exists())

    def test_html_overwrite_does_not_create_root_asset_folder(self):
        (self.root / "static/html").mkdir(parents=True)
        (self.root / "static/html/keep.html").write_text("keep")
        (self.root / "page.htm").write_text("old")
        result = create_file.create_file("page.htm", "<p>new</p>", overwrite=True)
        self.assertTrue(result["overwritten"])
        self.assertTrue((self.root / "static/html/keep.html").exists())
        self.assertTrue(result["syntax_check"]["ok"])

    def test_non_html_result_shape_unchanged(self):
        result = create_file.create_file("plain.txt", "ok")
        self.assertNotIn("syntax_check", result)
        self.assertNotIn("asset_folders", result)

    def test_html_formatter_failure_keeps_write_and_reports_status(self):
        from unittest.mock import patch

        with patch("lib.utils.html_prettify.prettify_html", return_value=("<p>x</p>", {
            "ok": False, "changed": False, "error": "formatter failed",
        })):
            result = create_file.create_file("failure.html", "<p>x</p>", format_content=True)
        self.assertEqual((self.root / "failure.html").read_text(encoding="utf-8"), "<p>x</p>")
        self.assertFalse(result["formatter"]["ok"])

    def test_html_checker_structural_cases_and_lines(self):
        cases = (("<div><span>x</span></div>", True), ("<img><br><input>", True),
                 ("<ul><li>a<li>b</ul>", True), ("<div>\n<span>", False),
                 ("</div>", False), ("<div", False), ("</div", False), ("<!-- no end", False))
        for html, expected in cases:
            with self.subTest(html=html):
                self.assertEqual(html_syntax_check.check_html(html)["ok"], expected)
        finding = html_syntax_check.check_html("<div>\n</span>")["findings"][0]
        self.assertEqual(finding["line"], 2)

    def test_html_syntax_check_uses_final_saved_content_after_opt_in_formatter(self):
        from unittest.mock import patch
        submitted = "<div></div>"
        with patch("lib.utils.html_prettify.prettify_html", return_value=("<div>", {"ok": True, "changed": True})):
            result = create_file.create_file("broken.html", submitted, format_content=True)
        self.assertFalse(result["syntax_check"]["ok"])
        self.assertEqual((self.root / "broken.html").read_text(encoding="utf-8"), "<div>")
        self.assertEqual(result["bytes"], len((self.root / "broken.html").read_bytes()))

    def test_python_processing_and_formatting_are_opt_in(self):
        from unittest.mock import patch
        with patch("lib.utils.python_postprocess.process_python_file") as process, \
             patch("lib.extras.code_format_lint.code_format_lint") as formatter:
            create_file.create_file("plain.py", "x=1\n")
            process.assert_not_called()
            formatter.assert_not_called()
            create_file.create_file("opt.py", "x=1\n", post_process_python=True, format_content=True)
            process.assert_called_once()
            formatter.assert_called_once()


if __name__ == "__main__":
    unittest.main()
