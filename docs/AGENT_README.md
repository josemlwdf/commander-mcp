# Handy Coder MCP 🛡️

Handy Coder MCP is a Python Model Context Protocol server for coding agents. It exposes workspace file, shell, search, diagnostics, test, and Git tools. File paths and command working directories are confined to the configured root, and tool descriptions explain the workspace path rules.

## Requirements and installation

- Python 3.11 or newer

Install from a checkout:

```bash
git clone https://github.com/josemlwdf/handy-coder-mcp.git
cd handy-coder-mcp
python -m venv .venv
.venv/bin/python -m pip install .
```

On Windows, use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.

Run against the current directory or select a workspace root:

```bash
.venv/bin/handy-coder-mcp
.venv/bin/handy-coder-mcp --root /home/user/my-app
.venv/bin/handy-coder-mcp --root /home/user/my-app --shell /bin/bash
```

`HANDY-CODER_ROOT` can provide the root when `--root` is omitted. `--shell` retains the shell override behavior. The server uses the configured root as its working directory and reports the authorized root in every tool description. Only the command-execution tool reports the working directory.

## MCP client configuration

For VS Code, configure the server in the workspace's `.vscode/mcp.json`. Set both `cwd` and `--root` to `${workspaceFolder}` so the server uses the open project rather than the directory where the VS Code process was started:

```json
{
  "servers": {
    "handy-coder-mcp": {
      "type": "stdio",
      "command": "/path/to/handy-coder-mcp/.venv/bin/handy-coder-mcp",
      "args": ["--root", "${workspaceFolder}"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

Replace the command with the installed entry point's path on your system. This configuration sets the authorized root and command working directory to the same open workspace. Use workspace-relative paths with path-taking tools; they resolve from the configured root. The `terminal_exec` description also reports its working directory.

For Kiro, Cursor, Claude Desktop, and other stdio MCP clients, point the server at the installed Python module or checkout:

```json
{
  "mcpServers": {
    "handy-coder-mcp": {
      "command": "/path/to/handy-coder-mcp/.venv/bin/python",
      "args": [
        "/path/to/handy-coder-mcp/handy-coder_mcp.py"
      ]
    }
  }
}
```

The configured Python executable must be the same one used to install the project dependencies. On Windows, use `/path/to/handy-coder-mcp/.venv/Scripts/python.exe` as the command.

Alternatively, use the installed entry point:

```json
{
  "mcpServers": {
    "handy-coder-mcp": {
      "command": "/path/to/handy-coder-mcp/.venv/bin/handy-coder-mcp"
    }
  }
}
```

The server registers `read_files`, `create_file`, `html_syntax_check`, `apply_edits`, `terminal_exec`, `search_text`, `find_files`, `code_get_diagnostics`, `run_tests`, `build_project`, `git_manage`, `defer_commit`, `get_capabilities`, `get_tool_details`, `skills_manage`, `orchestrator_bootstrap`, `orchestrator_update_task_status`, `code_format_lint`, `web_fetch_docs`, `subagent_run`, `subagent_status`, `subagent_result`, `subagent_complete`, and `subagent_cancel`. Call `get_tool_details` with a tool name for its full documentation. See the [registered tool inventory](docs/TOOL_INVENTORY.md) for grouping and notes on unregistered extras.

Tool modules are organized by tier: Tier 1 tools live in `lib/core`, and optional Tier 2 tools live in `lib/extras`.

For optional-feature and C# indexing setup, see [SETUP.md](./SETUP.md). For the canonical registered-tool list, see [TOOL_INVENTORY.md](./TOOL_INVENTORY.md).


### Check a stdio connection

If an MCP client reports request timeouts for every tool, first check that the configured command can start, complete the MCP handshake, list its tools, and answer a harmless read request. From this checkout, run:

```bash
.venv/bin/python test/scripts/mcp_smoke_check.py \
  --command .venv/bin/handy-coder-mcp \
  --root "$PWD" \
  --cwd "$PWD"
```

To check the exact installed command and workspace used by a client, pass them explicitly:

```bash
.venv/bin/python test/scripts/mcp_smoke_check.py \
  --command /path/to/handy-coder-mcp/.venv/bin/handy-coder-mcp \
  --root /path/to/workspace \
  --cwd /path/to/workspace
