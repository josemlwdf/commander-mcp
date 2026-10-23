"""Shared structured error handling for MCP tool calls.

FastMCP validates arguments and runs the tool inside ``Tool.run`` and converts every
failure into ``ToolError('Error executing tool X: ...')`` with ``__cause__`` set to the
original exception. That text can contain raw pydantic output (including
``errors.pydantic.dev`` links). ``install`` wraps ``ToolManager.call_tool`` so clients
receive ``{error_code, message}`` instead.
"""
from __future__ import annotations

import functools
import json
import re
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

_URL_NOTE = re.compile(r"\s*For further information visit https?://errors\.pydantic\.dev\S*")
_URL = re.compile(r"https?://errors\.pydantic\.dev\S*")


def _clean(value: Any) -> str:
    """Strip pydantic documentation links from free text."""
    return _URL.sub("", _URL_NOTE.sub("", str(value))).strip()


def _validation_payload(exc: ValidationError) -> dict:
    try:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
    except TypeError:  # very old pydantic
        errors = exc.errors()
    first = errors[0] if errors else {}
    field = ".".join(str(part) for part in first.get("loc", ()))
    reason = _clean(first.get("msg", "Invalid arguments")) or "Invalid arguments"
    payload: dict = {"error_code": "invalid_argument", "message": f"{field}: {reason}" if field else reason}
    if field:
        payload["field"] = field
    if len(errors) > 1:
        payload["additional_errors"] = len(errors) - 1
    return payload


def error_payload(exc: BaseException, tool_name: str | None = None) -> dict:
    """Build a structured, pydantic-free error payload from a FastMCP ToolError."""
    cause = exc.__cause__
    if isinstance(cause, ValidationError):
        payload = _validation_payload(cause)
    elif isinstance(cause, ValueError):
        payload = {"error_code": "invalid_argument", "message": _clean(cause) or "Invalid argument"}
    elif cause is not None:
        payload = {"error_code": "tool_error", "message": _clean(f"{type(cause).__name__}: {cause}")}
    else:
        text = _clean(exc)
        code = "unknown_tool" if text.startswith("Unknown tool") else "tool_error"
        payload = {"error_code": code, "message": text or "Tool call failed"}
    if tool_name:
        payload["tool"] = tool_name
    return payload


def _convert(manager: Any, name: str, payload: dict) -> Any:
    """Shape the payload like a normal result when FastMCP asked for conversion."""
    try:
        tool = manager.get_tool(name)
        if tool is not None:
            return tool.fn_metadata.convert_result(payload)
    except Exception:  # noqa: BLE001 - fall back to plain text content
        pass
    from mcp.types import TextContent

    return [TextContent(type="text", text=json.dumps(payload))]


def install(mcp: Any) -> bool:
    """Wrap ``mcp._tool_manager.call_tool``; a no-op for objects without a tool manager."""
    manager = getattr(mcp, "_tool_manager", None)
    original = getattr(manager, "call_tool", None)
    if manager is None or not callable(original) or getattr(original, "_structured_errors", False):
        return False

    @functools.wraps(original)
    async def call_tool(name, arguments, context=None, convert_result=False):
        try:
            return await original(name, arguments, context=context, convert_result=convert_result)
        except ToolError as exc:
            payload = error_payload(exc, name)
            return _convert(manager, name, payload) if convert_result else payload

    call_tool._structured_errors = True  # type: ignore[attr-defined]
    manager.call_tool = call_tool
    return True
