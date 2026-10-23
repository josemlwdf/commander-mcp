"""IDE detection and workspace root recovery from IDE settings and storage."""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections import OrderedDict
from pathlib import Path


KNOWN_IDES: dict[str, dict] = {
    "vscode": {
        "display_name": "Visual Studio Code",
        "process_patterns": ("code-server", ".vscode-server", "vscode", "code"),
        "config_dirs": ("Code",),
        "server_dirs": (".vscode-server",),
    },
    "vscode-insiders": {
        "display_name": "Visual Studio Code Insiders",
        "process_patterns": ("code-server-insiders", ".vscode-server-insiders", "code-insiders"),
        "config_dirs": ("Code - Insiders",),
        "server_dirs": (".vscode-server-insiders",),
    },
    "cursor": {
        "display_name": "Cursor",
        "process_patterns": ("cursor-server", ".cursor-server", "cursor"),
        "config_dirs": ("Cursor",),
        "server_dirs": (".cursor-server",),
    },
    "windsurf": {
        "display_name": "Windsurf",
        "process_patterns": ("windsurf-server", ".windsurf-server", "windsurf"),
        "config_dirs": ("Windsurf",),
        "server_dirs": (".windsurf-server",),
    },
    "vscodium": {
        "display_name": "VSCodium",
        "process_patterns": ("codium-server", ".vscodium-server", "codium", "vscodium"),
        "config_dirs": ("VSCodium",),
        "server_dirs": (".vscodium-server",),
    },
}


def _is_vscode_process_dir(path: Path) -> bool:
    """Check whether a path points to an IDE application/process directory."""
    path_str = str(path).lower().replace("\\", "/")
    if (
        "microsoft vs code" in path_str
        or "cursor" in path_str and ("resources" in path_str or "app" in path_str)
        or "windsurf" in path_str and ("resources" in path_str or "app" in path_str)
        or path.name.lower() in ("code", "code - insiders", "cursor", "windsurf", "codium")
    ):
        return True
    vscode_cwd = os.environ.get("VSCODE_CWD", "").strip()
    if vscode_cwd:
        try:
            if path.resolve() == Path(vscode_cwd).resolve():
                return True
        except (OSError, ValueError):
            pass
    return False


def _is_disallowed_default_root(path: Path) -> bool:
    """Check if path is a generic system/user root not suitable as an auto-detected project root."""
    try:
        resolved = path.resolve()
        # Filesystem root e.g. / or C:\
        if resolved == resolved.parent:
            return True
        # VS Code / IDE install directory
        if _is_vscode_process_dir(resolved):
            return True
        # Windows drive root in WSL e.g. /mnt/c
        parts = resolved.parts
        if len(parts) <= 3 and parts[0] == "/" and parts[1] == "mnt":
            return True
        # Windows user profile root in WSL e.g. /mnt/c/Users/<username>
        if len(parts) == 5 and parts[0] == "/" and parts[1] == "mnt" and parts[3].lower() == "users":
            return True
        # Windows user profile root on native Windows e.g. C:\Users\<username>
        if len(parts) == 3 and parts[1].lower() == "users" and len(parts[0]) == 3 and parts[0][1] == ":":
            return True
    except Exception:
        pass
    return False


