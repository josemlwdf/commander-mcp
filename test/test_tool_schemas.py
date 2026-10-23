import inspect
import unittest
from pathlib import Path
from typing import get_args, get_type_hints

from pydantic import BaseModel, TypeAdapter

from lib import support
from lib.core import (
    build_project,
    apply_edits,
    create_file,
    code_search,
    diagnostics,
    find_files,
    html_syntax_check,
    commit_nudge,
    git_manage,
    read_files,
    run_tests,
    skills,
    system_info,
    terminal_exec,
    orchestrator_bootstrap,
    orchestrator_tasks,
    web_search_tool,
)
from lib.extras import (
    browser,
    code_format_lint,
    debugger,
    lsp_lookup,
    semantic_search,
    web_fetch,
)
from lib.subagents import tools as subagent_tools


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
            create_file,
            html_syntax_check,
            code_search,
            diagnostics,
            find_files,
            commit_nudge,
            git_manage,
            build_project,
            read_files,
            run_tests,
            skills,
            system_info,
            terminal_exec,
            orchestrator_bootstrap,
            orchestrator_tasks,
            browser,
            code_format_lint,
            debugger,
            lsp_lookup,
            semantic_search,
            web_fetch,
            web_search_tool,
            subagent_tools,
        ):
            module.register(cls.mcp)

    def test_every_tool_and_argument_has_a_description(self):
        self.assertEqual(len(self.mcp.tools), 28)
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

    def test_argument_descriptions_name_inner_model_keys(self):
        """Clients that show only top-level text must still learn the nested key names."""
        import re

        def field_names(model, seen=()):
            names = []
            for field_name, field in model.model_fields.items():
                names.append(field_name)
                for nested in _models_in(field.annotation):
                    if nested not in seen:
                        names.extend(field_names(nested, seen + (model,)))
            return names

        checked = 0
        for name, function in self.mcp.tools.items():
            hints = get_type_hints(function, include_extras=True)
            for parameter in inspect.signature(function).parameters:
                models = list(_models_in(hints[parameter]))
                if not models:
                    continue
                text = " ".join(
                    item.description
                    for item in getattr(hints[parameter], "__metadata__", ())
                    if getattr(item, "description", None)
                )
                for model in models:
                    for key in field_names(model):
                        checked += 1
                        with self.subTest(tool=name, argument=parameter, key=key):
                            self.assertRegex(
                                text, rf"\b{re.escape(key)}\b",
                                f"{name}.{parameter} description does not name inner key '{key}'",
                            )
        self.assertGreater(checked, 0)

    def test_subagent_and_orchestrator_descriptions_encourage_delegation(self):
        subagent_desc = self.mcp.descriptions["subagent_run"].lower()
        bootstrap_desc = self.mcp.descriptions["orchestrator_bootstrap"].lower()
        self.assertIn("orchestration-first", subagent_desc)
        self.assertIn("delegate", subagent_desc)
        self.assertIn("orchestrator-first", bootstrap_desc)
        self.assertIn("subagent", bootstrap_desc)

    def test_apply_edits_schema_documents_group_expansion(self):
        function = self.mcp.tools["apply_edits"]
        signature = inspect.signature(function)
        self.assertIn("expand_groups", signature.parameters)
        self.assertIn("expand_groups=true", self.mcp.descriptions["apply_edits"])

    def test_fastmcp_registered_apply_edits_schema_accepts_expand_groups(self):
        try:
            from mcp.server.fastmcp import FastMCP
        except ModuleNotFoundError:
            self.skipTest("FastMCP dependency is not installed")

        server = FastMCP("schema-contract")
        apply_edits.register(server)
        registered = server._tool_manager._tools["apply_edits"]
        schema = getattr(registered, "parameters", None) or getattr(registered, "input_schema", {})
        self.assertIn("expand_groups", schema.get("properties", {}))
        self.assertNotIn("expand_groups", schema.get("required", []))

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

    def test_audited_collection_shapes_and_position_keys_are_documented(self):
        env_schema = TypeAdapter(get_type_hints(terminal_exec.terminal_exec, include_extras=True)["env"]).json_schema()
        extensions_schema = TypeAdapter(get_type_hints(code_search.search_text, include_extras=True)["extensions"]).json_schema()
        tags_schema = TypeAdapter(get_type_hints(skills.skills_manage, include_extras=True)["tags"]).json_schema()
        language_schema = TypeAdapter(get_type_hints(code_format_lint.code_format_lint, include_extras=True)["language"]).json_schema()
        self.assertEqual(env_schema["anyOf"][0]["type"], "object")
        self.assertEqual(env_schema["anyOf"][0]["additionalProperties"]["type"], "string")
        self.assertEqual(extensions_schema["anyOf"][0]["items"]["type"], "string")
        self.assertEqual(tags_schema["anyOf"][0]["items"]["type"], "string")
        self.assertIn("text", language_schema["enum"])
        self.assertIn('"MODE": "test"', self.mcp.descriptions["terminal_exec"])
        self.assertIn('[".py", ".md"]', self.mcp.descriptions["search_text"])
        hints = get_type_hints(apply_edits.apply_edits, include_extras=True)
        edit_position = apply_edits.Edit.model_fields["position"].description
        self.assertIn("line", edit_position)
        self.assertIn("col", edit_position)
        self.assertIn("line and col", str(hints["edits"].__metadata__[0].description))

    def test_git_guidance_shows_repo_path_selection(self):
        git_manage._set_inventory(("projects/api", "projects/web"), ())
        self.assertIn("repo_path='projects/api'", git_manage._tool_description())


if __name__ == "__main__":
    unittest.main()
