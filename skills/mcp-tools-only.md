---
name: mcp-tools-only
description: Keep the active session's work within MCP tools; use when the user asks to operate in MCP-only or MCP-tools-only mode.
invokable: true
---

# MCP tools only

For the rest of the active session, carry out user-requested work exclusively through MCP tools. This is a behavioral constraint for the assistant; invoking this skill does not change host or client settings, disable other tools, or enforce a platform-level mode.

## Rules

- Use only tools exposed through an MCP server for every action, including discovery, reading, writing, searching, execution, and verification.
- Do not use shell commands, direct filesystem or editor access, a browser, or other non-MCP tools. Do not switch to those tools when an MCP call fails.
- Prefer the MCP server and workspace the user has selected. If the target is ambiguous, use available MCP discovery tools to identify it; ask a concise clarification only when discovery cannot resolve it.
- Before a write, use MCP tools to inspect the relevant target and preserve unrelated content. Make only changes needed for the user's request.
- Use an MCP tool to verify each material change when a suitable read or status operation exists. Do not run extra checks unrelated to the request.
- If no suitable MCP tool is available, or an MCP tool fails, explain the specific limitation and what MCP capability is missing. Do not silently fall back to a non-MCP tool. Ask the user to make an exception only if continuing would otherwise be impossible.
- Continue honoring the user's scope and safety constraints while using MCP tools. MCP availability does not imply permission to make external, destructive, costly, or irreversible changes.

## Session behavior

Apply this constraint to subsequent work in the current session after the skill is invoked. If the user explicitly asks to leave MCP-only mode, resume normal tool selection. If a new session starts, this skill must be invoked there again; it does not persist or configure the host automatically.
