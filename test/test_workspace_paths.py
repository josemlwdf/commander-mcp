import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.core import terminal_exec


class FakeMCP:
    def __init__(self):
        self.descriptions = {}

    def tool(self, description):
        def register(function):
            self.descriptions[function.__name__] = description
            return function
        return register


class WorkspacePathTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
