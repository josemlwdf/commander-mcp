import inspect
import unittest
from pathlib import Path
from typing import get_args, get_type_hints

from pydantic import BaseModel, TypeAdapter

from lib import support
from lib.core import (
    apply_edits,
    code_search,
    diagnostics,
    find_files,
    git_manage,
    read_files,
    run_tests,
    skills,
    system_info,
    terminal_exec,
)
from lib.extras import (
    browser,
    code_format_lint,
    debugger,
    lsp_lookup,
    semantic_search,
    web_fetch,
)


class FakeMCP:
    def __init__(self):
        self.tools = {}
        self.descriptions = {}

    def tool(self, description):
        def register(function):
            self.tools[function.__name__] = function
            self.descriptions[function.__name__] = description
            return function
        return register


def _models_in(annotation):
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        yield annotation
    for argument in get_args(annotation):
        yield from _models_in(argument)


class ToolSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.configure(str(Path(__file__).resolve().parents[1]))
        cls.mcp = FakeMCP()
        for module in (
            apply_edits,
            code_search,
            diagnostics,
            find_files,
            git_manage,
            read_files,
            run_tests,
            skills,
            system_info,
            terminal_exec,
            browser,
            code_format_lint,
            debugger,
            lsp_lookup,
            semantic_search,
            web_fetch,
        ):
            module.register(cls.mcp)

    def test_every_tool_and_argument_has_a_description(self):
        self.assertEqual(len(self.mcp.tools), 16)
        for name, function in self.mcp.tools.items():
            with self.subTest(tool=name):
                self.assertTrue(self.mcp.descriptions[name].strip())
                self.assertNotIn("absolute", self.mcp.descriptions[name].lower())
                self.assertIn("Use relative paths.", self.mcp.descriptions[name])
                hints = get_type_hints(function, include_extras=True)
                for parameter in inspect.signature(function).parameters:
                    with self.subTest(argument=parameter):
                        metadata = getattr(hints[parameter], "__metadata__", ())
                        descriptions = [
                            item.description
                            for item in metadata
                            if getattr(item, "description", None)
                        ]
                        self.assertTrue(
                            descriptions,
                            f"{name}.{parameter} is missing a schema description",
                        )
                        for description in descriptions:
                            self.assertNotIn("absolute", description.lower())
                            if (
                                "path" in parameter
                                or parameter in {"paths", "cwd", "glob_pattern", "exclude_pattern"}
                            ):
                                self.assertIn("relative", description.lower())
                    for model in _models_in(hints[parameter]):
                        for field_name, field in model.model_fields.items():
                            with self.subTest(argument=f"{parameter}.{field_name}"):
                                self.assertTrue(
                                    field.description,
                                    f"{name}.{parameter}.{field_name} is missing a schema description",
                                )
                                self.assertNotIn("absolute", field.description.lower())
                                if "path" in field_name:
                                    self.assertIn("relative", field.description.lower())

    def test_every_tool_argument_has_a_concrete_json_schema(self):
        schema_keywords = {
            "type",
            "$ref",
            "anyOf",
            "oneOf",
            "allOf",
            "enum",
            "const",
        }

        def check_schema(schema, location):
            self.assertTrue(
                schema_keywords.intersection(schema),
                f"{location} has no concrete JSON Schema type",
            )
            self.assertNotEqual(
                schema.get("type"),
                "unknown",
                f"{location} has an unknown JSON Schema type",
            )
            for key in ("items", "additionalProperties"):
                nested = schema.get(key)
                if isinstance(nested, dict):
                    check_schema(nested, f"{location}.{key}")
            for key in ("anyOf", "oneOf", "allOf"):
                for index, nested in enumerate(schema.get(key, [])):
                    check_schema(nested, f"{location}.{key}[{index}]")
            for key, nested in schema.get("$defs", {}).items():
                check_schema(nested, f"{location}.$defs.{key}")
            for key, nested in schema.get("properties", {}).items():
                check_schema(nested, f"{location}.properties.{key}")

        for name, function in self.mcp.tools.items():
            hints = get_type_hints(function, include_extras=True)
            for parameter in inspect.signature(function).parameters:
                with self.subTest(tool=name, argument=parameter):
                    schema = TypeAdapter(hints[parameter]).json_schema()
                    check_schema(schema, f"{name}.{parameter}")


if __name__ == "__main__":
    unittest.main()
