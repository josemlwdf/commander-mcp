"""Shared workspace, MCP, and subprocess support."""
from __future__ import annotations
import fnmatch, os, re, shlex, subprocess, time, threading, signal
from collections.abc import Iterator
from pathlib import Path

ROOT = os.environ.get("PWD")
WORKING_DIRECTORY = ""
SHELL = os.environ.get("SHELL") or ("powershell.exe" if os.name == "nt" else "/bin/sh")
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_MS = 30_000
# Maximum seconds a tool handler subprocess is allowed to run.
# This constant documents the intended deadline for future worker-subprocess
# isolation; see lib/_worker.py for the run_in_worker implementation.
WORKER_DEADLINE_SECONDS = 30.0

def configure(root: str, shell: str | None = None) -> None:
    global ROOT, WORKING_DIRECTORY, SHELL
    ROOT = Path(root).expanduser().resolve(strict=True)
    if not ROOT.is_dir(): raise ValueError(f"Workspace root is not a directory: {ROOT}")
    WORKING_DIRECTORY = ROOT
    if not WORKING_DIRECTORY: raise RuntimeError(f"'pwd' returned no working directory for {ROOT}")
    if shell: SHELL = shell

def inside(path: str | Path, *, must_exist: bool = False) -> Path:
    p = Path(path).expanduser()
    p = (p if p.is_absolute() else ROOT / p).resolve(strict=must_exist)
    try: p.relative_to(ROOT)
    except ValueError: raise ValueError(f"Path escapes authorized workspace root: {path}")
    return p

def iter_workspace_files(
    *,
    path_glob: str | None = None,
    errors: list[dict[str, str]] | None = None,
) -> Iterator[tuple[Path, str]]:
    """Yield workspace files and report paths that could not be scanned."""
    root = inside(".")
    scan_errors = errors if errors is not None else []
    prefix = _glob_prefix(path_glob)
    pending = [root]

    while pending:
        directory = pending.pop()
        relative_directory = directory.relative_to(root).as_posix()
        directory_parts = directory.relative_to(root).parts
        try:
            with os.scandir(directory) as entries:
                sorted_entries = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            scan_errors.append({
                "path": relative_directory or ".",
                "message": exc.strerror or "Filesystem access failed",
            })
            continue

        child_directories = []
        for entry in sorted_entries:
            relative_path = (Path(relative_directory) / entry.name).as_posix()
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name == ".git":
                        continue
                    child_parts = (*directory_parts, entry.name)
                    if _may_match_glob_scope(child_parts, prefix):
                        child_directories.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=True):
                    continue
                try:
                    resolved = inside(entry.path, must_exist=True)
                except ValueError:
                    scan_errors.append({
                        "path": relative_path,
                        "message": "Skipped a file that resolves outside the authorized workspace",
                    })
                    continue
                except OSError as exc:
                    scan_errors.append({
                        "path": relative_path,
                        "message": exc.strerror or "Filesystem access failed",
                    })
                    continue
                if path_glob and not fnmatch.fnmatch(relative_path, path_glob):
                    continue
                yield resolved, relative_path
            except OSError as exc:
                scan_errors.append({
                    "path": relative_path,
                    "message": exc.strerror or "Filesystem access failed",
                })
        pending.extend(reversed(child_directories))

def _glob_prefix(pattern: str | None) -> tuple[str, ...]:
    if not pattern:
        return ()
    prefix = []
    for part in pattern.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return ()
        if any(character in part for character in "*?["):
            break
        prefix.append(part)
    return tuple(prefix)

