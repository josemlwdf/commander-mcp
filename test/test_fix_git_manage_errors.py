import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from lib import error_handler, support, tool_details, worker_supervisor
from lib.core import git_manage

BAD_FLAG = "--ignore-space-at-eol"


def _build_server():
    mcp = FastMCP("git-manage-errors")
    tool_details.install(mcp)
    git_manage.register(mcp)
    worker_supervisor.supervise_fastmcp_tools(mcp)
    error_handler.install(mcp)
    return mcp


def _call(mcp, name, arguments, convert_result=False):
    return asyncio.run(mcp._tool_manager.call_tool(name, arguments, convert_result=convert_result))


def _normalize(result):
    """Reduce a call_tool result (dict, content list, or (content, structured) tuple) to a dict."""
    if isinstance(result, tuple) and len(result) == 2:
        content, structured = result
        if isinstance(structured, dict):
            return _unwrap(structured)
        result = content
    if isinstance(result, dict):
        return _unwrap(result)
    if isinstance(result, (list, tuple)):
        for item in result:
            text = getattr(item, "text", None)
            if text:
                try:
                    parsed = json.loads(text)
                except ValueError:
                    return {"raw_text": text}
                if isinstance(parsed, dict):
                    return _unwrap(parsed)
    return {"raw": repr(result)}


def _unwrap(data):
    if "error_code" not in data and set(data) == {"result"} and isinstance(data["result"], dict):
        return data["result"]
    return data


class GitManageDiffOptionErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        support.configure(str(self.root))
        for cmd in (
            ["git", "init", "--quiet"],
            ["git", "config", "user.email", "t@example.com"],
            ["git", "config", "user.name", "T"],
        ):
            subprocess.run(cmd, cwd=self.root, check=True)
        (self.root / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "seed.txt"], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=self.root, check=True)
        git_manage._REPOSITORIES = None
        self.mcp = _build_server()

    def tearDown(self):
        self.temp.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))
        git_manage._REPOSITORIES = None

    def _assert_structured(self, result):
        self.assertIn("error_code", result, result)
        self.assertTrue(result.get("message"), result)
        self.assertIn(BAD_FLAG, result["message"])
        self.assertIn("--cached", result["message"])
        self.assertNotIn("pydantic.dev", json.dumps(result))

    def test_invalid_diff_option_is_structured_without_conversion(self):
        result = _normalize(_call(self.mcp, "git_manage", {"action": "diff", "args": [BAD_FLAG]}))
        self._assert_structured(result)

    def test_invalid_diff_option_is_structured_with_conversion(self):
        result = _normalize(
            _call(self.mcp, "git_manage", {"action": "diff", "args": [BAD_FLAG]}, convert_result=True)
        )
        self._assert_structured(result)

    def test_direct_call_still_raises_value_error_naming_flag_and_allowed_flags(self):
        with self.assertRaises(ValueError) as ctx:
            git_manage.git_manage("diff", args=[BAD_FLAG], repo_path=".")
        message = str(ctx.exception)
        self.assertIn(BAD_FLAG, message)
        for allowed in ("--cached", "--stat", "--name-only", "--"):
            self.assertIn(allowed, message)

    def test_allowed_diff_flags_still_work(self):
        result = git_manage.git_manage("diff", args=["--stat"], repo_path=".")
        self.assertNotIn("error_code", result)


if __name__ == "__main__":
    unittest.main()