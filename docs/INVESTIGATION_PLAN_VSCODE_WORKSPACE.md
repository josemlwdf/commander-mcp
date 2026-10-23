# Investigation plan: wrong workspace with multiple VS Code instances

## Problem

When two VS Code instances are running, Handy Coder MCP can resolve its authorized working directory from the other instance. This can cause file and command tools to operate in a different project than the one containing the MCP client.

## Original resolution path

At startup, `handy_coder_mcp._resolve_root` calls `ide_detector.detect_working_dir`. Root selection currently prefers, in order: the CLI `--root` argument, `HANDY-CODER_ROOT`, selected IDE workspace environment variables, roots recovered from IDE storage, the process current directory, and the server script directory. For recovered roots, `recover_workspace_roots` puts `windowsState.lastActiveWindow` first, then includes other opened windows and backup workspaces. If no root is available from `storage.json`, it considers workspace storage entries ordered by live lock and modification time. The process may therefore see several valid workspaces without a reliable association between the MCP process and its originating VS Code window.

## Investigation steps

1. **Capture a reproducible setup.** Open two VS Code windows on distinct projects, launch the MCP server from each, and record which project each instance expects and which root appears in the server's `[handy-coder-mcp] Authorized root` startup line. Repeat with local VS Code and Remote/WSL if applicable.
2. **Record inputs per launch.** For each server process, capture its PID, parent process chain, `cwd`, relevant environment variables (`VSCODE_WORKSPACE_FOLDER`, `WORKSPACE_FOLDER`, `VSCODE_PROJECT_DIR`, `VSCODE_CWD`, `HANDY-CODER_ROOT`, and `TERM_PROGRAM`), and exact launch arguments. Redact unrelated environment values.
3. **Inspect the candidate data.** Compare those inputs with the ordered paths returned by `recover_workspace_roots`, the `lastActiveWindow` and `openedWindows` fields in VS Code `globalStorage/storage.json`, and matching `workspaceStorage` records and lock files. Check whether both instances share the same user-data directory and whether startup timing changes the result.
4. **Isolate selection behavior.** Use temporary directories and mocked environment/storage data to reproduce the two-workspace case in `test/test_ide_detector.py`. Determine whether the wrong result comes from a missing per-window signal, stale `lastActiveWindow` state, broad process-based IDE detection, or candidate ordering/fallback behavior.
5. **Assess reliable identity signals.** Verify which values are unique to each launched MCP process and stable across supported VS Code launch modes. Prefer explicit client-provided workspace identity or launch configuration over global “last active” state when available; confirm behavior for multi-root workspaces and remote URI paths.
6. **Propose a minimal correction.** Define precedence and fallback behavior so a launch is tied to its own workspace where possible, while preserving explicit `--root` and `HANDY-CODER_ROOT` overrides. Avoid treating global IDE recency as proof of process-to-window ownership.
7. **Validate regression coverage.** Add tests for two simultaneous workspace candidates, stale `lastActiveWindow`, both launch orders, explicit root overrides, and relevant local/remote URI cases. Confirm a single-window launch still resolves as before and the startup log reports the selected root.

## Evidence to collect

- Expected and actual root for both VS Code instances.
- Launch arguments, current directory, parent process chain, and relevant environment values for each MCP process.
- Sanitized storage-derived candidate order, including which source supplied each candidate and its lock/mtime state.
- Whether results vary by local versus remote window, launch order, or timing.

## Completion criteria

The cause is tied to a reproducible signal or ordering behavior; a correction is proposed with explicit precedence and fallback rules; and regression cases demonstrate that two concurrent instances each resolve their own workspace without breaking explicit root configuration.

## Investigation findings (2026-10-23)

### Runtime evidence

- Two Handy Coder MCP processes were present under separate VS Code extension-host launches. Both had the same `cwd` (`/mnt/c/Users/josel`) and neither had any of the inspected per-workspace environment variables (`VSCODE_WORKSPACE_FOLDER`, `WORKSPACE_FOLDER`, `VSCODE_PROJECT_DIR`, `HANDY-CODER_ROOT`, or `TERM_PROGRAM`). The only relevant inherited value observed was `VSCODE_CWD=/mnt/c/Users/josel/AppData/Local/Programs/Microsoft VS Code`, which identifies the editor install directory, not a workspace.
- Both MCP processes were children of `/init`; their parent extension hosts belonged to the same VS Code server process. Their command lines contained no root arguments. Parent process environment reads were blocked by `/proc` permissions, so no further environment values were inferred.
- The remote VS Code user-data directory had no `globalStorage/storage.json`. Its `workspaceStorage` contained two `vscode.lock` files but no `workspace.json` records. This capture therefore could not establish the projects represented by those locks or demonstrate two simultaneous project roots. No local Windows VS Code process was visible in this Linux process namespace.

### Reproduction and cause

The selection defect is reproducible in the resolver: after discovering an IDE process, it used the first root from shared IDE storage, whose order is driven by global `lastActiveWindow`, `openedWindows`, or lock/mtime ordering. None of those signals ties a candidate to a particular MCP process. With two candidates, changing their order changes the old result even when the MCP process has its own valid workspace `cwd`.

### Correction and coverage

Root precedence is now: valid CLI root, `HANDY-CODER_ROOT`, IDE workspace environment, valid process `cwd`, a unique valid IDE-storage root, then the server checkout. Multiple storage candidates are treated as ambiguous and are not selected by recency or lock ordering. This preserves explicit overrides and single-candidate storage recovery while allowing independently configured launches to resolve through their own `cwd`.

Regression cases cover both storage candidate orders with each process cwd, stale `lastActiveWindow`, ambiguous-storage fallback, explicit root precedence, single-candidate recovery, local and remote URI parsing, and `_resolve_root`. A launch with a temporary CLI root printed that exact path in the `Authorized root` startup line. The process then exited because this environment lacks the required `mcp` package, before the server could start.

The live capture did not include two distinct project windows, so the end-to-end two-window behavior is supported by the mocked resolver reproduction rather than claimed as a live reproduction. For VS Code clients, explicitly setting both `args: ["--root", "${workspaceFolder}"]` and `cwd: "${workspaceFolder}"` remains the strongest per-window identity signal.
