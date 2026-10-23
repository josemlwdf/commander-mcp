import unittest

from mcp.server.fastmcp import FastMCP

from lib import code_index, tool_details
from lib.core import code_navigator, system_info, web_search_tool

NAMED_TOOLS = ("web_search", "index_codebase", "code_navigator", "get_tool_details")


class IndexCodebaseSchemaTests(unittest.TestCase):
    def setUp(self):
        self.mcp = FastMCP("t")
        code_navigator.register(self.mcp)

    def test_exclude_dirs_description_lists_real_defaults(self):
        props = self.mcp._tool_manager.get_tool("index_codebase").parameters["properties"]
        description = props["exclude_dirs"]["description"]
        self.assertIn("in addition to the default exclusions", description)
        for name in code_index.DEFAULT_EXCLUDES:
            self.assertIn(name, description)

    def test_exclude_dirs_remains_optional_string_list(self):
        schema = self.mcp._tool_manager.get_tool("index_codebase").parameters
        self.assertNotIn("exclude_dirs", schema.get("required", []))
        any_of = schema["properties"]["exclude_dirs"]["anyOf"]
        self.assertIn({"type": "array", "items": {"type": "string"}}, any_of)


class CapabilityNotesTests(unittest.TestCase):
    def tearDown(self):
        system_info._MCP = None

    def assert_specific_notes(self, tools):
        for name in NAMED_TOOLS:
            with self.subTest(tool=name):
                self.assertIn(name, tools)
                self.assertTrue(tools[name]["available"])
                self.assertNotEqual(tools[name]["note"], "registered MCP tool")

    def test_registry_branch_has_specific_notes(self):
        mcp = FastMCP("t")
        tool_details.install(mcp)
        web_search_tool.register(mcp)
        code_navigator.register(mcp)
        system_info.register(mcp)
        registered = set(mcp._tool_manager._tools)
        for name in NAMED_TOOLS:
            self.assertIn(name, registered)
        result = system_info.get_capabilities()
        self.assertEqual(set(result["tools"]) - {"get_capabilities"}, registered - {"get_capabilities"})
        self.assert_specific_notes(result["tools"])

    def test_fallback_branch_has_specific_notes(self):
        system_info._MCP = None
        self.assert_specific_notes(system_info.get_capabilities()["tools"])

    def test_notes_mention_key_behavior(self):
        system_info._MCP = None
        tools = system_info.get_capabilities()["tools"]
        self.assertIn("syntactic", tools["code_navigator"]["note"])
        self.assertIn("code_index.sqlite3", tools["index_codebase"]["note"])
        self.assertIn("unknown_tool", tools["get_tool_details"]["note"])


if __name__ == "__main__":
    unittest.main()