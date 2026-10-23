# Registered tool inventory

This inventory reflects modules registered by `handy-coder_mcp.py`. MCP tool names are declared in each module's `register()` function.
Descriptions in the MCP tool list are brief. Call `get_tool_details` with a registered tool name to retrieve its full description and structured argument documentation, including nested fields, defaults, and validation constraints. Unknown or unavailable names return an error.

## Core tools

- `read_files`
- `create_file`
- `html_syntax_check` — checks workspace HTML for structural syntax errors and reports line numbers.
- `apply_edits`
- `terminal_exec`
- `search_text`
- `find_files`
- `code_get_diagnostics`
- `run_tests`
- `build_project` — detects and builds Python, Node, or .NET projects with structured diagnostics.
- `git_manage` — successful `init` creates `test/` and `tmp/` and maintains `.gitignore`; `add` updates `.gitignore` before staging.
- `defer_commit`
- `web_search` — searches the public web with DuckDuckGo Lite.
- `index_codebase` — incrementally indexes workspace C# files in `.handy-coder/code_index.sqlite3`.
- `code_navigator` — searches indexed symbols, returns current symbol source, or finds likely syntactic usages.
- `get_capabilities`
- `get_tool_details` — returns full documentation for a currently registered tool.
- `skills_manage`
- `orchestrator_bootstrap`
- `orchestrator_update_task_status`

## Registered tools with optional runtime requirements

- `code_format_lint` — uses available local formatter and linter tools; capabilities vary with installed binaries.
- `web_fetch_docs` — fetches text from public HTTP(S) documentation pages.

## Subagent tools

- `subagent_run`
- `subagent_status`
- `subagent_result`
- `subagent_complete`
- `subagent_cancel`

`index_codebase` runs during server startup and can also be called to refresh the index. The index is local and incremental; see [Local C# code navigation](./SEMANTIC_SEARCH.md) for its behavior and limitations.

`web_search` and `web_fetch_docs` make outbound requests and require network access when called. The C# indexer uses the installed Tree-sitter C# parser and SQLite FTS5. See [optional feature setup](./SETUP.md) for details.

The debugger, browser, LSP lookup, and standalone semantic-search modules under `lib/extras` are not registered by the main server and are not exposed as MCP tools.
