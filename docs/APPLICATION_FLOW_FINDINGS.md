# Application Flow Findings

## Scope

Handy Coder MCP is a local coding-assistant server, not a graphical application. Its primary flow runs between an MCP client (such as VS Code or another agent host), the server process, and a configured project workspace. This review follows the implemented path through startup and representative tool use. It is based on the repository code and documentation; it does not include an interactive client session.

## Main flow

1. **Install and configure.** The user installs the Python package, then configures an MCP client to launch `handy-coder-mcp` over stdio. The client can supply `--root` (or `HANDY-CODER_ROOT`) and optionally a shell. If root is omitted, IDE/work-directory detection is attempted, then the server's script directory is the fallback.
2. **Server startup.** The server resolves and validates the workspace directory, creates `.handy-coder/`, configures the shell, and logs the authorized root to stderr. It registers the tools and checks that their JSON schemas use supported argument types. It then indexes C# files and starts the stdio MCP server. A startup indexing error currently aborts startup.
3. **Discover tools.** Once connected, the assistant can call `get_capabilities` to see registered tools and optional runtime dependencies. `get_tool_details` supplies full argument documentation for a selected tool; ordinary tool descriptions and schema descriptions are shortened to keep discovery responses compact.
4. **Inspect and navigate.** Typical entry points are `find_files`, `search_text`, and `read_files`. For C#, `code_navigator` searches the local index, retrieves current symbol source, or finds likely syntactic references. File operations are workspace-relative, with reads capped at 2 MiB per file.
5. **Make a change.** `apply_edits` modifies existing content; `create_file` creates files and refuses to overwrite an existing path unless asked. The server checks resolved paths against the configured root. Tool handlers generally run in worker processes with a 30 second deadline. The supervisor also checks subagent tool scope and commit-nudge rules around calls.
6. **Check the result.** The assistant can run `code_get_diagnostics`, `run_tests`, `build_project`, and `html_syntax_check`, depending on the project and installed tools. `git_manage` provides repository operations. Capabilities indicate optional binaries such as Git, pytest, Ruff, Black, ESLint, and jsbeautifier.
7. **Coordinate larger work.** `orchestrator_bootstrap` writes plan and task files under `.handy-coder/`. The subagent tools support cooperative host delegation or a configured backend. Optional web search and documentation fetching make outbound requests.

## Observations

- **Clear operational boundary:** The workspace root is configured centrally and path resolution checks both lexical and resolved paths, which helps prevent `..` and symlink escapes for file tools. Terminal command safety is explicitly described as heuristic, not OS isolation.
- **Good discovery layering:** Capability status and on-demand full tool details help an assistant choose tools without loading every long description at once.
- **Mixed startup strictness:** C# indexing is documented as an optional navigation feature, but its startup failure is fatal in `main()`. This can prevent unrelated tools from being used if the indexer or its dependencies fail.
- **IDE root selection is a critical branch:** The README records a known issue where multiple VS Code instances can cause the wrong working directory to be selected. Since this root governs file access and command working directories, client configuration with an explicit `--root` is the clearest route.
- **Optional availability is not uniform:** Some tools are always registered but may report unavailable runtime support; others are registered only in the main inventory and their capabilities are environment-dependent. Calling `get_capabilities` first reduces dead ends.
- **Validation is a user/assistant-driven loop:** Tools report results, but the code does not impose a universal sequence requiring diagnostics or tests after each edit. The client or assistant has to choose and interpret checks.
- **The architecture docs are partly aspirational:** `docs/DESIGN.md` describes proposed tiers and includes legacy/placeholder concepts. `docs/TOOL_INVENTORY.md` and the actual registration in `handy_coder_mcp.py` are better guides to the shipped flow.

## Suggested flow improvements

1. Make C# index startup non-fatal, or make indexing explicitly configurable, so failure of an auxiliary navigation feature does not block core file and terminal tools.
2. Surface the resolved root prominently in the client onboarding flow and encourage explicit `--root` configuration, especially for multiple IDE instances.
3. Keep the tool inventory synchronized with registered tools and label which capabilities depend on installed binaries, environment variables, or MCP client features.
4. Consider a concise end-to-end setup walkthrough that includes connection verification with `get_capabilities`, a workspace read, and a simple check.

## Evidence reviewed

- `README.md` — installation, client setup, workspace scope, and known VS Code issue.
- `handy_coder_mcp.py` — startup, tool registration, index initialization, and stdio transport.
- `lib/support.py` — root configuration, path checks, and subprocess support.
- `lib/worker_supervisor.py` — worker execution, deadlines, and call hooks.
- `lib/core/system_info.py` and `lib/tool_details.py` — capability and tool documentation endpoints.
- `lib/core/code_navigator.py`, `lib/core/orchestrator_bootstrap.py`, and `lib/subagents/tools.py` — indexing, orchestration, and delegation paths.
- `docs/TOOL_INVENTORY.md` and `docs/DESIGN.md` — documented inventory and architecture context.
