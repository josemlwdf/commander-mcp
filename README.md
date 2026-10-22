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

`HANDY_CODER_ROOT` can provide the root when `--root` is omitted. `--shell` retains the shell override behavior. The server uses the configured root as its working directory and reports the authorized root in every tool description. Only the command-execution tool reports the working directory.

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
        "/path/to/handy-coder-mcp/handy_coder_mcp.py"
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

The server registers `read_files`, `apply_edits`, `terminal_exec`, `code_search_ripgrep`, `find_files`, `code_get_diagnostics`, `run_tests`, `git_manage`, `lsp_symbol_lookup`, `vector_search_codebase`, `code_format_lint`, `web_fetch_docs`, `debug_session_step`, `browser_action`, `get_capabilities`, and `skills_manage`.

Tool modules are organized by tier: Tier 1 tools live in `lib/core`, and optional Tier 2 tools live in `lib/extras`.

### Check a stdio connection

If an MCP client reports request timeouts for every tool, first check that the configured command can start, complete the MCP handshake, list its tools, and answer a harmless read request. From this checkout, run:

```bash
.venv/bin/python scripts/mcp_smoke_check.py --root "$PWD" --cwd "$PWD"
```

To check the exact installed command and workspace used by a client, pass them explicitly:

```bash
.venv/bin/python scripts/mcp_smoke_check.py \
  --command /path/to/handy-coder-mcp/.venv/bin/handy-coder-mcp \
  --root /path/to/workspace \
  --cwd /path/to/workspace
```

The check initializes the server, lists registered tool names, and calls `read_files` on `README.md`. A successful check means the configured server and stdio transport responded locally; if the client still times out, compare its launch command, arguments, working directory, and Python environment with this invocation, then inspect the client’s server stderr log. The MCP server registers names such as `read_files`; clients may display them with an additional server-name prefix.

### Workspace paths and tool errors

Use workspace-relative paths, which resolve from the configured `--root`. Path operations are strictly validated against that root before any filesystem access occurs. Read and edit responses retain their existing `path` field and include `resolved_path`. `read_files` returns `total_lines`, `applied_start`, and `applied_end` alongside the content.

`apply_edits` validates and stages the entire batch of edits in memory before writing any changes to disk. If any edit in the batch contains an invalid regular expression, fails to match, or encounters a filesystem write failure, all changes are rolled back so no partial edits remain applied. The `old_str` parameter is a Python regular expression pattern used to locate text, while `new_str` is treated as literal replacement text (escape sequences and backreferences are not interpreted).

`find_files` and `code_search_ripgrep` return successful partial results when a workspace directory cannot be scanned. Their `errors` arrays identify skipped workspace-relative paths and explain the filesystem error. `find_files` and repository discovery enforce time limits (15 seconds) and entry limits (5,000 directories/results), excluding common generated directories by default (`node_modules`, `__pycache__`, `.venv`, `dist`, `build`, etc.), and set `incomplete: true` if limits are hit. `code_search_ripgrep` returns a structured `{"error_code": "invalid_regex", ...}` if given an invalid regular expression, and supports optional `context_lines` returning `context_before` and `context_after`.

Unfiltered `run_tests` runs require a recognized test setup (for example, pytest configuration or actual test files); a generic `pyproject.toml` alone is not enough. Supplying both a framework and `filter_path` allows a targeted run without workspace-wide test discovery. `run_tests` parses test outcomes (e.g. `passed`, `failed`, `errors`, `skipped`) into a structured `parsed` metadata dictionary.

`code_get_diagnostics` executes recipes and includes the process `exit_code` in its response. `code_format_lint` discloses which formatters and linters were run in `tools_run` and which were missing in `tools_skipped`. `web_fetch_docs` includes `http_status`, `final_url`, and a `truncated` boolean indicator in its response.

`git_manage` discovers Git repositories when first called, so connecting the MCP server does not require scanning the configured workspace. If the configured workspace root is a repository, it reports that root without searching for nested repositories; otherwise it searches descendant directories without following symlinks or entering `.git` metadata. When several repositories are found, pass the listed workspace-relative path as `repo_path` on every action. With one repository, `repo_path` is optional. If none is found, the tool description instructs the agent to warn the user and ask before initializing; `action: "init"` initializes the configured workspace root only when no repository is found, then dynamically updates the tool description advertised to the MCP client. `git_manage` reports `git_unavailable`, `not_a_git_repository`, `git_repository_check_failed`, or `repository_discovery_incomplete` in `error_code` for those failure cases.