```

The check initializes the server, lists registered tool names, and calls `read_files` on `README.md`. A successful check means the configured server and stdio transport responded locally; if the client still times out, compare its launch command, arguments, working directory, and Python environment with this invocation, then inspect the client’s server stderr log. The MCP server registers names such as `read_files`; clients may display them with an additional server-name prefix.

### Workspace paths and tool errors

Use workspace-relative paths, which resolve from the configured `--root`. Path operations are strictly validated against that root before any filesystem access occurs. Read and edit responses retain their existing `path` field and include `resolved_path`. `read_files` returns `total_lines`, `applied_start`, and `applied_end` alongside the content.

`apply_edits` validates and stages the entire batch of edits in memory before writing any changes to disk. If any edit in the batch contains an invalid regular expression, fails to match, or encounters a filesystem write failure, all changes are rolled back so no partial edits remain applied. Each edit requires `pattern` and `replacement`; legacy `old_str`/`new_str` aliases are rejected. Matching uses Python regex (`re` module). Replacement text is literal by default, including backslashes; set top-level `expand_groups=true` to interpret Python replacement templates such as `\1` or `\g<name>`. Invalid references return `invalid_replacement`. `replace_all=true` can be bounded per edit with `max_replacements`; `case_insensitive=true` enables case-insensitive matching. A `position` is an object with 1-based `line` and `col` keys (for example, `{"line": 3, "col": 5}`); it starts searching at or after that coordinate and replaces the first match there.

If your client also exposes a separate `multi_edit` helper, use this quick guide:

| Tool | Match mode | Replacement mode | Best for |
|---|---|---|---|
| `multi_edit` (client helper) | Literal substring | Literal substring | Simple exact-text swaps |
| `apply_edits` (this server) | Python `re` regex | Literal by default; regex-template when `expand_groups=true` | Structured/targeted edits with validation and match counts |

`apply_edits` contract highlights:

- Entire edit batches are validated and staged before write; failures leave files unchanged.
- Edits are processed in input order against staged content; repeated or overlapping edits on the same file are order-sensitive.
- `replace_all=false` replaces the first match per edit; `replace_all=true` replaces all matches.
- `replacement` is literal unless `expand_groups=true`, where `\\1` and `\\g<name>` are expanded.
- `max_replacements` limits each edit when `replace_all=true`; `case_insensitive=true` ignores case.
- Errors are structured (`error_code`, `edit_index`, message) and include a unified diff for successful staged edits.

`apply_edits` failure-mode matrix:

| Scenario | Typical `error_code` | Outcome |
|---|---|---|
| Missing target file | `not_found` | Batch stops at that edit index; no files are written. |
| Path escapes workspace root | `path_escape` | Batch stops; no files are written. |
| Target is not a UTF-8 text file | `binary_file` | Batch stops; no files are written. |
| Duplicate/overlapping edits where a later pattern no longer matches staged text | `no_match` | Batch stops at the failing edit; earlier staged changes are discarded (atomic rollback). |
| Invalid regex in `pattern` | `invalid_regex` | Batch stops; no files are written. |
| Invalid group reference/template with `expand_groups=true` | `invalid_replacement` | Batch stops; no files are written. |
| Invalid edit shape (missing/contradictory aliases) | `invalid_edit` | Batch stops; no files are written. |
| Filesystem access/write failure | `io_error` (staging) or raised write failure with rollback | Any writes already performed are reverted to original content. |

`find_files` and `search_text` return successful partial results when a workspace directory cannot be scanned. Their `errors` arrays identify skipped workspace-relative paths and explain the filesystem error. `find_files` supports extension/type and size filters, path/size/modified sorting, and optional size/time metadata. `search_text` supports `extensions` as an array of strings (for example, `[".py", ".md"]`), `case_insensitive`, and optional `context_lines` returning `context_before` and `context_after`; match entries contain the actual 1-based line number. `read_files` supports either `line_ranges` or `start_line`/`end_line` shorthand for one file, a selectable encoding, and optional size/time metadata. `create_file` supports POSIX permissions and an optional `.bak` backup on overwrite. Submitted content is preserved by default; `format_content=true` enables supported formatting and `post_process_python=true` enables Python source processing. HTML writes check the final saved content and suggest organizing web assets under `static/html`, `static/js`, and `static/css` without creating those directories; use `html_syntax_check` to check a file independently.

Unfiltered `run_tests` runs require a recognized test setup (for example, pytest configuration or actual test files); a generic `pyproject.toml` alone is not enough. Supplying both a framework and `filter_path` allows a targeted run without workspace-wide test discovery. `run_tests` parses test outcomes (e.g. `passed`, `failed`, `errors`, `skipped`) into a structured `parsed` metadata dictionary, and for pytest may include `hints`/`preflight_notes` for common import-path issues. If tests import local modules (for example `from lib import support`), adding `test/__init__.py` is recommended for reliable collection across environments.

`code_get_diagnostics` executes recipes and includes the process `exit_code` in its response; `path`, `check`, and `timeout_ms` select its directory, configured check, and execution limit. `terminal_exec` accepts optional `env` as an object whose values are strings (for example, `{"MODE": "test"}`), `stdin`, and per-call `shell`; its `exit_code` is the process status, with 124 indicating timeout. Its terminal escape checks are heuristic lexical checks for recognizable path escapes, not a complete sandbox or OS isolation. `code_format_lint` discloses which formatters and linters were run in `tools_run` and which were missing in `tools_skipped`. `web_fetch_docs` retains its direct HTTP(S) and pagination design and includes `http_status`, `final_url`, and `truncated`; it does not provide browser/editor state.

`build_project` detects Python, Node, and .NET configurations. It asks for `path` when more than one candidate exists, uses a Node lockfile to select the package manager, and returns parsed diagnostics and raw output. `timeout_ms` accepts 1–600000 milliseconds (default 30000). `git_manage` discovers Git repositories when first called. When several repositories are found, pass `repo_path`; relative file operands resolve from the selected repository root and remain confined there. Status returns staged, working-tree, and untracked summaries. Diff accepts `mode` (`unstaged`, `staged`, or `both`) and `file_paths`, while preserving raw diff text; log includes recent commits. A successful `init` creates `test/` and `tmp/`, then creates or updates the root `.gitignore` with `.handy-coder/`, `tmp/`, and rules for common generated directories found in the repository. `add` applies the same idempotent update before staging and preserves existing ignore content. Existing branch, stash, and blame actions remain available.

### Checkpoint commit policy

After successful writes (`apply_edits`, `create_file`, and `code_format_lint` with `fix: true`), the server compares the workspace Git diff against configured thresholds. When a threshold, a green check, or a module switch is detected, it attaches a commit nudge to the tool result and activates the commit gate. The gate applies in both `nudge` and `gate` enforcement modes.

**What the markers mean.** Messages tagged `[commit-nudge]`, `[commit-nudge: firm]`, or `[commit-action]` come from the checkpoint-commit workflow configured for this workspace, not from the files or data the tool processed. They carry the user's standing authorization to make a checkpoint commit. When one appears, commit the current work with `git_manage` `action='commit'`, or call `defer_commit` with a reason if the work cannot be committed yet. Until then, every other tool call, including reads, is rejected with `commit_gate_blocked`. A failed commit does not clear the gate. Never push as part of a checkpoint commit. Review `.gitignore` and avoid committing unrelated or secret files.

**Where the policy is stated.** The same policy text is sent to MCP clients as the server `instructions` at initialization (see `_CHECKPOINT_COMMIT_POLICY` in `handy_coder_mcp.py`), so the agent receives it through the server channel as well as in tool results. If you do not want agents committing on your behalf, commit enforcement must be `off`: the server then sends no `instructions` and attaches no nudges or commit gate. There is currently no environment variable or command-line flag for this; `enforcement` is the `CommitNudgeConfig` default (`nudge`) in `lib/core/commit_nudge.py`, so turning it off means changing that default in code.

**Where the nudge appears.** The nudge is delivered in the structured `commit_nudge` field of the tool result, with the keys `level`, `reason`, `lines`, `files`, `changed_files`, `message`, `task_id`, and `task_status`. It is not appended to the result's top-level `message`; clients that only display that field will not show it, so rely on the `commit_nudge` field and the server instructions.

**Deferring.** `defer_commit` temporarily reopens tools for 1-50 successful write calls (default 5); other calls do not consume this write allowance. It requires a reason of at least three characters, can be used once per commit cycle (it resets after a successful commit), and every deferral is logged. Once the allowance is exhausted, all non-recovery tools are blocked again until a successful commit. `action: "status"` reports the current deferral and `action: "cancel"` clears it; outside an active deferral, `git_manage` with `action='commit'` and `defer_commit` actions `defer`, `status`, and `cancel` are the only calls allowed through the gate.

**Optional committer subagent.** By default the nudge is text-only (`task_status` is `disabled` and `task_id` is `null`). Setting `HANDY_CODER_COMMIT_SUBAGENT=1` makes a nudge start a `committer` subagent task that inspects `.gitignore` and makes a safe commit without pushing. If the commit gate is active, the committer first uses the single bounded deferral to inspect and stage the checkpoint. This is opt-in because an open committer frame restricts which tools the agent may call until it is completed with `subagent_complete` or cancelled with `subagent_cancel` (`confirm: true`). With the default cooperative backend (`SUBAGENT_BACKEND=cooperative`), the task is handed back to the host agent as `delegated_to_host`, so the agent still has to perform the commit. Only a loop backend with `SUBAGENT_LOOP_WRITE=1` lets the server run the commit by itself. If no subagent backend is available, nothing is started and the nudge falls back to its text-only form.

### Skills

`skills_manage` stores and retrieves reusable skills, which are Markdown files with a small frontmatter block (`name`, `description`, `invokable`) in the project's `skills` directory. Set `HANDY-CODER_SKILLS_DIR` to use a different directory. The directory is not tied to `--root`.

- `action: "list"` returns every skill's `name`, `description` and `invokable` flag, plus an `errors` array for files that could not be read.
- `action: "get"` with `name` returns the full `content` and the `body` without frontmatter.
- `action: "save"` with `name`, `description` and `content` creates a skill. The frontmatter is generated, so `content` must be the body only. It fails with `already_exists` unless `overwrite: true` is passed. Pass `invokable: false` for skills that should not be invoked directly. Optional `tags` is an array of strings (for example, `["python", "testing"]`); `tags` and `version` are saved as metadata; `search`, `delete`, and `export` provide discovery, removal, and full Markdown retrieval.

Names must be 1-64 characters of lowercase letters, digits and hyphens, which also prevents path traversal. Failures return an `error_code`: `missing_argument`, `invalid_name`, `invalid_description`, `content_has_frontmatter`, `skill_too_large`, `not_found`, `already_exists`, `binary_file` or `io_error`.

### Subagents

Subagent tools provide a stable five-tool contract for delegated work without requiring any new credentials. `get_capabilities` reports each role's allowed tools, tool-call budget, and result format. `subagent_result` pages results using `offset`, `total_length`, and `next_offset`; `subagent_cancel` requires `confirm: true`. Cooperative frames expire after five minutes. The host completes cooperative tasks with `subagent_complete`; a successful `git_manage` commit by a committer closes its frame automatically, while skipped or unsuccessful commits and other roles still require `subagent_complete`. Streaming and checkpoint/resume are unsupported.

- `subagent_run`: starts a task with a role (`generalist`, `coder`, `committer`, `explorer`, `reviewer`, or `planner`) and returns `completed`, `running`, `delegated_to_host` (only with an explicit `SUBAGENT_BACKEND=cooperative`) or `unavailable`. `unavailable` means no subagent backend exists: nothing is started, no task id is created and no frame is opened, so the parent should do the work itself and not retry.
- `subagent_status`: checks the current task state and usage.
- `subagent_result`: fetches the full or paged result text.
- `subagent_complete`: closes a cooperative delegated frame.
- `subagent_cancel`: cancels a task.

Role tool access is scoped: `generalist`/`explorer` stay read-only, `reviewer` adds diagnostics/test execution, `planner`/`coder` can also use `create_file` + `apply_edits` when the task requires writing files, and `committer` can use `git_manage`. Loop prompts include compact argument examples for the allowed tools. They also expose `subagent_status` and `subagent_result` so a loop can check and retrieve other task results when useful.

Every subagent response includes a `backend` field. Default selection is `auto`, resolved in this order: sampling capability, existing provider key in the current process environment, a model from your Continue config (on by default; `SUBAGENT_CONTINUE=0` turns it off), local Ollama when `SUBAGENT_LOCAL=1`, then no backend (`subagent_run` returns `unavailable`).

Loop-capable backends (`sampling`, `direct:*`, `local:ollama`, `continue:<provider>`) run an internal JSON-action loop and can return `completed` directly from `subagent_run` when `wait: true` and the run finishes quickly; otherwise they return `running` and should be polled with `subagent_status`. The loop accepts JSON actions, text-wrapped Continue `<function_calls>` tool calls, and plain-text final answers. Wrapped tool calls are executed in order up to the role's tool-call budget; extra calls in that response are skipped and the model is asked to return its final answer.

Optional overrides:

- `SUBAGENT_BACKEND=cooperative|sampling|direct|local|continue` chooses the backend family. `continue` forces Continue discovery even when `SUBAGENT_CONTINUE=0`; if no Continue model is usable it resolves to no backend.
- `SUBAGENT_CONTINUE=0` (also `false`, `no` or `off`) stops `auto` selection from reading your Continue config (see below). Unset or any other value leaves discovery on.
- `SUBAGENT_CONTINUE_MODEL` picks a Continue model by `name` or `model` (case-insensitive). An unknown value selects nothing; it never falls back to another model.
- `SUBAGENT_CONTINUE_CONFIG` points at a specific Continue config file and is searched first.
- `SUBAGENT_LOOP_WRITE=1` lets loop backends run roles that can write or commit (`planner`, `coder`, `committer`). Without it `subagent_run` returns `unavailable` for those roles.
- `SUBAGENT_WAIT_SECONDS` controls the synchronous wait budget for `subagent_run` (default 15).
- `SUBAGENT_MODEL_OPENAI`, `SUBAGENT_MODEL_ANTHROPIC`, `SUBAGENT_MODEL_LOCAL` override default model ids per backend.
- `SUBAGENT_SAMPLING_URL` (plus optional `SUBAGENT_SAMPLING_MODEL` and `SUBAGENT_SAMPLING_API_KEY`) enables HTTP sampling in runtimes without a native sampling bridge.

Use subagents for broad searches, independent reviews, and large-input summaries; avoid delegation only for single quick lookups. In practice, prefer an orchestrator-first style while `subagent_run` is available: keep parent context minimal and delegate non-trivial work to subagents. When it returns `unavailable`, do the work yourself in the conversation.

#### Continue backend

By default (unless `SUBAGENT_CONTINUE=0`) the server reuses a chat model from your [Continue](https://continue.dev) config, so you do not configure a second set of credentials. Config files are searched in this order: `SUBAGENT_CONTINUE_CONFIG`, `$CONTINUE_GLOBAL_DIR/config.yaml`, `~/.continue/config.yaml`, `~/.continue/config.json`, `/mnt/c/Users/<profile>/.continue/` for every Windows profile visible under WSL (current user first; `Public`, `Default`, `Default User` and `All Users` are skipped, symlinked profiles are ignored, at most 32 profiles), `%USERPROFILE%\.continue\`, then `<workspace>/.continue/config.yaml`. Files over 1 MiB are skipped.

Only models with a chat role, a key (literal `apiKey`, an `Authorization` header or a `${{ secrets.X }}` reference resolved from the environment or a `.env` next to the config) and a supported provider are used; local providers such as Ollama need no key. The workspace config is untrusted and cannot resolve secret references. Keys are never logged or returned; `get_capabilities` reports only provider, model name and config path. Roles that can write (`planner`, `coder`, `committer`) still need `SUBAGENT_LOOP_WRITE=1`.

**Running in Docker.** A container cannot see your host's Continue directory. Mount it read-only and point `SUBAGENT_CONTINUE_CONFIG` at the mounted file, for example `-v ~/.continue:/continue:ro -e SUBAGENT_CONTINUE_CONFIG=/continue/config.yaml`. Secret references need the matching variables in the container environment or a `.env` file in the mounted directory.

- **Cost:** this spends your own provider credits or hits your own local model. Loops can take several turns per task, so the cost is not one request.
- **Models:** only entries with the `chat` role (or no `roles`) and a supported provider are used: `openai`, `anthropic`, `openrouter`, `deepseek`, `mistral`, `groq`, `together`, `lmstudio`, `ollama`. Hub entries (`uses:`), other providers and remote models without a resolvable key are skipped.
- **Keys:** a literal `apiKey`, an `Authorization` header under `requestOptions.headers`, or a `${{ secrets.NAME }}` reference resolved from the process environment, then from a `.env` next to the config file. Whether Continue itself reads that `.env` is unverified. Anthropic keys are sent as `x-api-key`.
- **Workspace configs are untrusted:** `<workspace>/.continue/config.yaml` can be shipped by a repository, so `${{ secrets.* }}` references in it are never resolved. Literal keys in it still work.
- **Secrets stay out of output:** keys and header values are redacted from error text (backend HTTP errors and failed-task messages), and `get_capabilities` reports only the provider, model name and config path. The text of a completed task's result is the model's own output and is not scrubbed, so a key can appear there if the model obtains it some other way, for example by reading a workspace `.continue/config.yaml` that contains a literal key with `read_files`. Prefer `${{ secrets.NAME }}` references or a config outside the workspace.
- **YAML needs PyYAML:** install it with `pip install .[continue]`. Without it YAML configs are skipped and only JSON configs work.
- **Limits:** loop tool calls run in a server thread with a 60-second deadline and are not isolated in the worker process. `subagent_cancel` stops a task before its next turn or tool call, but an HTTP request already in flight still finishes. A model's `maxTokens` only lowers the per-request limit, never raises it.

### Orchestrator bootstrap

`orchestrator_bootstrap` creates `.handy-coder/implementation-plan.md` and `.handy-coder/tasks.md` from a single high-level request, first deleting any existing copies of those two files (the `overwrite` argument is deprecated and ignored; the result lists removed paths in `deleted_files`). The generated tasks file uses `[ ]` placeholders with explicit status labels (`non_started`, `interrupted`, `complete`, `failed`) so an orchestrator can track progress while repeatedly spawning subagents.

`orchestrator_update_task_status` atomically updates a single `[status: ...]` task line (selected by `line_number` or `task_contains`) without rewriting progress manually.

### Optional Tier 2 integrations

Tier 2 tools are registered without requiring external services. Tools report a clear unsupported/configuration message when their optional backend is unavailable.

- `code_format_lint`: `auto` infers Python and JavaScript/TypeScript from supported file extensions; use `language="auto"` when a language cannot be inferred. `language="text"` is a successful no-op and runs no tools, including with `fix: true`. The tool uses installed Ruff or Black for Python and ESLint for JavaScript/TypeScript. Install `.[javascript-format]` to enable jsbeautifier formatting for `.js`, `.mjs`, and `.cjs` files; it uses two-space indentation. Set `fix: true` to write formatting/fix changes.
- `create_file`: Python files receive automatic processing and formatting during creation; HTML files receive a syntax check.
- When creating a Python file or applying edits to one, review `requirements.txt` and add any new third-party dependencies the Python change introduces. The file-writing tools include this reminder in successful Python write responses.
- `web_fetch_docs`: direct public HTTP(S) fetching is available without credentials. Requests to private, local, or reserved addresses are blocked.

This server does not include the Tier 2 external MCP gateway tool.

## Security and limits

- `--root` is the authorization boundary. Use workspace-relative file paths, which resolve from it. Read and edit responses include a `resolved_path`. Paths are validated lexically before any filesystem access: paths that reference parent directories (`../`), absolute paths, or home-directory expansions that would escape the authorized root are rejected immediately.
- Shell execution in `terminal_exec` applies a heuristic lexical guard that rejects recognizable path escapes, home-directory references (`~`), common `$HOME`/`$PWD`-style escapes, and redirection targets that look like they escape the workspace root. This guard catches common patterns but is not a complete sandbox. Treat `terminal_exec` as trusted-input-only and use operating-system isolation if untrusted command input is possible.
- File reads are capped at 2 MiB per file. Process output is capped at 1 MiB per stream.
- **Worker deadline:** All tool calls execute in an isolated worker subprocess. The default server-side deadline is **30 seconds**. `terminal_exec` may extend its own worker deadline by passing `timeout_ms`; the maximum accepted is 600,000 ms (10 minutes), adding a 5-second grace period over the subprocess deadline. Timed-out workers have their entire process group killed.
- `find_files` and repository discovery impose a 15-second scan time limit and a 5,000-entry ceiling; results are marked `incomplete: true` if either limit is reached.
- Use relative paths inside the workspace. Commands such as `npm test`, `git status`, `ls`, and `python scripts/build.py` run with the workspace as their default directory.

`terminal_exec` and `git_manage` can modify the workspace. Review changes with `git_manage` actions `status` and `diff` before committing, and review `.gitignore` for missing generated artifact rules. `git_manage` can also push to a configured remote when using the `push` action.
