import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.core import apply_edits, terminal_exec


class FakeMCP:
    def __init__(self):
        self.descriptions = {}

    def tool(self, description):
        def register(function):
            self.descriptions[function.__name__] = description
            return function
        return register


class WorkspacePathTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parents[1]
        support.configure(str(self.repo_root))

    def test_configured_workspace_is_root_and_working_directory(self):
        with tempfile.TemporaryDirectory() as workspace:
            with patch.object(support, "ROOT", ""), patch.object(support, "WORKING_DIRECTORY", ""):
                support.configure(workspace)
                expected = Path(workspace).resolve()
                self.assertEqual(support.ROOT, expected)
                self.assertEqual(support.WORKING_DIRECTORY, expected)

    def test_working_directory_is_only_in_terminal_description(self):
        support.configure(str(Path(__file__).resolve().parents[1]))
        mcp = FakeMCP()
        terminal_exec.register(mcp)

        self.assertNotIn("Working directory:", support.describe("Read workspace files"))
        self.assertIn("Working directory:", mcp.descriptions["terminal_exec"])
        self.assertIn(str(support.ROOT), mcp.descriptions["terminal_exec"])
        self.assertIn("not a complete sandbox", mcp.descriptions["terminal_exec"])

    def test_apply_edits_description_advertises_order_and_failure_codes(self):
        support.configure(str(Path(__file__).resolve().parents[1]))
        mcp = FakeMCP()
        apply_edits.register(mcp)

        description = mcp.descriptions["apply_edits"]
        self.assertIn("order-sensitive", description)
        self.assertIn("invalid_replacement", description)
        self.assertIn("nothing is written", description)


    def test_inside_rejects_sibling_prefix_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            workspace = parent / "ws"
            workspace.mkdir()
            sibling = parent / "ws-evil"
            sibling.mkdir()
            outside_file = sibling / "outside.txt"
            outside_file.write_text("x", encoding="utf-8")

            support.configure(str(workspace))
            with self.assertRaises(ValueError):
                support.inside(str(outside_file), must_exist=True)

    def test_inside_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            workspace = parent / "workspace"
            workspace.mkdir()
            outside = parent / "outside.txt"
            outside.write_text("secret", encoding="utf-8")
            link = workspace / "link.txt"
            os.symlink(outside, link)

            support.configure(str(workspace))
            with self.assertRaises(ValueError):
                support.inside("link.txt", must_exist=True)


if __name__ == "__main__":
    unittest.main()
