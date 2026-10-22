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


if __name__ == "__main__":
    unittest.main()
