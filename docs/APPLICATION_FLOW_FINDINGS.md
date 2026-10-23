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
7. **Coordinate larger work.** `orchestrator_bootstrap` writes plan and task files under `.handy-coder/`. The subagent tools run a loop on an available backend (client sampling, a provider key in the environment, a model from the user's Continue config (on by default; `SUBAGENT_CONTINUE=0` disables it), or local Ollama). When none is available, `subagent_run` returns `unavailable`, starts no task and opens no frame, and the parent does the work itself. Cooperative host delegation happens only with an explicit `SUBAGENT_BACKEND=cooperative`. Optional web search and documentation fetching make outbound requests.

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

## Automatic actions performed by the MCP

This section separates actions the server performs as part of normal operation from actions that only happen when the assistant requests a particular tool or option.

### Automatic during server startup

- **Resolve the workspace root.** The server tries the supplied `--root`, environment/configured IDE working directory, then falls back to the server directory. It resolves the path and fails startup if the result is not a directory.
- **Create workspace state.** Startup creates `.handy-coder/` under the selected workspace, even before the assistant requests orchestration or indexing.
- **Log the active boundary.** The resolved root and shell are written to stderr. Home-directory or filesystem-root workspaces also produce a warning because scans may be large.
- **Register and validate tools.** The server registers the tool modules, checks tool argument schemas for unsupported types, and terminates startup if the schema check fails.
- **Index C# files.** The server runs the C# indexer once before accepting MCP calls. The local index is stored at `.handy-coder/code_index.sqlite3`; the indexer skips its configured excluded/generated directories. An indexing error currently terminates startup.
- **Start the stdio server and worker supervisor.** Tool handlers are wrapped so most execute in bounded worker processes. A semaphore limits concurrent workers to eight; the default worker deadline is 30 seconds, with a longer deadline derived from the requested timeout for terminal/build tools. A small set of handlers runs in-process.

### Automatic around tool calls

Every incoming call goes through the supervisor hooks before its handler runs:

- **Enforce an open subagent frame.** While a cooperative subagent frame is active, calls outside that role's allowed tool list are rejected. Nested subagent runs are rejected. The manager sweeps expired tasks/frames when task state is accessed; cooperative frames expire after five minutes.
- **Apply commit-gate checks when configured.** The code supports `off`, `nudge`, and `gate` enforcement modes; the default is `nudge`. After a commit nudge in either enabled mode, every later tool call, including reads, is blocked until a successful `git_manage action='commit'` or a bounded `defer_commit`; while a defer is active, tools are temporarily reopened. Outside an active defer, only commit and defer/status/cancel recovery calls pass. A defer is allowed once per commit cycle for up to 50 successful write calls (default 5); after it expires, the gate applies again. The separate hard-limit gate in `gate` mode also blocks writes that exceed configured limits.
- **Track checks and writes.** Successful test, diagnostics, and lint results are recorded as the latest check status for commit-nudge evaluation. Successful non-dry-run edits and file creates update in-memory write counts, changed paths, and approximate payload size. Failed writes and dry-run edits are excluded.
- **Evaluate commit nudges after writes.** The server evaluates the workspace Git diff after successful writes and can attach a nudge when configured thresholds, a green check, or a module switch is detected. The nudge activates the gate and is attached in the structured `commit_nudge` field (see the checkpoint commit policy in `AGENT_README.md`). By default no subagent task or frame is created. Only when `HANDY_CODER_COMMIT_SUBAGENT=1` does a nudge start a `committer` subagent to inspect `.gitignore` and make a safe commit; it is instructed not to push and to leave unrelated changes untouched, and the tool result carries the task ID/status. If the subagent backend is explicitly cooperative, the host receives a delegated task and must complete that subagent flow. If no backend is available, nothing is started and the nudge falls back to its text-only form.
- **Add subagent reminders/completion metadata.** Calls made in a delegated frame can receive a reminder to complete the frame. A successful commit by a `committer` closes its cooperative frame automatically; other roles, skipped commits, and unsuccessful commits require `subagent_complete` (or cancellation).

### Automatic actions tied to a requested tool

- **`create_file`:** Creates missing parent directories by default. It refuses to overwrite unless `overwrite=true`. HTML files always receive a structural syntax check after saving. Python processing or formatting only runs when its corresponding option is enabled; content formatting defaults off. Successful writes can trigger the commit-nudge hook described above.
- **`git_manage` with `init`:** Initializes Git, creates `test/` and `tmp/`, and maintains `.gitignore` with required `.handy-coder/` and `tmp/` rules plus detected generated-directory rules.
- **`git_manage` with `add`:** Updates `.gitignore` with those rules before staging the requested files. This means an add request can also modify `.gitignore`.
- **`git_manage` with `status` or `log`:** Enriches Git output with parsed untracked-file summaries or structured recent commit metadata.
- **`code_get_diagnostics`:** Inspects project configuration and selects the first configured/available check in its detection order. If an earlier configured binary is missing, it records that and tries the next candidate where possible.
- **`run_tests` and build/check tools:** Start the selected local process with bounded execution and return structured output. The MCP does not automatically run these after every edit; the assistant must request them.
- **`orchestrator_bootstrap`:** Creates `.handy-coder/implementation-plan.md` and `.handy-coder/tasks.md` when explicitly called. Any existing copies of those two files are deleted first, so each call starts from a clean slate; the `overwrite` argument is deprecated and ignored.
- **`skills_manage` save:** Creates the configured skills directory if needed. Saving or deleting skills is explicit; neither happens automatically at startup.
- **C# indexing:** Can also be refreshed explicitly with `index_codebase`; it is not automatically refreshed after each code edit in the described main flow.

### Actions that are not automatic

The server does not automatically run tests or diagnostics after every edit, format files by default, commit or push after every write, invoke web tools, or create orchestration plans. Commit behavior is the exception described above: once the nudge policy triggers, it attaches a commit nudge and blocks non-recovery calls until the agent commits or uses its bounded deferral. The nudge is text-only by default; only when `HANDY_CODER_COMMIT_SUBAGENT=1` does it start a commit subagent, whose result may be a cooperative delegation for the host to execute (explicit cooperative mode only), or `unavailable`, in which case only the text nudge is shown. Push is explicitly excluded from the committer task.

## Evidence reviewed

- `README.md` — installation, client setup, workspace scope, and known VS Code issue.
- `handy_coder_mcp.py` — startup, tool registration, index initialization, and stdio transport.
- `lib/support.py` — root configuration, path checks, and subprocess support.
- `lib/worker_supervisor.py` — worker execution, deadlines, and call hooks.
- `lib/core/system_info.py` and `lib/tool_details.py` — capability and tool documentation endpoints.
- `lib/core/code_navigator.py`, `lib/core/orchestrator_bootstrap.py`, and `lib/subagents/tools.py` — indexing, orchestration, and delegation paths.
- `docs/TOOL_INVENTORY.md` and `docs/DESIGN.md` — documented inventory and architecture context.
