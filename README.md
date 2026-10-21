# Handy Coder MCP 🛡️

Handy Coder MCP is a Python Model Context Protocol server for coding agents. It exposes workspace file, shell, search, diagnostics, test, and Git tools. File paths and command working directories are confined to the configured root, which is reported in every tool description.

## Requirements and installation

- Python 3.11 or newer

Install from a checkout:

```bash
git clone https://github.com/josemlwdf/handy-coder-mcp.git
cd handy-coder-mcp
python -m pip install .
```

Run against the current directory or select a workspace root:

```bash
handy-coder-mcp
handy-coder-mcp --root /home/user/my-app
handy-coder-mcp --root /home/user/my-app --shell /bin/bash
```

`HANDY_CODER_ROOT` can provide the root when `--root` is omitted. `COMMANDER_ROOT` remains supported as a legacy fallback. `--shell` retains the shell override behavior. The server runs `pwd` from the configured root at startup and includes that result in all tool descriptions; startup stops with a clear error if `pwd` fails.

## MCP client configuration

For VS Code, Kiro, Cursor, Claude Desktop, and other stdio MCP clients, point the server at the installed Python module or checkout:

```json
{
  "mcpServers": {
    "handy-coder-mcp": {
      "command": "python",
      "args": [
        "/path/to/handy-coder-mcp/handy_coder_mcp.py",
        "--root",
        "${workspaceFolder}"
      ]
    }
  }
}
```

Alternatively, use the installed entry point:

```json
{
  "mcpServers": {
    "handy-coder-mcp": {
      "command": "handy-coder-mcp",
      "args": ["--root", "${workspaceFolder}"]
    }
  }
}
```

The server exposes `workspace_read_files`, `workspace_apply_edits`, `terminal_exec`, `code_search_ripgrep`, `workspace_find_files`, `code_get_diagnostics`, `workspace_run_tests`, and `git_manage`.

## Security and limits

- `--root` is the authorization boundary. File paths, read/write targets, and command working directories must resolve inside it; symlink escapes are rejected.
- Shell execution rejects recognizable path escapes, home-directory references, and redirection targets outside the root. As with any shell interface, do not expose this server to untrusted clients.
- File reads are capped at 2 MiB per file. Process output is capped at 1 MiB per stream and command timeouts are bounded to ten minutes (30 seconds by default).
- Use relative paths inside the workspace. Commands such as `npm test`, `git status`, `ls`, and `python scripts/build.py` run with the workspace as their default directory.

`terminal_exec` and `git_manage` can modify the workspace. Review changes with `git_manage` actions `status` and `diff` before committing. No tool pushes commits to a remote.
