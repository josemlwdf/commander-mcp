import tempfile
import unittest
from pathlib import Path

from lib import support
from lib.core import orchestrator_bootstrap


class OrchestratorBootstrapTests(unittest.TestCase):
    def setUp(self):
        self._original_root = str(Path(support.ROOT))

    def tearDown(self):
        support.configure(self._original_root)

    def test_bootstrap_creates_plan_and_tasks_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            support.configure(tmp)
            result = orchestrator_bootstrap.orchestrator_bootstrap(
                user_task="Implement feature X with clear staging",
                context="Prefer low-risk ordering",
            )

            self.assertTrue(result["created"])
            self.assertEqual(result["state_dir"], ".handy_coder")

            plan = Path(tmp) / result["implementation_plan"]
            tasks = Path(tmp) / result["tasks_file"]
            self.assertTrue(plan.exists())
            self.assertTrue(tasks.exists())

            plan_text = plan.read_text(encoding="utf-8")
            tasks_text = tasks.read_text(encoding="utf-8")
            self.assertIn("generalist", plan_text)
            self.assertIn("planner", plan_text)
            self.assertIn("coder", plan_text)
            self.assertIn("- [ ]", tasks_text)
            self.assertIn("status: non_started", tasks_text)

    def test_bootstrap_returns_already_exists_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            support.configure(tmp)
            first = orchestrator_bootstrap.orchestrator_bootstrap(user_task="Initial task")
            self.assertTrue(first["created"])

            again = orchestrator_bootstrap.orchestrator_bootstrap(user_task="Second task")
            self.assertEqual(again["error_code"], "already_exists")
            self.assertIn("existing_files", again)

    def test_bootstrap_can_overwrite_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            support.configure(tmp)
            orchestrator_bootstrap.orchestrator_bootstrap(user_task="Initial task")
            result = orchestrator_bootstrap.orchestrator_bootstrap(
                user_task="Replacement task",
                overwrite=True,
            )
            self.assertTrue(result["created"])
            plan_path = Path(tmp) / result["implementation_plan"]
            self.assertIn("Replacement task", plan_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
