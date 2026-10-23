import unittest
from unittest.mock import patch

import handy_coder_mcp
from lib.core import commit_nudge


class ServerInstructionsTests(unittest.TestCase):
    def test_policy_returned_when_nudges_enabled(self):
        for mode in ("nudge", "gate"):
            with self.subTest(enforcement=mode):
                with patch.object(commit_nudge.CONFIG, "enforcement", mode):
                    text = handy_coder_mcp._server_instructions()
                self.assertIsInstance(text, str)
                self.assertIn("[commit-nudge]", text)
                self.assertIn("[commit-action]", text)
                self.assertIn("git_manage", text)
                self.assertIn("defer_commit", text)
                self.assertIn("including reads", text)
                self.assertIn("up to 50 successful write calls", text)
                self.assertIn("blocked again until a successful commit", text)
                self.assertIn("Never push", text)

    def test_no_instructions_when_enforcement_off(self):
        with patch.object(commit_nudge.CONFIG, "enforcement", "off"):
            self.assertIsNone(handy_coder_mcp._server_instructions())

    def test_config_restored_after_patch(self):
        original = commit_nudge.CONFIG.enforcement
        with patch.object(commit_nudge.CONFIG, "enforcement", "off"):
            pass
        self.assertEqual(commit_nudge.CONFIG.enforcement, original)


if __name__ == "__main__":
    unittest.main()