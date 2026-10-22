# Handy Coder MCP

Handy Coder MCP is a local [Model Context Protocol](https://modelcontextprotocol.io/) server that gives coding assistants tools for working in a project: reading and editing files, searching code, running commands and tests, checking Git, and coordinating delegated tasks.

It runs over standard input/output and uses a configured workspace directory as its file and command boundary. It is written in Python and requires Python 3.11 or newer.

## Install

Clone the repository and install it into a virtual environment:

```sh
git clone https://github.com/josemlwdf/handy-coder-mcp.git
cd handy-coder-mcp
python -m venv .venv
.venv/bin/python -m pip install .
```

On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

## Run

Start the server for the current directory:

```sh
.venv/bin/handy-coder-mcp
```

Or choose a workspace explicitly:

```sh
.venv/bin/handy-coder-mcp --root /path/to/project
```

`--root` sets the workspace used by file tools and as the default command directory. `HANDY_CODER_ROOT` can set the same value when `--root` is omitted. `--shell` optionally selects the shell used by command execution.

## Connect an MCP client

Configure your client to launch the installed command and set its working directory to the project. For example, a VS Code `.vscode/mcp.json` entry:

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

Replace the command path with the installed executable on your machine. Other stdio MCP clients use the same command and argument pattern in their own configuration format.

## What it provides

The server includes tools for workspace file operations, code search, terminal commands, diagnostics, test execution, Git actions, skills, orchestration, and subagents. Optional tools can format or lint code when supported binaries are installed, and fetch text from public web pages.

The available tools and optional dependencies vary by environment. Use `get_capabilities` from your MCP client to see what is available. The full [tool inventory](docs/TOOL_INVENTORY.md) lists registered tools; [optional setup notes](docs/SETUP.md) cover configuration and troubleshooting.

## Security and workspace scope

File operations and command working directories are scoped to the configured workspace. The command tool uses heuristic checks to block common path escapes; this is not operating-system isolation. Only connect the server to workspaces and commands you trust.

## Troubleshooting

If the client cannot connect or times out, confirm it launches the same Python environment where the package was installed and that its workspace path is valid. The repository includes a local stdio smoke check:

```sh
.venv/bin/python scripts/mcp_smoke_check.py --root "$PWD" --cwd "$PWD"
```

## Development

Run the test suite from the repository root:

```sh
.venv/bin/python -m pytest
```
