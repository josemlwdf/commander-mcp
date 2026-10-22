# Registered tool inventory

This inventory reflects modules registered by `handy_coder_mcp.py`. MCP tool names are declared in each module's `register()` function.

## Core tools

- `read_files`
- `create_file`
- `apply_edits`
- `terminal_exec`
- `search_text`
- `find_files`
- `code_get_diagnostics`
- `run_tests`
- `build_project` — detects and builds Python, Node, or .NET projects with structured diagnostics.
- `git_manage`
- `defer_commit`
- `get_capabilities`
- `skills_manage`
- `orchestrator_bootstrap`
- `orchestrator_update_task_status`

## Optional tools

- `code_format_lint` — uses installed formatter and linter binaries.
- `web_fetch_docs` — fetches public HTTP(S) documentation pages.

## Subagent tools

- `subagent_run`
- `subagent_status`
- `subagent_result`
- `subagent_complete`
- `subagent_cancel`

Optional tools are registered at startup; their capabilities depend on local configuration and installed dependencies. Other modules under `lib/extras` (debugger, browser, LSP lookup, and semantic search) are not currently registered by the main server and therefore are not exposed as tools.
