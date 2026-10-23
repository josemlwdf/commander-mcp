"""Keep verbose runtime tool documentation available on demand."""
from __future__ import annotations

import copy
import functools
from typing import Annotated, Any

from pydantic import Field


def _brief(description: str) -> str:
    """Return a compact first sentence suitable for tools/list."""
    text = " ".join((description or "").split())
    end = min((text.find(mark) for mark in (". ", "! ", "? ") if text.find(mark) >= 0), default=-1)
    if end >= 0:
        text = text[:end + 1]
    return text[:160].rstrip()


def _schema(tool: Any) -> dict:
    schema = getattr(tool, "parameters", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    if schema is None:
        return {}
    return copy.deepcopy(schema)


def _strip_descriptions(value: Any) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("description"), str):
            value["description"] = _brief(value["description"])
        for child in value.values():
            _strip_descriptions(child)
    elif isinstance(value, list):
        for child in value:
            _strip_descriptions(child)


def _arguments(schema: dict) -> dict:
    required = set(schema.get("required", []))
    definitions = schema.get("$defs", {})

    def resolve(prop: dict) -> dict:
        ref = prop.get("$ref", "")
        key = ref.rsplit("/", 1)[-1] if ref.startswith("#/$defs/") else None
        if key and key in definitions:
            return {**definitions[key], **prop}
        return prop

    return {
        name: _nested_detail(resolve(prop), name, required, resolve)
        for name, prop in schema.get("properties", {}).items()
    }


def _nested_detail(prop: dict, name: str, required: set[str], resolve=lambda value: value) -> dict:
    prop = resolve(prop)
    item = {
        "type": prop.get("type") or next(
            (key for key in ("anyOf", "oneOf", "allOf") if key in prop),
            prop.get("$ref", "object"),
        ),
        "required": name in required,
        "description": prop.get("description", ""),
    }
    if "default" in prop:
        item["default"] = prop["default"]
    constraints = {k: v for k, v in prop.items() if k not in {"type", "description", "title", "default", "properties", "$defs"}}
    if constraints:
        item["constraints"] = constraints
    if isinstance(prop.get("properties"), dict):
        nested_required = set(prop.get("required", []))
        item["properties"] = {k: _nested_detail(v, k, nested_required, resolve) for k, v in prop["properties"].items()}
    if isinstance(prop.get("items"), dict):
        item["items"] = _nested_detail(prop["items"], "item", set(), resolve)
    return item


def install(mcp: Any) -> None:
    """Wrap FastMCP registration, retaining original details and publishing brief schemas."""
    catalog: dict[str, dict] = {}
    mcp._tool_details_catalog = catalog
    original_tool = mcp.tool

    @functools.wraps(original_tool)
    def tool(*args, description: str | None = None, **kwargs):
        decorator = original_tool(*args, description=_brief(description or ""), **kwargs)

        def register(function):
            registered = decorator(function)
            name = getattr(registered, "__name__", function.__name__)
            manager_tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", {})
            runtime_tool = manager_tools.get(name)
            full_schema = _schema(runtime_tool) if runtime_tool is not None else {}
            catalog[name] = {
                "description": description or "",
                "inputSchema": full_schema,
            }
            if runtime_tool is not None:
                brief_schema = copy.deepcopy(full_schema)
                _strip_descriptions(brief_schema)
                if hasattr(runtime_tool, "parameters"):
                    runtime_tool.parameters = brief_schema
                elif hasattr(runtime_tool, "input_schema"):
                    runtime_tool.input_schema = brief_schema
            return registered
        return register

    mcp.tool = tool

    def update_description(name: str, description: str) -> None:
        if name in catalog:
            catalog[name]["description"] = description
        manager_tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", {})
        runtime_tool = manager_tools.get(name)
        if runtime_tool is not None:
            runtime_tool.description = _brief(description)

    mcp._tool_details_update = update_description

    def get_tool_details(
        tool_name: Annotated[str, Field(description="Name of a tool registered in this server instance.")],
    ) -> dict:
        entry = catalog.get(tool_name)
        if entry is None:
            return {"error": f"Unknown or unavailable tool: {tool_name}"}
        manager_tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", {})
        runtime_tool = manager_tools.get(tool_name)
        if runtime_tool is None:
            return {"error": f"Unknown or unavailable tool: {tool_name}"}
        schema = copy.deepcopy(entry["inputSchema"])
        # Runtime descriptions can change after startup (currently git_manage).
        live_description = getattr(runtime_tool, "description", None)
        description = entry["description"]
        if live_description and live_description != _brief(entry["description"]):
            description = live_description
        return {
            "tool_name": tool_name,
            "description": description,
            "arguments": _arguments(schema),
            "definitions": schema.get("$defs", {}),
        }

    # Register through the original FastMCP method so this meta-tool is not shortened.
    meta_description = "Get full documentation and argument details for a registered tool."
    registered = original_tool(name="get_tool_details", description=meta_description)(get_tool_details)
    meta_tool = getattr(getattr(mcp, "_tool_manager", None), "_tools", {}).get("get_tool_details")
    catalog["get_tool_details"] = {
        "description": meta_description,
        "inputSchema": _schema(meta_tool),
    }