### Skills

`skills_manage` stores and retrieves reusable skills, which are Markdown files with a small frontmatter block (`name`, `description`, `invokable`) in the project's `skills` directory. Set `HANDY_CODER_SKILLS_DIR` to use a different directory. The directory is not tied to `--root`.

- `action: "list"` returns every skill's `name`, `description` and `invokable` flag, plus an `errors` array for files that could not be read.
- `action: "get"` with `name` returns the full `content` and the `body` without frontmatter.
- `action: "save"` with `name`, `description` and `content` creates a skill. The frontmatter is generated, so `content` must be the body only. It fails with `already_exists` unless `overwrite: true` is passed. Pass `invokable: false` for skills that should not be invoked directly.

Names must be 1-64 characters of lowercase letters, digits and hyphens, which also prevents path traversal. Failures return an `error_code`: `missing_argument`, `invalid_name`, `invalid_description`, `content_has_frontmatter`, `skill_too_large`, `not_found`, `already_exists`, `binary_file` or `io_error`.

### Optional Tier 2 integrations

Tier 2 tools are registered without requiring external services. Tools report a clear unsupported/configuration message when their optional backend is unavailable.

- `lsp_symbol_lookup`: set `HANDY_CODER_LSP_COMMAND` to a local language-server executable and arguments, such as `pylsp`. The command is launched without a shell.
- `vector_search_codebase`: install `sentence-transformers` separately and set `HANDY_CODER_EMBEDDING_MODEL` to a model already present on disk. Model downloads are disabled.
- `code_format_lint`: uses installed Ruff or Black for Python and installed ESLint and/or Prettier for JavaScript/TypeScript. Project `node_modules/.bin` tools are recognized. Set `fix: true` to write formatting/fix changes.
- `web_fetch_docs`: direct public HTTP(S) fetching is available without credentials. Requests to private, local, or reserved addresses are blocked. Web search requires `HANDY_CODER_SEARCH_URL`, a trusted JSON endpoint accepting a `q` query parameter and returning `{"results":[{"title":"...","url":"...","snippet":"..."}]}`.
- `debug_session_step`: connects only to a local DAP adapter. Set `HANDY_CODER_DAP_PORT`, optionally `HANDY_CODER_DAP_HOST` (default `127.0.0.1`), `HANDY_CODER_DAP_ADAPTER_ID`, and `HANDY_CODER_DAP_START_REQUEST` to a JSON object with a `command` (`launch` or `attach`) and an `arguments` object. Use `action: "stop"` to close a session.
- `browser_action`: requires the optional Playwright Python package and an installed Chromium browser. HTTP(S) pages are supported; localhost is permitted for local development, and private/reserved network requests are blocked.

This server does not include the Tier 2 external MCP gateway tool.

## Security and limits

- `--root` is the authorization boundary. Use workspace-relative file paths, which resolve from it. Read and edit responses include a `resolved_path`. Paths are validated lexically before any filesystem access: paths that reference parent directories (`../`), absolute paths, or home-directory expansions that would escape the authorized root are rejected immediately.
- Shell execution in `terminal_exec` applies a heuristic lexical guard that rejects recognizable path escapes, home-directory references (`~`), and redirection targets that look like they escape the workspace root. This guard catches common patterns but is not a complete sandbox; it does not interpret shell variables or complex quoting. Avoid exposing this server to untrusted clients.
- File reads are capped at 2 MiB per file. Process output is capped at 1 MiB per stream.
- **Worker deadline:** All tool calls (except `debug_session_step`) execute in an isolated worker subprocess. The default server-side deadline is **30 seconds**. `terminal_exec` may extend its own worker deadline by passing `timeout_ms`; the maximum accepted is 600,000 ms (10 minutes), adding a 5-second grace period over the subprocess deadline. Timed-out workers have their entire process group killed. `debug_session_step` runs in a long-lived supervised subprocess that preserves DAP session state across calls, with a 30-second deadline per action.
- `find_files` and repository discovery impose a 15-second scan time limit and a 5,000-entry ceiling; results are marked `incomplete: true` if either limit is reached.
- Use relative paths inside the workspace. Commands such as `npm test`, `git status`, `ls`, and `python scripts/build.py` run with the workspace as their default directory.

`terminal_exec` and `git_manage` can modify the workspace. Review changes with `git_manage` actions `status` and `diff` before committing. No tool pushes commits to a remote.
