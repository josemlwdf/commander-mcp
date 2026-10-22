import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import support
from lib.core import build_project


class BuildProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        support.configure(self.tmp.name)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ambiguous_detection_returns_choices_without_running(self):
        (self.root / "pyproject.toml").write_text("[build-system]\nbuild-backend='setuptools.build_meta'\n")
        (self.root / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))
        with patch.object(build_project, "run_process") as run:
            result = build_project.build_project()
        self.assertEqual(result["error_code"], "ambiguous_project")
        self.assertEqual(len(result["choices"]), 2)
        run.assert_not_called()

    def test_node_lockfile_selects_manager_and_returns_parsed_diagnostics(self):
        project = self.root / "app"
        project.mkdir()
        (project / "package.json").write_text(json.dumps({"scripts": {"build": "tsc"}}))
        (project / "pnpm-lock.yaml").write_text("lockfileVersion: 9")
        output = {"exit_code": 1, "stdout": "src/a.ts(4,2): error TS1: bad\n", "stderr": "warning: check\n", "timed_out": False, "duration_ms": 23}
        with patch.object(build_project.shutil, "which", return_value="/bin/pnpm"), patch.object(build_project, "run_process", return_value=output) as run:
            result = build_project.build_project(path="app")
        self.assertEqual(run.call_args.args[0], ["/bin/pnpm", "run", "build"])
        self.assertEqual(result["errors"][0]["line"], 4)
        self.assertEqual(result["errors"][0]["column"], 2)
        self.assertEqual(result["raw_output"], output["stdout"] + output["stderr"])

    def test_path_confinement(self):
        with self.assertRaises(ValueError):
            build_project.build_project(path="../outside")

    def test_missing_node_package_manager_is_reported(self):
        project = self.root / "app"
        project.mkdir()
        (project / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))
        with patch.object(build_project.shutil, "which", return_value=None), patch.object(build_project, "run_process") as run:
            result = build_project.build_project(path="app")
        self.assertEqual(result["error_code"], "build_tool_unavailable")
        run.assert_not_called()

    def test_timeout_is_forwarded_and_reported(self):
        project = self.root / "app"
        project.mkdir()
        (project / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))
        timed_out = {"exit_code": 124, "stdout": "partial", "stderr": "", "timed_out": True, "duration_ms": 100}
        with patch.object(build_project.shutil, "which", return_value="/bin/npm"), patch.object(build_project, "run_process", return_value=timed_out) as run:
            result = build_project.build_project(path="app", timeout_ms=45000)
        self.assertEqual(run.call_args.kwargs["timeout_ms"], 45000)
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
