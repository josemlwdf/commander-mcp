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

The server exposes `workspace_read_files`, `workspace_apply_edits`, `terminal_exec`, `code_search_ripgrep`, `workspace_find_files`, `code_get_diagnostics`, `workspace_run_tests`, `git_manage`, `lsp_symbol_lookup`, `vector_search_codebase`, `code_format_lint`, `web_fetch_docs`, `debug_session_step`, and `browser_action`.

Tool modules are organized by tier: Tier 1 tools live in `lib/core`, and optional Tier 2 tools live in `lib/extras`.

### Workspace paths and tool errors

Use workspace-relative paths, which resolve from the configured `--root`. Path operations are confined to that root. Read and edit responses retain their existing `path` field and include `resolved_path`.

`workspace_find_files` and `code_search_ripgrep` return successful partial results when a workspace directory cannot be scanned. Their `errors` arrays identify skipped workspace-relative paths and explain the filesystem error. Search `path_glob` patterns are used to limit traversal when their directory prefix is statically known.

Unfiltered `workspace_run_tests` runs require a recognized test setup (for example, pytest configuration or actual test files); a generic `pyproject.toml` alone is not enough. Supplying both a framework and `filter_path` allows a targeted run without workspace-wide test discovery.

`git_manage` discovers Git repositories when first called, so connecting the MCP server does not require scanning the configured workspace. If the configured workspace root is a repository, it reports that root without searching for nested repositories; otherwise it searches descendant directories without following symlinks or entering `.git` metadata. When several repositories are found, pass the listed workspace-relative path as `repo_path` on every action. With one repository, `repo_path` is optional. If none is found, the tool description instructs the agent to warn the user and ask before initializing; `action: "init"` initializes the configured workspace root only when no repository is found, then updates the tool description. `git_manage` reports `git_unavailable`, `not_a_git_repository`, `git_repository_check_failed`, or `repository_discovery_incomplete` in `error_code` for those failure cases.

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

- `--root` is the authorization boundary. Use workspace-relative file paths, which resolve from it. Read and edit responses include a `resolved_path`.
- Shell execution rejects recognizable path escapes, home-directory references, and redirection targets outside the root. As with any shell interface, do not expose this server to untrusted clients.
- File reads are capped at 2 MiB per file. Process output is capped at 1 MiB per stream and command timeouts are bounded to ten minutes (30 seconds by default).
- Use relative paths inside the workspace. Commands such as `npm test`, `git status`, `ls`, and `python scripts/build.py` run with the workspace as their default directory.

`terminal_exec` and `git_manage` can modify the workspace. Review changes with `git_manage` actions `status` and `diff` before committing. No tool pushes commits to a remote.
