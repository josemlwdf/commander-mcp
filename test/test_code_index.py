import tempfile
import unittest
from pathlib import Path

from lib import support, code_index


class CodeIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_root = support.ROOT
        support.configure(str(self.root))
        (self.root / "A.cs").write_text('''namespace Game;\n/// <summary>Clamp movement outside the pitch.</summary>\npublic class SoccerAgent {\n public void ClampOutOfBoundsMovement() { Move(); }\n public void Move() {}\n public class Nested { public int Score { get; set; } }\n}\n''')

    def tearDown(self):
        support.ROOT = self.old_root
        self.temp.cleanup()

    def test_incremental_search_symbol_and_references(self):
        first = code_index.index_codebase()
        self.assertEqual(first["parsed"], 1)
        second = code_index.index_codebase()
        self.assertEqual(second["skipped"], 1)
        search = code_index.code_navigator("search_code", query="ClampOutOfBoundsMovement")
        self.assertEqual(search["results"][0]["name"], "ClampOutOfBoundsMovement")
        symbol = code_index.code_navigator("get_symbol", symbol="SoccerAgent.ClampOutOfBoundsMovement")
        self.assertIn("Move();", symbol["source"])
        refs = code_index.code_navigator("find_references", symbol="Move")
        self.assertTrue(refs["references"])

    def test_add_change_delete(self):
        code_index.index_codebase()
        (self.root / "B.cs").write_text("class Added { void Work() {} }")
        self.assertEqual(code_index.index_codebase()["parsed"], 1)
        (self.root / "A.cs").write_text("class Replaced { void Next() {} }")
        code_index.index_codebase()
        (self.root / "B.cs").unlink()
        self.assertEqual(code_index.index_codebase()["removed"], 1)

    def test_input_validation_and_isolation(self):
        self.assertEqual(code_index.code_navigator("search_code", query=" ")["error_code"], "missing_parameter")
        self.assertEqual(code_index.code_navigator("nope")["error_code"], "invalid_action")
        other = tempfile.TemporaryDirectory()
        old = support.ROOT
        try:
            support.configure(other.name)
            (Path(other.name) / "Other.cs").write_text("class Other {}")
            code_index.index_codebase()
            self.assertTrue((Path(other.name) / ".handy-coder/code_index.sqlite3").exists())
        finally:
            support.ROOT = old
            other.cleanup()


if __name__ == "__main__":
    unittest.main()
