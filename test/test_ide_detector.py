import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import ide_detector
import handy_coder_mcp


class IdeDetectorTests(unittest.TestCase):
    def test_detect_ide_from_term_program(self):
        with patch.dict(os.environ, {"TERM_PROGRAM": "vscode"}, clear=True):
            self.assertEqual(ide_detector.detect_ide(), "vscode")

        with patch.dict(os.environ, {"TERM_PROGRAM": "cursor"}, clear=True):
            self.assertEqual(ide_detector.detect_ide(), "cursor")

        with patch.dict(os.environ, {"TERM_PROGRAM": "windsurf"}, clear=True):
            self.assertEqual(ide_detector.detect_ide(), "windsurf")

        with patch.dict(os.environ, {"TERM_PROGRAM": "codium"}, clear=True):
            self.assertEqual(ide_detector.detect_ide(), "vscodium")

    def test_detect_ide_from_env_vars(self):
        with patch.dict(os.environ, {"VSCODE_GIT_IPC_HANDLE": "/tmp/ipc"}, clear=True):
            self.assertEqual(ide_detector.detect_ide(), "vscode")

        with patch.dict(os.environ, {"CURSOR_PROJECT_DIR": "/home/user/project"}, clear=True):
            self.assertEqual(ide_detector.detect_ide(), "cursor")

    def test_uri_to_path_wsl_remote(self):
        uri = "vscode-remote://wsl%2Bkali-linux/opt/handy-coder_mcp"
        path = ide_detector.uri_to_path(uri)
        self.assertIsNotNone(path)
        self.assertEqual(str(path), "/opt/handy-coder_mcp")

    def test_uri_to_path_wsl_network_share(self):
        uri = "file://wsl.localhost/kali-linux/home/think/repo"
        path = ide_detector.uri_to_path(uri)
        self.assertIsNotNone(path)
        self.assertEqual(str(path), "/home/think/repo")

        uri_dollar = "file://wsl$/kali-linux/home/think/repo"
        path_dollar = ide_detector.uri_to_path(uri_dollar)
        self.assertIsNotNone(path_dollar)
        self.assertEqual(str(path_dollar), "/home/think/repo")

    def test_uri_to_path_file_linux(self):
        uri = "file:///home/think/test_mcp"
        path = ide_detector.uri_to_path(uri)
        self.assertIsNotNone(path)
        self.assertEqual(str(path), "/home/think/test_mcp")

    def test_uri_to_path_code_workspace_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            sub_project = tmp_path / "sub_project"
            sub_project.mkdir()
            ws_file = tmp_path / "my.code-workspace"
            ws_file.write_text(json.dumps({"folders": [{"path": "sub_project"}]}))

            resolved = ide_detector.uri_to_path(ws_file.as_uri())
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved, sub_project.resolve())

    def test_is_disallowed_default_root(self):
        self.assertTrue(ide_detector._is_disallowed_default_root(Path("/")))
        self.assertTrue(ide_detector._is_disallowed_default_root(Path("/mnt/c")))
        self.assertTrue(ide_detector._is_disallowed_default_root(Path("/mnt/c/Users/josel")))
        self.assertFalse(ide_detector._is_disallowed_default_root(Path("/opt/handy-coder_mcp")))

    def test_recover_workspace_roots_from_storage_json(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_user = Path(tmp_dir) / "User"
            storage_dir = fake_user / "globalStorage"
            storage_dir.mkdir(parents=True)

            project_dir = Path(tmp_dir) / "my_project"
            project_dir.mkdir()

            storage_json = storage_dir / "storage.json"
            storage_json.write_text(json.dumps({
                "windowsState": {
                    "lastActiveWindow": {
                        "folder": project_dir.as_uri(),
                    },
                    "openedWindows": [],
                }
            }))

            with patch("lib.ide_detector.find_ide_user_data_dirs", return_value=[fake_user]):
                recovered = ide_detector.recover_workspace_roots("vscode")
                self.assertEqual(recovered, [project_dir.resolve()])

    def test_detect_working_dir_prefers_cli_and_env(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            p1 = tmp_path / "cli_root"
            p1.mkdir()
            p2 = tmp_path / "env_root"
            p2.mkdir()

            # CLI argument wins
            self.assertEqual(
                ide_detector.detect_working_dir(candidate=str(p1)),
                str(p1.resolve()),
            )
            self.assertEqual(handy_coder_mcp._resolve_root(str(p1)), str(p1.resolve()))

            # HANDY-CODER_ROOT wins over IDE settings
            with patch.dict(os.environ, {"HANDY-CODER_ROOT": str(p2)}):
                self.assertEqual(
                    ide_detector.detect_working_dir(),
                    str(p2.resolve()),
                )

            # Unexpanded variable is ignored, falls back to IDE settings or script_dir
            with patch.dict(os.environ, {"HANDY-CODER_ROOT": "${workspaceFolder}"}):
                with patch("lib.ide_detector.recover_workspace_roots", return_value=[]):
                    with patch("pathlib.Path.cwd", return_value=Path("/mnt/c/Users/josel")):
                        res = ide_detector.detect_working_dir(candidate="${workspaceFolder}", script_dir=tmp_path)
                        self.assertEqual(res, str(tmp_path.resolve()))

    def test_detect_working_dir_recovers_from_ide_settings(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fake_user = Path(tmp_dir) / "User"
            storage_dir = fake_user / "globalStorage"
            storage_dir.mkdir(parents=True)

            active_project = Path(tmp_dir) / "active_project"
            active_project.mkdir()

            storage_json = storage_dir / "storage.json"
            storage_json.write_text(json.dumps({
                "windowsState": {
                    "lastActiveWindow": {
                        "folder": f"vscode-remote://wsl+test{active_project}",
                    }
                }
            }))

            with patch("lib.ide_detector.find_ide_user_data_dirs", return_value=[fake_user]):
                with patch.dict(os.environ, {}, clear=True):
                    # Mock CWD to user profile so CWD fallback doesn't shadow recovery
                    with patch("pathlib.Path.cwd", return_value=Path("/mnt/c/Users/josel")):
                        detected = ide_detector.detect_working_dir(script_dir=Path(tmp_dir) / "script")
                        self.assertEqual(detected, str(active_project.resolve()))

    def test_detect_working_dir_uses_process_cwd_for_multiple_workspaces(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            workspace_a = base / "workspace_a"
            workspace_b = base / "workspace_b"
            checkout = base / "server"
            for path in (workspace_a, workspace_b, checkout):
                path.mkdir()

            # Candidate order can change with global window recency. The MCP
            # process cwd remains the per-launch workspace signal.
            for roots in ([workspace_a, workspace_b], [workspace_b, workspace_a]):
                for process_workspace in (workspace_a, workspace_b):
                    with self.subTest(roots=roots, cwd=process_workspace):
                        with patch("lib.ide_detector.detect_ide", return_value="vscode"):
                            with patch("lib.ide_detector.recover_workspace_roots", return_value=roots):
                                with patch("pathlib.Path.cwd", return_value=process_workspace):
                                    detected = ide_detector.detect_working_dir(script_dir=checkout)
                        self.assertEqual(detected, str(process_workspace.resolve()))

    def test_detect_working_dir_ignores_ambiguous_storage_after_stale_last_active(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            stale = base / "stale_last_active"
            current = base / "current_workspace"
            checkout = base / "server"
            for path in (stale, current, checkout):
                path.mkdir()
            fake_user = base / "User"
            storage_dir = fake_user / "globalStorage"
            storage_dir.mkdir(parents=True)
            (storage_dir / "storage.json").write_text(json.dumps({
                "windowsState": {
                    "lastActiveWindow": {"folder": stale.as_uri()},
                    "openedWindows": [{"folder": current.as_uri()}],
                }
            }))

            with patch("lib.ide_detector.find_ide_user_data_dirs", return_value=[fake_user]):
                with patch("lib.ide_detector.detect_ide", return_value="vscode"):
                    with patch("pathlib.Path.cwd", return_value=current):
                        detected = ide_detector.detect_working_dir(script_dir=checkout)
            self.assertEqual(detected, str(current.resolve()))

    def test_detect_working_dir_ambiguous_storage_falls_back_to_checkout(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            first = base / "first"
            second = base / "second"
            checkout = base / "server"
            for path in (first, second, checkout):
                path.mkdir()
            with patch("lib.ide_detector.detect_ide", return_value="vscode"):
                with patch("lib.ide_detector.recover_workspace_roots", return_value=[first, second]):
                    with patch("pathlib.Path.cwd", return_value=Path("/mnt/c/Users/josel")):
                        detected = ide_detector.detect_working_dir(script_dir=checkout)
            self.assertEqual(detected, str(checkout.resolve()))

    def test_handy_coder_resolve_root_integration(self):
        # handy_coder_mcp._resolve_root should resolve to the open workspace or checkout
        resolved = handy_coder_mcp._resolve_root(None)
        self.assertTrue(Path(resolved).is_dir())
        self.assertFalse(ide_detector._is_disallowed_default_root(Path(resolved)))


if __name__ == "__main__":
    unittest.main()