def _may_match_glob_scope(path_parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    if not prefix:
        return True
    path_parts = tuple(os.path.normcase(part) for part in path_parts)
    prefix = tuple(os.path.normcase(part) for part in prefix)
    if len(path_parts) <= len(prefix):
        return prefix[:len(path_parts)] == path_parts
    return path_parts[:len(prefix)] == prefix

def run_process(command: str | list[str], *, cwd: str | Path = ROOT, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                shell: bool = False, max_output_bytes: int = MAX_OUTPUT_BYTES) -> dict:
    work = inside(cwd, must_exist=True)
    if not work.is_dir(): raise ValueError(f"Working directory is not a directory: {work}")
    if not 1 <= int(timeout_ms) <= 600_000: raise ValueError("timeout_ms must be between 1 and 600000")
    start = time.monotonic()
    argv = command
    use_shell = shell
    if shell:
        validate_shell_command(command, work)
        if os.name == "nt": argv = [SHELL, "-Command", command]
        else: argv = [SHELL, "-c", command]
        use_shell = False
    captured={"stdout":bytearray(),"stderr":bytearray()}; truncated={"stdout":False,"stderr":False}
    try:
        p=subprocess.Popen(argv,cwd=work,shell=use_shell,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                           env={**os.environ,"CI":"true","DEBIAN_FRONTEND":"noninteractive"},
                           start_new_session=(os.name!="nt"))
    except OSError as exc:
        return {"stdout":"","stderr":f"Failed to start process: {exc}","exit_code":1,"timed_out":False,"duration_ms":round((time.monotonic()-start)*1000)}
    def drain(stream,key):
        while True:
            chunk=stream.read(65536)
            if not chunk: break
            room=max_output_bytes-len(captured[key])
            if room>0: captured[key].extend(chunk[:room])
            if len(chunk)>room: truncated[key]=True
    readers=[threading.Thread(target=drain,args=(p.stdout,"stdout"),daemon=True),threading.Thread(target=drain,args=(p.stderr,"stderr"),daemon=True)]
    for t in readers:t.start()
    timed_out=False
    try: code=p.wait(timeout=timeout_ms/1000)
    except subprocess.TimeoutExpired:
        timed_out=True
        try:
            if os.name=="nt": p.kill()
            else: os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        code=p.wait()
    for t in readers:t.join()
    p.stdout.close()
    p.stderr.close()
    def decode(key): return bytes(captured[key]).decode("utf-8","replace")+("\n...[truncated]" if truncated[key] else "")
    stderr_text=decode("stderr")
    if timed_out: stderr_text+=f"\n[TIMEOUT] Process exceeded {timeout_ms}ms."
    return {"stdout":decode("stdout"), "stderr":stderr_text, "exit_code":124 if timed_out else code, "timed_out":timed_out,
            "duration_ms":round((time.monotonic()-start)*1000)}

def validate_shell_command(command: str, cwd: Path) -> None:
    """Reject obvious shell path escapes, including redirection and nested commands."""
    if not command.strip(): raise ValueError("command cannot be empty")
    if re.search(r"(?:\$\((.*?)\)|`([^`]+)`)", command):
        for m in re.finditer(r"\$\((.*?)\)|`([^`]+)`", command):
            validate_shell_command(m.group(1) or m.group(2), cwd)
    if re.search(r"(?:\$HOME|\$USERPROFILE|%USERPROFILE%|%HOME%|~(?:/|\\|\b))", command, re.I):
        raise ValueError("Command references a home-directory escape outside the authorized workspace")
    for match in re.finditer(r"(?:\d?>|>>|<)\s*([^\s;&|]+)", command):
        target=match.group(1).strip("'\"()")
        if target and target not in ("/dev/null","NUL"):
            try: inside(target if Path(target).is_absolute() else cwd/target)
            except ValueError: raise ValueError(f"Redirection target escapes authorized workspace: {target}")
    try: tokens=shlex.split(command, posix=os.name != "nt")
    except ValueError: raise ValueError("Malformed shell command")
    redir=False
    for token in tokens:
        if token in (">",">>","<","2>","1>","&>",">|"):
            redir=True; continue
        if token in (";","&&","||","|"): redir=False; continue
        if token.startswith("-") and "=" in token: token=token.split("=",1)[1]
        candidate=token
        if redir or candidate.startswith(("./","../","/","~/","\\\\")) or re.match(r"^[A-Za-z]:[\\/]",candidate):
            if candidate.startswith("~"): raise ValueError("Home-directory path escapes the workspace")
            try: inside(candidate if Path(candidate).is_absolute() else cwd / candidate)
            except ValueError: raise ValueError(f"Command path escapes authorized workspace: {candidate}")
        redir=False

def result_text(value: object) -> str:
    if isinstance(value, str): return value
    import json
    return json.dumps(value, ensure_ascii=False, indent=2)

def describe(text: str, *, include_working_directory: bool = False) -> str:
    working_directory = f" Working directory: {WORKING_DIRECTORY}." if include_working_directory else ""
    return (
        f"{text}{working_directory} Use relative paths."
    )
