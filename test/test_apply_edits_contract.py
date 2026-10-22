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
                apply_edits.Edit(path="sample.txt", old_str="foo", new_str="bar"),
                apply_edits.Edit(path="sample.txt", old_str="bar", new_str="baz"),
            ]
        )

        self.assertNotIn("error_code", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "baz\n")

    def test_overlapping_ordered_edits_fail_atomically(self):
        target = self.root / "overlap.txt"
        target.write_text("abc\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [
                apply_edits.Edit(path="overlap.txt", old_str="abc", new_str="x"),
                apply_edits.Edit(path="overlap.txt", old_str="ab", new_str="y"),
            ]
        )

        self.assertEqual(result.get("error_code"), "no_match")
        self.assertEqual(result.get("edit_index"), 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "abc\n")

    def test_replace_all_false_replaces_first_match_only(self):
        target = self.root / "dupes.txt"
        target.write_text("cat cat cat\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [apply_edits.Edit(path="dupes.txt", old_str="cat", new_str="dog")],
            replace_all=False,
        )

        self.assertEqual(result["edits"][0]["matches"], 1)
        self.assertEqual(target.read_text(encoding="utf-8"), "dog cat cat\n")

    def test_replace_all_true_replaces_all_matches(self):
        target = self.root / "dupes_all.txt"
        target.write_text("cat cat cat\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [apply_edits.Edit(path="dupes_all.txt", old_str="cat", new_str="dog")],
            replace_all=True,
        )

        self.assertEqual(result["edits"][0]["matches"], 3)
        self.assertEqual(target.read_text(encoding="utf-8"), "dog dog dog\n")

    def test_new_str_is_literal_when_expand_groups_is_false(self):
        target = self.root / "literal.txt"
        target.write_text("abc\n", encoding="utf-8")

        apply_edits.apply_edits(
            [apply_edits.Edit(path="literal.txt", old_str="(a)(b)(c)", new_str=r"\3-\2-\1")],
            expand_groups=False,
        )

        self.assertEqual(target.read_text(encoding="utf-8"), "\\3-\\2-\\1\n")

    def test_pattern_and_replacement_aliases_work(self):
        target = self.root / "alias.txt"
        target.write_text("hello world\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [apply_edits.Edit(path="alias.txt", pattern="hello", replacement="hi")]
        )

        self.assertNotIn("error_code", result)
        self.assertEqual(target.read_text(encoding="utf-8"), "hi world\n")

    def test_conflicting_alias_values_return_invalid_edit(self):
        target = self.root / "conflict.txt"
        target.write_text("hello\n", encoding="utf-8")

        result = apply_edits.apply_edits(
            [
                apply_edits.Edit(
                    path="conflict.txt",
                    old_str="hello",
                    pattern="world",
                    new_str="x",
                )
            ]
        )

        self.assertEqual(result.get("error_code"), "invalid_edit")
        self.assertEqual(result.get("edit_index"), 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")


if __name__ == "__main__":
    unittest.main()