def detect_ide() -> str | None:
    """Identify the current IDE (e.g. 'vscode', 'cursor', 'windsurf', 'vscode-insiders', 'vscodium')."""
    # 1. Environment variables
    term_prog = os.environ.get("TERM_PROGRAM", "").lower()
    if "cursor" in term_prog:
        return "cursor"
    if "windsurf" in term_prog:
        return "windsurf"
    if "codium" in term_prog:
        return "vscodium"
    if "vscode" in term_prog:
        return "vscode"

    for var_name in os.environ:
        vl = var_name.lower()
        if "cursor" in vl:
            return "cursor"
        if "windsurf" in vl:
            return "windsurf"
        if "vscode" in vl:
            return "vscode"

    # 2. Ancestor process cmdlines
    try:
        pid = os.getpid()
        proc = Path("/proc")
        while pid > 1:
            pid_dir = proc / str(pid)
            if not pid_dir.is_dir():
                break
            try:
                cmd = (pid_dir / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
                for ide_key, info in KNOWN_IDES.items():
                    for pattern in info["process_patterns"]:
                        if pattern in cmd:
                            return ide_key
            except (OSError, PermissionError):
                pass
            try:
                status = (pid_dir / "status").read_text(encoding="utf-8", errors="ignore")
                ppid_match = re.search(r"^PPid:\s+(\d+)", status, re.MULTILINE)
                if not ppid_match:
                    break
                pid = int(ppid_match.group(1))
            except (OSError, PermissionError):
                break
    except Exception:
        pass

    # 3. Running processes in /proc (for Linux/WSL)
    try:
        proc = Path("/proc")
        current_uid = os.getuid() if hasattr(os, "getuid") else None
        scores: dict[str, int] = {k: 0 for k in KNOWN_IDES}
        for pid_entry in proc.iterdir():
            if not pid_entry.name.isdigit():
                continue
            try:
                if current_uid is not None:
                    if pid_entry.stat().st_uid != current_uid:
                        continue
                cmd = (pid_entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
                for ide_key, info in KNOWN_IDES.items():
                    for pattern in info["process_patterns"]:
                        if pattern in cmd:
                            scores[ide_key] += 1
            except (OSError, PermissionError):
                continue

        best_ide, best_score = max(scores.items(), key=lambda item: item[1])
        if best_score > 0:
            return best_ide
    except Exception:
        pass

    # 4. Check active server directories in user home
    home = Path.home()
    for ide_key, info in KNOWN_IDES.items():
        for sdir in info["server_dirs"]:
            if (home / sdir).is_dir():
                return ide_key

    return None


def uri_to_path(uri: str) -> Path | None:
    """Convert an IDE workspace URI (vscode-remote://, file://, etc.) to a local Path."""
    if not uri or not isinstance(uri, str):
        return None
    uri = uri.strip()
    parsed = urllib.parse.urlparse(uri)
    scheme = parsed.scheme.lower()

    path_str: str | None = None
    if scheme in ("vscode-remote", "cursor-remote", "windsurf-remote"):
        # e.g. vscode-remote://wsl%2Bkali-linux/opt/handy-coder_mcp
        path_str = urllib.parse.unquote(parsed.path)
    elif scheme == "file":
        raw_path = urllib.parse.unquote(parsed.path)
        netloc = parsed.netloc.lower()
        if netloc in ("wsl.localhost", "wsl$"):
            # e.g. file://wsl.localhost/kali-linux/home/think/...
            parts = raw_path.strip("/").split("/", 1)
            path_str = "/" + parts[1] if len(parts) > 1 else "/" + parts[0]
        elif len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            # Windows file URI e.g. /c:/Users/josel -> /mnt/c/Users/josel on Linux/WSL
            drive = raw_path[1].lower()
            rest = raw_path[3:]
            path_str = f"/mnt/{drive}{rest}" if os.name != "nt" else f"{drive}:{rest}"
        else:
            path_str = raw_path
    elif not scheme and (uri.startswith("/") or (len(uri) > 2 and uri[1] == ":")):
        path_str = uri

    if not path_str:
        return None

    candidate = Path(path_str).expanduser()
    # If candidate is a .code-workspace file, resolve to the workspace project folder
    if candidate.is_file() and candidate.suffix in (".code-workspace", ".workspace"):
        try:
            with open(candidate, "r", encoding="utf-8") as f:
                ws_data = json.load(f)
            folders = ws_data.get("folders", [])
            if folders and isinstance(folders[0], dict) and "path" in folders[0]:
                sub = candidate.parent / folders[0]["path"]
                if sub.is_dir():
                    return sub.resolve()
        except Exception:
            pass
        return candidate.parent.resolve()

    return candidate.resolve() if candidate.exists() else candidate


def find_ide_user_data_dirs(ide_name: str | None = None) -> list[Path]:
    """Find IDE user data directories (containing storage.json / workspaceStorage)."""
    ides_to_check = [ide_name] if ide_name in KNOWN_IDES else list(KNOWN_IDES.keys())
    user_dirs: list[Path] = []
    seen: set[Path] = set()

    def _add_user_dir(p: Path) -> None:
        try:
            resolved = p.resolve()
            if resolved.is_dir() and resolved not in seen:
                seen.add(resolved)
                user_dirs.append(resolved)
        except OSError:
            pass

    for key in ides_to_check:
        info = KNOWN_IDES[key]

        # 1. Windows AppData via WSL mounts (/mnt/*/Users/*/AppData/Roaming/<Folder>/User)
        for drive_letter in ("c", "d", "e"):
            drive_users = Path(f"/mnt/{drive_letter}/Users")
            if drive_users.is_dir():
                try:
                    for u in drive_users.iterdir():
                        if u.is_dir() and u.name.lower() not in ("public", "default", "default user", "all users"):
                            for cdir in info["config_dirs"]:
                                _add_user_dir(u / "AppData" / "Roaming" / cdir / "User")
                except OSError:
                    pass

        # 2. Native Linux desktop configuration (~/.config/<Folder>/User)
        for cdir in info["config_dirs"]:
            _add_user_dir(Path.home() / ".config" / cdir / "User")

        # 3. WSL / Remote Server data directory (~/.<server_dir>/data/User)
        for sdir in info["server_dirs"]:
            _add_user_dir(Path.home() / sdir / "data" / "User")

    return user_dirs


def recover_workspace_roots(ide_name: str | None = None) -> list[Path]:
    """Recover opened workspace directories from the settings and storage files of the IDE."""
    candidates: OrderedDict[Path, None] = OrderedDict()
    user_dirs = find_ide_user_data_dirs(ide_name)

    for user_dir in user_dirs:
        # A. Inspect globalStorage/storage.json (fastest and most accurate source for active windows)
        storage_json = user_dir / "globalStorage" / "storage.json"
        if storage_json.is_file():
            try:
                with open(storage_json, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # 1. lastActiveWindow has highest priority
                last_win = data.get("windowsState", {}).get("lastActiveWindow")
                if isinstance(last_win, dict):
                    for key in ("folder", "workspace"):
                        if uri := last_win.get(key):
                            if p := uri_to_path(uri):
                                if p.is_dir() and not _is_vscode_process_dir(p):
                                    candidates[p] = None

                # 2. openedWindows
                opened = data.get("windowsState", {}).get("openedWindows", [])
                if isinstance(opened, list):
                    for win in opened:
                        if isinstance(win, dict):
                            for key in ("folder", "workspace"):
                                if uri := win.get(key):
                                    if p := uri_to_path(uri):
                                        if p.is_dir() and not _is_vscode_process_dir(p):
                                            candidates[p] = None

                # 3. backupWorkspaces
                backup = data.get("backupWorkspaces", {})
                for folder_entry in backup.get("folders", []):
                    if isinstance(folder_entry, dict) and (uri := folder_entry.get("folderUri")):
                        if p := uri_to_path(uri):
                            if p.is_dir() and not _is_vscode_process_dir(p):
                                candidates[p] = None
                for ws_entry in backup.get("workspaces", []):
                    if isinstance(ws_entry, dict) and (uri := ws_entry.get("workspaceUri")):
                        if p := uri_to_path(uri):
                            if p.is_dir() and not _is_vscode_process_dir(p):
                                candidates[p] = None
            except Exception:
                pass

        # If candidates already found from storage.json, don't scan full workspaceStorage
        if candidates:
            continue

        # B. Fallback: Inspect workspaceStorage/<hash>/workspace.json if storage.json had no open windows
        ws_storage = user_dir / "workspaceStorage"
        if ws_storage.is_dir():
            try:
                entries: list[tuple[bool, float, Path]] = []
                for item in ws_storage.iterdir():
                    if not item.is_dir():
                        continue
                    ws_json = item / "workspace.json"
                    if not ws_json.is_file():
                        continue

                    # Check for active lockfile
                    has_live_lock = False
                    for lock_name in ("vscode.lock", "cursor.lock", "windsurf.lock"):
                        lock_file = item / lock_name
                        if lock_file.is_file():
                            try:
                                lock_data = json.loads(lock_file.read_text(encoding="utf-8"))
                                pid = lock_data.get("pid")
                                if pid and isinstance(pid, int):
                                    os.kill(pid, 0)
                                    has_live_lock = True
                                    break
                            except Exception:
                                pass

                    mtime = 0.0
                    try:
                        mtime = ws_json.stat().st_mtime
                    except OSError:
                        pass
                    entries.append((has_live_lock, mtime, ws_json))

                # Sort live locks first, then by latest mtime
                entries.sort(key=lambda x: (x[0], x[1]), reverse=True)

                for _, _, ws_json in entries[:15]:
                    try:
                        with open(ws_json, "r", encoding="utf-8") as f:
                            ws_data = json.load(f)
                        for key in ("folder", "workspace"):
                            if uri := ws_data.get(key):
                                if p := uri_to_path(uri):
                                    if p.is_dir() and not _is_vscode_process_dir(p):
                                        candidates[p] = None
                    except Exception:
                        pass
            except OSError:
                pass

    return list(candidates.keys())


def detect_working_dir(
    candidate: str | None = None,
    script_dir: Path | None = None,
) -> str | None:
    """Determine the workspace root directory.

    Order of evaluation:
    1. Explicit candidate from CLI / parameter (if not unexpanded variable and valid)
    2. Explicit environment override HANDY-CODER_ROOT
    3. IDE environment variables (VSCODE_WORKSPACE_FOLDER, etc.)
    4. Current working directory (if not high-risk / system / user home root)
    5. A unique workspace recovered from IDE storage
    6. Script directory fallback
    """
    # 1. Explicit candidate parameter
    if candidate and (candidate.startswith("${") or not candidate.strip()):
        candidate = None

    if candidate:
        p = Path(candidate).expanduser()
        if p.is_dir() and not _is_vscode_process_dir(p):
            return str(p.resolve())

    # 2. Explicit environment override
    if root := os.environ.get("HANDY-CODER_ROOT", "").strip():
        if not root.startswith("${"):
            p = Path(root).expanduser()
            if p.is_dir() and not _is_vscode_process_dir(p):
                return str(p.resolve())

    # 3. Standard IDE workspace environment variables
    for var in (
        "VSCODE_WORKSPACE_FOLDER",
        "WORKSPACE_FOLDER",
        "VSCODE_PROJECT_DIR",
        "CURSOR_PROJECT_DIR",
        "PROJECT_DIR",
    ):
        if root := os.environ.get(var, "").strip():
            if not root.startswith("${"):
                p = Path(root).expanduser()
                if p.is_dir() and not _is_vscode_process_dir(p):
                    return str(p.resolve())

    # 4. Prefer the process working directory. Unlike IDE storage, it belongs to
    # this MCP process and is commonly set by the client's workspace launch config.
    try:
        cwd = Path.cwd().resolve()
        if cwd.is_dir() and not _is_disallowed_default_root(cwd):
            return str(cwd)
    except Exception:
        pass

    # 5. Identify the current IDE and recover working dir from IDE settings.
    # IDE storage is shared by windows, so it cannot associate a candidate with
    # this process when multiple workspaces are open. Use it only when unique.
    ide = detect_ide()
    recovered_roots = recover_workspace_roots(ide)

    if recovered_roots:
        valid_roots = [p for p in recovered_roots if not _is_disallowed_default_root(p)]
        if len(valid_roots) == 1:
            return str(valid_roots[0])
        # Multiple storage candidates have no reliable relationship to this
        # process. Continue to the checkout fallback instead of guessing based
        # on global window recency or workspace-storage ordering.

    # 6. Fall back to script checkout directory
    if script_dir and script_dir.is_dir():
        return str(script_dir.resolve())

    return None
