import tempfile
import unittest
from pathlib import Path

from lib import support
from lib.core import orchestrator_bootstrap, orchestrator_tasks


class OrchestratorTaskStatusTests(unittest.TestCase):
    def setUp(self):
        self._original_root = str(Path(support.ROOT))

    def tearDown(self):
        support.configure(self._original_root)

    def _bootstrap(self, tmp: str) -> Path:
        support.configure(tmp)
        result = orchestrator_bootstrap.orchestrator_bootstrap(user_task="Track status updates")
        self.assertTrue(result["created"])
        return Path(tmp) / result["tasks_file"]

    def test_update_status_by_line_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks_file = self._bootstrap(tmp)
            result = orchestrator_tasks.orchestrator_update_task_status(
                status="interrupted",
                line_number=12,
            )
            self.assertTrue(result["updated"])
            self.assertEqual(result["previous_status"], "non_started")
            self.assertEqual(result["status"], "interrupted")
            self.assertIn("[status: interrupted]", tasks_file.read_text(encoding="utf-8"))

    def test_update_status_by_task_contains(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks_file = self._bootstrap(tmp)
            result = orchestrator_tasks.orchestrator_update_task_status(
                status="complete",
                task_contains="Run `planner` subagent",
            )
            self.assertTrue(result["updated"])
            self.assertEqual(result["status"], "complete")
            self.assertIn("[status: complete] Run `planner` subagent", tasks_file.read_text(encoding="utf-8"))

    def test_ambiguous_text_selector_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._bootstrap(tmp)
            result = orchestrator_tasks.orchestrator_update_task_status(
                status="failed",
                task_contains="subagent",
            )
            self.assertEqual(result["error_code"], "ambiguous_task_selector")

    def test_invalid_selector_combo_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._bootstrap(tmp)
            result = orchestrator_tasks.orchestrator_update_task_status(
                status="failed",
                line_number=1,
                task_contains="anything",
            )
            self.assertEqual(result["error_code"], "invalid_selector")

    def test_no_write_when_format_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            tasks_file = self._bootstrap(tmp)
            original = tasks_file.read_text(encoding="utf-8")
            lines = original.splitlines()
            lines[9] = "not a status line"
            tampered = "\n".join(lines) + "\n"
            tasks_file.write_text(tampered, encoding="utf-8")

            result = orchestrator_tasks.orchestrator_update_task_status(
                status="complete",
                line_number=10,
            )
            self.assertEqual(result["error_code"], "invalid_task_format")
            self.assertEqual(tasks_file.read_text(encoding="utf-8"), tampered)


if __name__ == "__main__":
    unittest.main()
