import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from lib import error_handler, support, worker_supervisor
from lib.core import find_files, read_files


class _HandlerFixture:
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp_dir.name).resolve()
        support.configure(str(cls.root))
        (cls.root / "lines.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
        cls.mcp = FastMCP("t")
        read_files.register(cls.mcp)
        find_files.register(cls.mcp)
        worker_supervisor.supervise_fastmcp_tools(cls.mcp)
        error_handler.install(cls.mcp)

    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()
        support.configure(str(Path(__file__).resolve().parents[1]))

    def call(self, name, arguments):
        return asyncio.run(self.mcp._tool_manager.call_tool(name, arguments, convert_result=False))


class ReadFilesAliasSchemaTests(_HandlerFixture, unittest.TestCase):
    def test_published_schema_lists_start_and_end(self):
        params = self.mcp._tool_manager.get_tool("read_files").parameters
        schema = params["$defs"]["LineRange"]
        props = schema["properties"]
        for key in ("path", "start_line", "end_line", "start", "end"):
            self.assertIn(key, props)
        self.assertEqual(schema["required"], ["path"])
        self.assertIs(schema.get("additionalProperties"), False)
        self.assertIn("Deprecated", props["start"]["description"])
        self.assertIn("Deprecated", props["end"]["description"])

    def test_canonical_and_legacy_keys_read_same_lines(self):
        for keys in ({"start_line": 2, "end_line": 3}, {"start": 2, "end": 3}, {"start": 2, "end_line": 3}):
            result = read_files.read_files(
                ["lines.txt"], line_ranges=[{"path": "lines.txt", **keys}]
            )
            self.assertEqual(result["files"][0]["content"], "two\nthree", keys)
            self.assertEqual(result["files"][0]["applied_start"], 2)
            self.assertEqual(result["files"][0]["applied_end"], 3)

    def test_canonical_key_wins_over_alias(self):
        item = read_files.LineRange(path="lines.txt", start_line=1, start=3, end_line=2)
        self.assertEqual((item.start_line, item.end_line), (1, 2))

    def test_unknown_keys_still_rejected(self):
        with self.assertRaises(ValidationError):
            read_files.LineRange.model_validate(
                {"path": "lines.txt", "start_line": 1, "end_line": 2, "bogus": 1}
            )

    def test_incomplete_or_reversed_range_rejected(self):
        for bad in ({"start": 2}, {"end_line": 2}, {"start_line": 3, "end_line": 1}, {"start": 0, "end": 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    read_files.LineRange.model_validate({"path": "lines.txt", **bad})

    def test_reversed_range_is_structured_through_handler(self):
        result = self.call(
            "read_files",
            {"paths": ["lines.txt"], "line_ranges": [{"path": "lines.txt", "start": 3, "end": 1}]},
        )
        self.assertEqual(result["error_code"], "invalid_argument")
        self.assertNotIn("pydantic.dev", json.dumps(result))


class FindFilesSortBySchemaTests(_HandlerFixture, unittest.TestCase):
    def test_sort_by_schema_is_enum_with_modified(self):
        props = self.mcp._tool_manager.get_tool("find_files").parameters["properties"]
        self.assertEqual(props["sort_by"]["enum"], ["path", "size", "modified"])
        self.assertIn("modified", props["sort_by"]["description"])

    def test_sort_by_bogus_is_invalid_argument(self):
        result = self.call("find_files", {"glob_pattern": "*", "sort_by": "bogus"})
        self.assertEqual(result["error_code"], "invalid_argument")
        self.assertEqual(result.get("field"), "sort_by")
        self.assertNotIn("pydantic.dev", json.dumps(result))

    def test_sort_by_valid_values_work(self):
        for value in ("path", "size", "modified"):
            result = self.call("find_files", {"glob_pattern": "*.txt", "sort_by": value})
            self.assertNotIn("error_code", result)
            self.assertIn("lines.txt", result["files"])


if __name__ == "__main__":
    unittest.main()