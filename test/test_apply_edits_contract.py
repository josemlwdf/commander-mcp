import inspect
import tempfile
import unittest
from pathlib import Path

from lib import support
from lib.core import apply_edits


class ApplyEditsContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        support.configure(str(self.root))

    def tearDown(self):
        self.temp_dir.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))

    def test_sequential_edits_in_same_file_use_staged_content(self):
        target = self.root / "sample.txt"
        target.write_text("foo\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [
                apply_edits.Edit(path="sample.txt", pattern="foo", replacement="bar"),
                apply_edits.Edit(path="sample.txt", pattern="bar", replacement="baz"),
            ]
        )

        self.assertNotIn("error_code", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "baz\n")

    def test_overlapping_ordered_edits_fail_atomically(self):
        target = self.root / "overlap.txt"
        target.write_text("abc\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [
                apply_edits.Edit(path="overlap.txt", pattern="abc", replacement="x"),
                apply_edits.Edit(path="overlap.txt", pattern="ab", replacement="y"),
            ]
        )

        self.assertEqual(result.get("error_code"), "no_match")
        self.assertEqual(result.get("edit_index"), 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "abc\n")

    def test_replace_all_false_replaces_first_match_only(self):
        target = self.root / "dupes.txt"
        target.write_text("cat cat cat\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [apply_edits.Edit(path="dupes.txt", pattern="cat", replacement="dog")],
            replace_all=False,
        )

        self.assertEqual(result["edits"][0]["matches"], 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "dog cat cat\n")

    def test_replace_all_true_replaces_all_matches(self):
        target = self.root / "dupes_all.txt"
        target.write_text("cat cat cat\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [apply_edits.Edit(path="dupes_all.txt", pattern="cat", replacement="dog")],
            replace_all=True,
        )

        self.assertEqual(result["edits"][0]["matches"], 3)
        self.assertEqual(target.read_text(encoding="utf-8"), "dog dog dog\n")

    def test_replacement_is_literal_when_expand_groups_is_false(self):
        target = self.root / "literal.txt"
        target.write_text("abc\n", encoding="utf-8")

        apply_edits.apply_edits(
            [apply_edits.Edit(path="literal.txt", pattern="(a)(b)(c)", replacement=r"\3-\2-\1")],
            expand_groups=False,
        )

        self.assertEqual(target.read_text(encoding="utf-8"), "\\3-\\2-\\1\n")

    def test_named_and_numbered_capture_expansion_and_special_characters(self):
        target = self.root / "captures.txt"
        target.write_text("name:value\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [apply_edits.Edit(
                path="captures.txt",
                pattern=r"(?P<key>\w+):(\w+)",
                replacement=r"\2=\g<key>\\tail $1",
            )],
            expand_groups=True,
        )

        self.assertNotIn("error_code", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "value=name\\tail $1\n")

    def test_empty_file_accepts_empty_match_and_reports_one_replacement(self):
        target = self.root / "empty.txt"
        target.write_text("", encoding="utf-8")

        result = apply_edits.apply_edits(
            [apply_edits.Edit(path="empty.txt", pattern=r"\A\Z", replacement="created")]
        )

        self.assertEqual(result["edits"][0]["matches"], 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "created")

    def test_expand_groups_is_in_callable_schema(self):
        signature = inspect.signature(apply_edits.apply_edits)
        self.assertIn("expand_groups", signature.parameters)
        self.assertFalse(signature.parameters["expand_groups"].default)

    def test_legacy_aliases_are_rejected(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            apply_edits.Edit(path="alias.txt", old_str="hello", new_str="hi")

    def test_bounded_case_insensitive_replacements(self):
        target = self.root / "bounded.txt"
        target.write_text("Cat cat CAT\n", encoding="utf-8")
        result = apply_edits.apply_edits(
            [apply_edits.Edit(path="bounded.txt", pattern="cat", replacement="dog")],
            replace_all=True, max_replacements=2, case_insensitive=True,
        )
        self.assertEqual(result["edits"][0]["matches"], 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "dog dog CAT\n")

    def test_html_edits_prettify_once_and_return_final_file_diff(self):
        target = self.root / "page.html"
        target.write_text("<div><p>old</p></div>", encoding="utf-8")
        result = apply_edits.apply_edits([
            apply_edits.Edit(path="page.html", pattern="old", replacement="new"),
            apply_edits.Edit(path="page.html", pattern="<p>", replacement="<p class='x'>"),
        ])
        saved = target.read_text(encoding="utf-8")
        self.assertIn('<p class="x">', saved)
        self.assertIn("new", saved)
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(result["files"][0]["path"], "page.html")
        self.assertIn("<div><p>old</p></div>", result["files"][0]["diff"])
        self.assertTrue(result["files"][0]["formatter"]["ok"])
        self.assertEqual([edit["matches"] for edit in result["edits"]], [1, 1])

    def test_html_dry_run_previews_formatting_without_writing(self):
        target = self.root / "page.htm"
        original = "<p>old</p>"
        target.write_text(original, encoding="utf-8")
        result = apply_edits.apply_edits(
            [apply_edits.Edit(path="page.htm", pattern="old", replacement="new")],
            dry_run=True,
        )
        self.assertEqual(target.read_text(encoding="utf-8"), original)
        self.assertTrue(result["files"][0]["formatter"]["changed"])
        self.assertIn("new", result["files"][0]["diff"])

    def test_html_formatting_failure_keeps_edit_and_reports_status(self):
        from unittest.mock import patch

        target = self.root / "page.html"
        target.write_text("<p>old</p>", encoding="utf-8")
        with patch("lib.core.apply_edits.prettify_html", return_value=("<p>new</p>", {
            "ok": False, "changed": False, "error": "formatter failed",
        })):
            result = apply_edits.apply_edits([
                apply_edits.Edit(path="page.html", pattern="old", replacement="new")
            ])
        self.assertEqual(target.read_text(encoding="utf-8"), "<p>new</p>")
        self.assertFalse(result["files"][0]["formatter"]["ok"])


if __name__ == "__main__":
    unittest.main()
