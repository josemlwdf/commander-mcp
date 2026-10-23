import os
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import pydantic
from pydantic import ValidationError

from lib import support
from lib.core import apply_edits, code_search, git_manage, read_files, terminal_exec
from lib.extras import debugger
from lib.worker_supervisor import (
    DEFAULT_WORKER_DEADLINE,
    run_tool_in_worker,
    supervise_fastmcp_tools,
    _SEMAPHORE,
)


def sample_quick_task(x: int) -> dict:
    return {"result": x * 2}


def sample_hanging_task(duration: float) -> dict:
    time.sleep(duration)
    return {"done": True}


def sample_crashing_task() -> dict:
    os._exit(42)


def sample_raising_task() -> dict:
    raise ValueError("intentional worker error")


class MockTool:
    def __init__(self, fn, name):
        self.fn = fn
        self.name = name


class MockManager:
    def __init__(self, tools):
        self._tools = {t.name: t for t in tools}


class MockFastMCP:
    def __init__(self, tools):
        self._tool_manager = MockManager(tools)
        self._tools = {t.name: t for t in tools}

    def tool(self, description):
        def decorator(fn):
            mock_tool = MockTool(fn, fn.__name__)
            self._tools[fn.__name__] = mock_tool
            self._tool_manager._tools[fn.__name__] = mock_tool
            return fn
        return decorator


class WorkerSupervisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.configure(str(Path(__file__).resolve().parents[1]))

    def test_run_tool_success(self):
        res = run_tool_in_worker(sample_quick_task, args=(21,))
        self.assertEqual(res, {"result": 42})

    def test_run_tool_timeout_kills_process_group_and_returns_error(self):
        res = run_tool_in_worker(sample_hanging_task, args=(5.0,), deadline=0.3)
        self.assertTrue(res.get("timed_out"))
        self.assertEqual(res.get("error_code"), "timeout")
        self.assertIn("timed out after 0.3s", res.get("message", ""))
        self.assertIn("duration_ms", res)
        self.assertEqual(res.get("timeout_ms"), 300)

    def test_run_tool_crash_returns_error(self):
        res = run_tool_in_worker(sample_crashing_task, deadline=2.0)
        self.assertEqual(res.get("error_code"), "worker_crash")
        self.assertIn("terminated unexpectedly", res.get("message", ""))
        self.assertIn("duration_ms", res)

    def test_run_tool_re_raises_tool_exceptions(self):
        with self.assertRaises(ValueError) as ctx:
            run_tool_in_worker(sample_raising_task)
        self.assertIn("intentional worker error", str(ctx.exception))

    def test_supervise_fastmcp_tools_wraps_non_debugger_tools(self):
        t1 = MockTool(sample_quick_task, "quick_task")
        t2 = MockTool(sample_hanging_task, "debug_session_step")
        mcp = MockFastMCP([t1, t2])

        supervise_fastmcp_tools(mcp)

        # both tools are wrapped; debug runs in-process while others may use worker
        self.assertIsNot(t2.fn, sample_hanging_task)
        self.assertIsNot(t1.fn, sample_quick_task)

        # Wrapped tool execution calls worker
        result = t1.fn(10)
        self.assertEqual(result, {"result": 20})

    def test_supervise_fastmcp_tools_preserves_terminal_exec_timeout(self):
        t_exec = MockTool(terminal_exec.terminal_exec, "terminal_exec")
        mcp = MockFastMCP([t_exec])

        supervise_fastmcp_tools(mcp)

        # Call with explicit timeout_ms
        res = t_exec.fn("echo hello", timeout_ms=5000)
        self.assertEqual(res.get("exit_code"), 0)
        self.assertEqual(res.get("stdout").strip(), "hello")
        self.assertIn("timeout_reason", res)
        self.assertIsNone(res["timeout_reason"])

    def test_supervised_apply_edits_forwards_expand_groups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_root = support.ROOT
            support.configure(temp_dir)
            try:
                target = Path(temp_dir) / "capture.txt"
                target.write_text("cat\n", encoding="utf-8")
                tool = MockTool(apply_edits.apply_edits, "apply_edits")
                mcp = MockFastMCP([tool])
                supervise_fastmcp_tools(mcp)

                result = tool.fn(
                    [apply_edits.Edit(path="capture.txt", pattern=r"(c)(at)", replacement=r"\2-\1")],
                    expand_groups=True,
                )

                self.assertNotIn("error_code", result)
                self.assertEqual(target.read_text(encoding="utf-8"), "at-c\n")
            finally:
                support.configure(str(previous_root))

    def test_terminal_exec_reports_timeout_reason(self):
        # Test terminal_exec process timeout
        res = terminal_exec.terminal_exec("sleep 5", timeout_ms=300)
        self.assertTrue(res.get("timed_out"))
        self.assertIsNotNone(res.get("timeout_reason"))
        self.assertIn("exceeded", res.get("timeout_reason", ""))

    def test_nested_models_reject_unknown_fields(self):
        # Position in apply_edits
        with self.assertRaises(ValidationError):
            apply_edits.Position(line=1, col=1, extra_field="bad")

        # Edit in apply_edits
        with self.assertRaises(ValidationError):
            apply_edits.Edit(path="a.py", old_str="x", new_str="y", unknown="forbidden")

        # LineRange in read_files
        with self.assertRaises(ValidationError):
            read_files.LineRange(path="a.py", start=1, end=10, unexpected_attr=True)

    def test_read_files_returns_metadata_ranges_and_total_lines(self):
        test_file = Path(support.ROOT) / "test_read_meta.txt"
        test_file.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n", encoding="utf-8")
        try:
            res = read_files.read_files(
                paths=["test_read_meta.txt"],
                line_ranges=[read_files.LineRange(path="test_read_meta.txt", start=2, end=4)],
            )
            self.assertEqual(len(res["files"]), 1)
            item = res["files"][0]
            self.assertEqual(item["total_lines"], 5)
            self.assertEqual(item["applied_start"], 2)
            self.assertEqual(item["applied_end"], 4)
            self.assertEqual(item["content"], "line 2\nline 3\nline 4")
        finally:
            if test_file.exists():
                test_file.unlink()

    def test_code_search_returns_error_code_for_invalid_regex(self):
        res = code_search.search_text("[unclosed bracket")
        self.assertEqual(res.get("error_code"), "invalid_regex")
        self.assertIn("message", res)

    def test_apply_edits_stages_batch_and_rolls_back_on_failure(self):
        f1 = Path(support.ROOT) / "test_stage_1.txt"
        f2 = Path(support.ROOT) / "test_stage_2.txt"
        f1.write_text("apple banana", encoding="utf-8")
        f2.write_text("cat dog", encoding="utf-8")
        try:
            # Second edit has invalid regex, first edit should not be written to disk
            edits = [
                apply_edits.Edit(path="test_stage_1.txt", old_str="apple", new_str="orange"),
                apply_edits.Edit(path="test_stage_2.txt", old_str="[invalid regex", new_str="parrot"),
            ]
            result = apply_edits.apply_edits(edits=edits)
            self.assertEqual(result.get("error_code"), "invalid_regex")
            self.assertEqual(result.get("edit_index"), 1)

            # Check that first file was untouched
            self.assertEqual(f1.read_text(encoding="utf-8"), "apple banana")
            self.assertEqual(f2.read_text(encoding="utf-8"), "cat dog")
        finally:
            if f1.exists():
                f1.unlink()
            if f2.exists():
                f2.unlink()

    def test_path_validation_rejects_out_of_root_before_fs_access(self):
        with self.assertRaises(ValueError) as ctx:
            support.inside("../escaped_file.txt")
        self.assertIn("escapes", str(ctx.exception).lower())

        with self.assertRaises(ValueError) as ctx:
            support.inside("/etc/passwd")
        self.assertIn("escapes", str(ctx.exception).lower())

    def test_debugger_worker_preserves_session_across_calls(self):
        # Set up a mock DAP server on a loopback socket
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]

        def handle_dap(sock):
            conn, _ = sock.accept()
            try:
                reader = conn.makefile("rb")
                while True:
                    length = None
                    while True:
                        line = reader.readline()
                        if not line:
                            return
                        if line in (b"\r\n", b"\n"):
                            break
                        key, _, val = line.decode("ascii").partition(":")
                        if key.lower() == "content-length":
                            length = int(val.strip())
                    if length is None:
                        return
                    body = reader.read(length)
                    import json
                    req = json.loads(body)
                    cmd = req["command"]
                    seq = req["seq"]

                    resp = {
                        "seq": seq + 100,
                        "type": "response",
                        "request_seq": seq,
                        "command": cmd,
                        "success": True,
                    }
                    if cmd == "initialize":
                        resp["body"] = {}
                        payload = json.dumps(resp).encode("utf-8")
                        conn.sendall(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
                        evt = {
                            "seq": seq + 101,
                            "type": "event",
                            "event": "initialized",
                        }
                        evt_payload = json.dumps(evt).encode("utf-8")
                        conn.sendall(f"Content-Length: {len(evt_payload)}\r\n\r\n".encode("ascii") + evt_payload)
                    elif cmd == "disconnect":
                        payload = json.dumps(resp).encode("utf-8")
                        conn.sendall(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
                        return
                    else:
                        payload = json.dumps(resp).encode("utf-8")
                        conn.sendall(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
            except Exception:
                pass
            finally:
                conn.close()

        server_thread = threading.Thread(target=handle_dap, args=(server_sock,), daemon=True)
        server_thread.start()

        env_patch = {
            "HANDY-CODER_DAP_HOST": "127.0.0.1",
            "HANDY-CODER_DAP_PORT": str(port),
            "HANDY-CODER_DAP_START_REQUEST": '{"command": "launch", "arguments": {}}',
        }

        fake_mcp = MockFastMCP([])
        debugger.register(fake_mcp)
        debug_tool = fake_mcp._tools["debug_session_step"].fn

        with patch.dict(os.environ, env_patch):
            # Call 1: start
            res1 = debug_tool(action="start")
            self.assertTrue(res1.get("supported"))
            self.assertEqual(res1.get("action"), "start")

            # Call 2: stop (verifying state was preserved across calls)
            res2 = debug_tool(action="stop")
            self.assertTrue(res2.get("supported"))
            self.assertEqual(res2.get("action"), "stop")

        server_sock.close()


if __name__ == "__main__":
    unittest.main()
