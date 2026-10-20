# Commander mcp 🛡️

A secure, context-confined Model Context Protocol (MCP) server that empowers AI coding agents (in **VS Code**, **Kiro**, Cursor, Claude Desktop, etc.) to execute system commands strictly within the authorized project directory.

---

## Key Features

- **Context Confinement & Jailbreak Prevention**:
  - Automatically restricts execution `cwd` to the authorized directory or validated subdirectories.
  - Lexical and path inspection blocks out-of-context references (e.g. `../` escapes, `/etc/passwd`, `C:\Windows`, user home directories `~`, `$HOME`, `%USERPROFILE%`).
  - Redirection target inspection (blocks `> /tmp/out.txt` or `> ../escape.txt`).
  - Subshell inspection (`$(...)` and backticks).
  - Returns clear, actionable security warning messages directly to the agent when an attempt is blocked.
- **Dynamic OS-Aware Agent Guidance**:
  - Injects runtime environment metadata dynamically into the MCP tool definition (`tools/list`).
  - Alerts the agent whether it is targeting **Windows** (PowerShell cmdlets, `$env:VAR`, `\`), **Linux** (bash, `/`), or **macOS** (zsh/bash, `/`).
  - Informs the agent of the exact authorized workspace root and path rules.
- **Cross-Platform Compatibility**:
  - Full support for Windows (PowerShell / `cmd.exe`), Linux (`bash`/`sh`), and macOS (`zsh`/`bash`).
  - Handles Windows drive letters (`C:\`), UNC paths, and case-insensitivity.
- **Process Protection**:
  - Configurable timeouts (default: 60s) with clean process tree termination (`taskkill /T /F` on Windows, `SIGTERM`/`SIGKILL` on POSIX).
  - Non-interactive defaults (`CI=true`, `DEBIAN_FRONTEND=noninteractive`).
  - Output buffering caps (1 MB limit) to prevent memory exhaustion and client crashes.

---

## Tool Exposed to the Agent

### `execute_command`
Executes a system shell command strictly confined within the authorized project directory.

- **Parameters**:
  - `command` *(string, required)*: The shell command to execute.
  - `subdir` *(string, optional)*: Relative subdirectory inside the workspace to execute the command in.
  - `timeout_ms` *(number, optional)*: Execution timeout in milliseconds (default: `60000`).

---

## Installation & Setup

### Prerequisites
- Node.js >= 18.0.0

### Option A: Local Build
Clone or place this repository on your machine:
```bash
git clone <repo-url> commander_mcp
cd commander_mcp
npm install
npm run build
```

---

## IDE & Client Configuration

### 1. VS Code (Cline / Roo Code / Claude Dev)

Add to your MCP settings file (`claude_desktop_config.json` or Cline's `settings.json`):

```json
{
  "mcpServers": {
    "commander": {
      "command": "node",
      "args": [
        "/path/to/commander_mcp/dist/index.js",
        "--root",
        "${workspaceFolder}"
      ]
    }
  }
}
```

#### Windows Example (VS Code / Cline):
```json
{
  "mcpServers": {
    "commander": {
      "command": "node",
      "args": [
        "C:\\tools\\commander_mcp\\dist\\index.js",
        "--root",
        "${workspaceFolder}"
      ]
    }
  }
}
```

---

### 2. Kiro AI Editor

In Kiro's MCP configuration (`.kiro/mcp.json` or Settings > MCP):

```json
{
  "mcpServers": {
    "commander": {
      "command": "node",
      "args": [
        "/path/to/commander_mcp/dist/index.js",
        "--root",
        "${workspaceFolder}"
      ]
    }
  }
}
```

---

### 3. Global / Direct CLI Usage

You can also run Commander mcp directly against any folder:

```bash
# Run confined to current directory:
commander-mcp

# Run confined to a specific project root:
commander-mcp --root /home/user/my-app

# Run with custom shell override:
commander-mcp --root /home/user/my-app --shell /bin/bash
```

---

## Security Behavior Examples

### Allowed Commands
- `npm test`
- `git status`
- `ls -la` / `dir`
- `cat src/index.ts`
- `python scripts/build.py`
- `echo "sample" > ./test.txt`

### Blocked Commands (Agent receives corrective alert)
| Command | Reason Blocked |
|---|---|
| `cat /etc/passwd` | Absolute path outside workspace root |
| `type C:\Windows\System32\drivers\etc\hosts` | Absolute Windows system path outside workspace root |
| `cat ../secret.txt` | Path traversal escapes workspace root |
| `echo "leak" > /tmp/out.txt` | Redirection target points outside workspace root |
| `cat ~/.ssh/id_rsa` | Home directory escape outside workspace root |
| `echo $(cat /etc/shadow)` | Subshell command accesses out-of-context file |
| `dir %USERPROFILE%` | Environment variable points outside workspace root |

When a blocked command is received, the tool responds with:
```
[SECURITY ALERT - COMMAND BLOCKED]
The command was rejected because it attempts to access files or directories outside your authorized context.
Authorized Workspace Root: /path/to/project
Current Effective Directory: /path/to/project
Detected Security Violations:
  - Path argument '/etc/passwd' points outside the authorized workspace root.

Remediation:
  - Do not use parent directory traversals ('../') that leave the workspace.
  - Do not reference absolute paths like '/etc', '/var', '/tmp', 'C:\Windows', or user homes ('~').
  - Keep all file reads, writes, and script targets strictly relative to or inside '/path/to/project'.
```

---

## Testing

Run the automated test suite:
```bash
npm test
```

This verifies:
- POSIX & Windows path confinement algorithms
- Subshell and redirection breakout detection
- Environment variable sanitization
- Process runner timeout & exit code capture
- End-to-end MCP client/server JSON-RPC communication

