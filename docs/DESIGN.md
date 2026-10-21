Building a **Model Context Protocol (MCP) server** for a coder assistant requires turning those 20 capabilities into specific JSON-RPC tool declarations, handlers, and security boundaries.

Structuring your tools into **3 implementation tiers** ensures a smooth development roadmap for your MCP server architecture:

---

## 🛠️ MCP Server Schema & Architecture Blueprint

```
                     ┌─────────────────────────────────────────┐
                     │          VS Code / Agent Host           │
                     │  (Copilot, Cline, Cursor, Claude Code)  │
                     └────────────────────┬────────────────────┘
                                          │
                                JSON-RPC 2.0 (stdio or SSE)
                                          │
                     ┌────────────────────▼────────────────────┐
                     │            Your MCP Server              │
                     └─┬──────────────────┬──────────────────┬─┘
                       │                  │                  │
            ┌──────────▼────────┐ ┌───────▼─────────┐ ┌──────▼──────────┐
            │   Core File Ops   │ │    Execution    │ │   Intelligence   │
            │ (fs, git, ripgrep)│ │ (terminal, exec)│ │(LSP, AST, Web)   │
            └───────────────────┘ └─────────────────┘ └────────────────┘

```

---

## Tier 1: Core File & Shell Loop (Build These First)

*These tools form the base capabilities required for your agent to read, edit, run, and commit changes.*

The Python modules for these Tier 1 tools are in `lib/core`; optional Tier 2 tool modules are in `lib/extras`.

### 1. `read_files`

* **MCP Tool Name:** `workspace_read_files`
* **Input Schema:** `{ paths: string[], line_ranges?: { path: string, start: number, end: number }[] }`
* **Implementation:** Read workspace files directly from disk with UTF-8 encoding. Cap maximum file read size (e.g., 2MB) to prevent context window blowouts.

### 2. `edit_files`

* **MCP Tool Name:** `workspace_apply_edits`
* **Input Schema:** `{ edits: Array<{ path: string, old_str?: string, new_str: string, position?: { line: number, col: number } }> }`
* **Implementation:** Perform localized diff updates or search-and-replace patches instead of full-file overwrites. Returns success/failure status with unified diff string.

### 3. `execute_terminal_command`

* **MCP Tool Name:** `terminal_exec`
* **Input Schema:** `{ command: string, cwd?: string, timeout_ms?: number }`
* **Implementation:** Spawn a subshell (`child_process.exec` / `subprocess`). Capture and stream stdout/stderr. Must enforce strict timeout defaults (e.g., 30s) so the tool doesn't hang.

### 4. `search_text_regex`

* **MCP Tool Name:** `code_search_ripgrep`
* **Input Schema:** `{ query: string, is_regex?: boolean, path_glob?: string, max_results?: number }`
* **Implementation:** Execute `rg` (Ripgrep) as a binary subprocess in JSON mode (`--json`) to parse line numbers, matches, and path metadata fast.

### 5. `find_files`

* **MCP Tool Name:** `workspace_find_files`
* **Input Schema:** `{ glob_pattern: string, exclude_pattern?: string }`
* **Implementation:** Use fast glob tools (`fast-glob` in Node or `ignore` library to respect `.gitignore`).

### 6. `get_workspace_diagnostics`

* **MCP Tool Name:** `code_get_diagnostics`
* **Input Schema:** `{ path?: string }`
* **Implementation:** Reads current LSP diagnostic state (syntax errors, missing imports, type mismatches) or parses compiler/linter error output.

### 7. `run_tests`

* **MCP Tool Name:** `workspace_run_tests`
* **Input Schema:** `{ test_framework?: "jest" | "pytest" | "vitest", filter_path?: string }`
* **Implementation:** Triggers test framework CLI commands and formats stdout stack traces into readable JSON structures for the LLM.

### 8. `git_operations`

* **MCP Tool Name:** `git_manage`
* **Input Schema:** `{ action: "status" | "diff" | "commit" | "checkout" | "log", args?: string[] }`
* **Implementation:** Wrap local `git` CLI execution. `diff` and `status` are vital so the agent can self-audit before finishing a task.

---

## Tier 2: Intelligence & Extended Checks

*These tools reduce hallucinations and give the agent context across external tools, codebases, and docs.*

The Python server registers the six local/configurable tools in this tier. They do not require external services at startup; each reports when its optional backend is not configured. `mcp_gateway_integration` is intentionally excluded. See the README for configuration and security boundaries.

