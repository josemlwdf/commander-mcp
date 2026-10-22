import sys
import unittest
from pathlib import Path

from lib import support


class SupportProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.configure(str(Path(__file__).resolve().parents[1]))

    def test_duration_ms_success_and_nonnegative(self):
        result = support.run_process([sys.executable, "-c", "import time; time.sleep(0.03); print('ok')"])
        self.assertEqual(result["exit_code"], 0)
        self.assertFalse(result["timed_out"])
        self.assertGreaterEqual(result["duration_ms"], 0)
        self.assertIn("ok", result["stdout"])

    def test_duration_ms_nonzero_exit_not_derived_from_exit_code(self):
        result = support.run_process([sys.executable, "-c", "import time,sys; time.sleep(0.05); sys.exit(7)"])
        self.assertEqual(result["exit_code"], 7)
        self.assertFalse(result["timed_out"])
        self.assertGreater(result["duration_ms"], 20)
        self.assertNotEqual(result["duration_ms"], result["exit_code"])

    def test_duration_ms_timeout(self):
        result = support.run_process(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout_ms=200,
        )
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["exit_code"], 124)
        self.assertGreaterEqual(result["duration_ms"], 0)

    def test_timing_metadata_consistent_between_list_and_shell_modes(self):
        list_result = support.run_process([sys.executable, "-c", "import time; time.sleep(0.02)"])
        shell_result = support.run_process(
            '.venv/bin/python -c "import time; time.sleep(0.02)"',
            shell=True,
        )
        for result in (list_result, shell_result):
            self.assertIn("duration_ms", result)
            self.assertIn("timed_out", result)
            self.assertIn("exit_code", result)
            self.assertGreaterEqual(result["duration_ms"], 0)

    def test_configure_creates_handy_coder_state_dir(self):
        state_dir = Path(support.ROOT) / ".handy_coder"
        self.assertTrue(state_dir.exists())
        self.assertTrue(state_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