### 9. `get_symbol_definitions` (LSP)

* **MCP Tool Name:** `lsp_symbol_lookup`
* **Input Schema:** `{ symbol_name: string, path: string, position: { line: number, character: number } }`
* **Implementation:** Queries connected Language Server Protocol endpoints for "Go to Definition" or "Find References".

### 10. `semantic_code_search`

* **MCP Tool Name:** `vector_search_codebase`
* **Input Schema:** `{ natural_language_query: string, top_k?: number }`
* **Implementation:** Integrates with local vector indexes (e.g., LanceDB / ChromaDB) or embed models to perform conceptual lookups (e.g., "Where is session management initialized?").

### 11. `lint_and_format`

* **MCP Tool Name:** `code_format_lint`
* **Input Schema:** `{ paths: string[], fix?: boolean }`
* **Implementation:** Runs Prettier, ESLint, Ruff, or Black via shell call and returns lint errors or formatted diffs.

### 12. `web_search_and_fetch`

* **MCP Tool Name:** `web_fetch_docs`
* **Input Schema:** `{ url?: string, search_query?: string }`
* **Implementation:** Connect to search APIs (Exa, Brave) or convert raw HTML docs into stripped Markdown using readability parsers.

### 13. `mcp_gateway_integration`

* **MCP Tool Name:** `mcp_external_call`
* **Input Schema:** `{ target_service: "github" | "jira" | "postgres", action: string, payload: object }`
* **Implementation:** Acts as a bridge to delegate specific external tasks (like checking GitHub PRs or executing DB schema migrations) to specialized secondary MCP servers.

### 14. `run_debugger`

* **MCP Tool Name:** `debug_session_step`
* **Input Schema:** `{ action: "start" | "step_over" | "step_into" | "inspect_vars", breakpoint_file?: string, line?: number }`
* **Implementation:** Connects to VS Code's DAP (Debug Adapter Protocol) or standalone runtime debuggers (like `pdb` or `node --inspect`).

### 15. `browser_automation`

* **MCP Tool Name:** `browser_action`
* **Input Schema:** `{ action: "navigate" | "click" | "screenshot" | "get_html", url?: string, selector?: string }`
* **Implementation:** Leverages Playwright or Puppeteer headless instances to render UI, capture screenshots, and inspect DOM states.

---

## Tier 3: Safety & Structuring Tools

*These tools protect the environment and maintain task stability during complex, multi-step runs.*

### 16. `task_plan_tracker`

* **MCP Tool Name:** `planner_update_todos`
* **Input Schema:** `{ tasks: Array<{ id: string, description: string, status: "pending" | "in_progress" | "completed" }> }`
* **Implementation:** Persists a structured TODO/Plan JSON array in local state to help the agent maintain multi-file refactoring contexts across model turns.

### 17. `sandbox_execution`

* **MCP Tool Name:** `sandbox_run`
* **Input Schema:** `{ image: string, command: string, mount_workspace?: boolean }`
* **Implementation:** Runs code commands inside isolated Docker / Dev Containers rather than on host machines.

### 18. `request_human_approval`

* **MCP Tool Name:** `safety_prompt_user`
* **Input Schema:** `{ action_type: "file_delete" | "git_push" | "package_install", description: string }`
* **Implementation:** Returns an MCP elicitation message or pauses execution until the user grants explicit terminal/UI authorization.

### 19. `checkpoint_undo`

* **MCP Tool Name:** `workspace_snapshot`
* **Input Schema:** `{ action: "create" | "restore", snapshot_id?: string }`
* **Implementation:** Uses Git stashes or temporary shadow directories to restore files if an agent edit breaks tests or fails syntax validation.

### 20. `tree_sitter_ast_parse`

* **MCP Tool Name:** `ast_parse_and_query`
* **Input Schema:** `{ path: string, tree_sitter_query: string }`
* **Implementation:** Uses Tree-sitter bindings to extract AST nodes (e.g., extract all class methods, imports, or variable declarations cleanly without relying on regex).

---

## Recommended MCP Server Tech Stack

* **Language:** TypeScript/Node.js (using `@modelcontextprotocol/sdk`) or Python (using `mcp`)
* **Transport:** `stdio` (for local extension setups in VS Code / Cursor) or `Streamable HTTP` (for remote hosted agent runners).
* **Safety Pattern:** Group tool approvals by tier. Auto-approve Tier 1 read/search tools, but set human-in-the-loop triggers for Tier 1 `terminal_exec` and Tier 3 safety tools.