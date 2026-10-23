"""Shared workspace, MCP, and subprocess support."""
from __future__ import annotations
import fnmatch
import os
import re
import shlex
import subprocess
import time
import threading
import signal
from collections.abc import Iterator
from pathlib import Path

ROOT = os.environ.get("PWD")
WORKING_DIRECTORY = ""
SHELL = os.environ.get("SHELL") or ("powershell.exe" if os.name == "nt" else "/bin/sh")
MAX_READ_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
DEFAULT_TIMEOUT_MS = 30_000
# Generated/build/cache directories skipped by default in workspace scans.
GENERATED_DIRS = frozenset({
    "node_modules", "__pycache__", "dist", "build", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage", ".next",
    "target", "out", ".tox", "htmlcov",
})
# Maximum seconds a tool handler subprocess is allowed to run.
# This constant documents the intended deadline for future worker-subprocess
# isolation; see lib/_worker.py for the run_in_worker implementation.
WORKER_DEADLINE_SECONDS = 30.0
HANDY_CODER_STATE_DIRNAME = ".handy-coder"


def setup_required(*, missing_requirements: list[str], remediation_commands: list[str], documentation_url: str, message: str, error_code: str = "setup_required") -> dict:
    """Return the shared, machine-readable response for an unavailable optional tool."""
    return {
        "supported": False,
        "status": "setup_required",
        "error_code": error_code,
        "message": message,
        "missing_requirements": missing_requirements,
        "remediation_commands": remediation_commands,
        "documentation_url": documentation_url,
    }


def ensure_handy_coder_state_dir(root: Path | None = None) -> Path:
    """Ensure the per-workspace orchestration state directory exists."""
    workspace_root = Path(root or ROOT)
    state_dir = workspace_root / HANDY_CODER_STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def configure(root: str, shell: str | None = None) -> None:
    global ROOT, WORKING_DIRECTORY, SHELL
    ROOT = Path(root).expanduser().resolve(strict=True)
    if not ROOT.is_dir():
        raise ValueError(f"Workspace root is not a directory: {ROOT}")
    WORKING_DIRECTORY = ROOT
    if not WORKING_DIRECTORY:
        raise RuntimeError(f"'pwd' returned no working directory for {ROOT}")
    if shell:
        SHELL = shell
    ensure_handy_coder_state_dir(ROOT)
    # Warn when the workspace root is a home directory or drive root — scanning
    # these trees often causes timeouts in large environments.
    _warn_high_risk_root(ROOT)


def _warn_high_risk_root(root: Path) -> None:
    """Emit a startup warning when the workspace is a home dir or drive root."""
    import sys
    # Check for drive/filesystem root (e.g. / or C:\)
    if root == root.parent:
        print(
            f"[handy-coder-mcp] WARNING: workspace root is a filesystem root ({root}). "
            "Workspace-wide scans may time out. Narrow the workspace with --root.",
            file=sys.stderr,
        )
        return
    # Check for home directory
    try:
        home = Path.home().resolve()
        if root == home:
            print(
                f"[handy-coder-mcp] WARNING: workspace root is the user home directory ({root}). "
                "Workspace-wide scans may time out. Narrow the workspace with --root.",
                file=sys.stderr,
            )
    except RuntimeError:
        pass

PATH_ESCAPE_MESSAGE = "Path escapes authorized workspace root"


def path_escape_message(path: object) -> str:
    """Single wording for every workspace path-escape error."""
    return f"{PATH_ESCAPE_MESSAGE}: {path}"


def inside(path: str | Path, *, must_exist: bool = False) -> Path:
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else ROOT / raw
    normalized = Path(os.path.normpath(str(candidate)))
    try:
        normalized.relative_to(ROOT)
    except ValueError:
        raise ValueError(path_escape_message(path))
    p = normalized.resolve(strict=must_exist)
    try:
        p.relative_to(ROOT)
    except ValueError:
        raise ValueError(path_escape_message(path))
    return p

def iter_workspace_files(
    *,
    path_glob: str | None = None,
    errors: list[dict[str, str]] | None = None,
    exclude_dirs: frozenset[str] = frozenset(),
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
                    if entry.name == ".git" or entry.name in exclude_dirs:
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
                    # When the pattern uses **, also accept files that match with
                    # zero intermediate directories (e.g. src/**/*.py → src/foo.py).
                    if "**" not in path_glob:
                        continue
                    collapsed = path_glob.replace("**/", "")
                    if not fnmatch.fnmatch(relative_path, collapsed):
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
                shell: bool = False, max_output_bytes: int = MAX_OUTPUT_BYTES,
                env: dict[str, str] | None = None, stdin: str | None = None, shell_executable: str | None = None) -> dict:
    work = inside(cwd, must_exist=True)
    if not work.is_dir():
        raise ValueError(f"Working directory is not a directory: {work}")
    if not 1 <= int(timeout_ms) <= 600_000:
        raise ValueError("timeout_ms must be between 1 and 600000")
    start = time.monotonic()
    argv = command
    use_shell = shell
    if shell:
        validate_shell_command(command, work)
        selected_shell = shell_executable or SHELL
        if os.name == "nt":
            argv = [selected_shell, "-Command", command]
        else:
            argv = [selected_shell, "-c", command]
        use_shell = False
    captured={"stdout":bytearray(),"stderr":bytearray()}
    truncated={"stdout":False,"stderr":False}
    try:
        p=subprocess.Popen(argv,cwd=work,shell=use_shell,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                           stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                           env={**os.environ,"CI":"true","DEBIAN_FRONTEND":"noninteractive", **(env or {})},
                           start_new_session=(os.name!="nt"))
    except OSError as exc:
        return {"stdout":"","stderr":f"Failed to start process: {exc}","exit_code":1,"timed_out":False,"duration_ms":round((time.monotonic()-start)*1000)}
    def drain(stream,key):
        while True:
            chunk=stream.read(65536)
            if not chunk:
                break
            room=max_output_bytes-len(captured[key])
            if room>0:
                captured[key].extend(chunk[:room])
            if len(chunk)>room:
                truncated[key]=True
    readers=[threading.Thread(target=drain,args=(p.stdout,"stdout"),daemon=True),threading.Thread(target=drain,args=(p.stderr,"stderr"),daemon=True)]
    for t in readers:
        t.start()
    if stdin is not None:
        try:
            p.stdin.write(stdin.encode("utf-8"))
            p.stdin.close()
        except BrokenPipeError:
            pass
    timed_out=False
    try:
        code=p.wait(timeout=timeout_ms/1000)
    except subprocess.TimeoutExpired:
        timed_out=True
        try:
            if os.name=="nt":
                p.kill()
            else:
                os.killpg(p.pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
        code=p.wait()
    for t in readers:
        t.join()
    p.stdout.close()
    p.stderr.close()
    def decode(key): return bytes(captured[key]).decode("utf-8","replace")+("\n...[truncated]" if truncated[key] else "")
    stderr_text=decode("stderr")
    timeout_reason = f"Process exceeded {timeout_ms}ms." if timed_out else None
    if timed_out:
        stderr_text+=f"\n[TIMEOUT] Process exceeded {timeout_ms}ms."
    return {"stdout":decode("stdout"), "stderr":stderr_text, "exit_code":124 if timed_out else code, "timed_out":timed_out,
            "timeout_reason": timeout_reason,
            "duration_ms":round((time.monotonic()-start)*1000)}

def validate_shell_command(command: str, cwd: Path) -> None:
    """Reject obvious shell path escapes, including redirection and nested commands."""
    if not command.strip():
        raise ValueError("command cannot be empty")
    if re.search(r"(?:\$\((.*?)\)|`([^`]+)`)", command):
        for m in re.finditer(r"\$\((.*?)\)|`([^`]+)`", command):
            validate_shell_command(m.group(1) or m.group(2), cwd)
    if _HOME_ESCAPE_RE.search(command) or re.search(r"~(?:/|\\|\b)", command):
        raise ValueError("Command references a home-directory escape outside the authorized workspace")
    try:
        lexer = shlex.shlex(_split_unquoted_newlines(command), posix=os.name != "nt", punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        raise ValueError("Malformed shell command")

    # Check only real shell redirection operators. Quoted text such as `>` is
    # emitted as a regular word by shlex and therefore cannot trigger this guard.
    for index, token in enumerate(tokens):
        if token and all(ch in _SHELL_PUNCTUATION for ch in token) and (">" in token or "<" in token):
            if token[-1] == "&" or token in ("<<", "<<<"):
                continue
            target = tokens[index + 1] if index + 1 < len(tokens) else ""
            if target and not all(ch in _SHELL_PUNCTUATION for ch in target):
                if target not in _SAFE_DEVICES:
                    try:
                        inside(target if Path(target).is_absolute() else cwd / target)
                    except ValueError:
                        raise ValueError(f"Redirection target escapes authorized workspace: {target}")

    at_command = True      # next word is a command name (start, or after ; && || | ( ))
    command_name = ""
    expect_target = False  # previous token was a redirection operator
    expect_script = False  # previous token was `sh -c` style flag
    requires_directory_target = False
    saw_directory_target = False

    def _finish_command() -> None:
        if requires_directory_target and not saw_directory_target:
            raise ValueError(f"'{command_name}' requires an explicit workspace-relative directory target")

    for token in tokens:
        if os.name == "nt":
            token = token.strip("'\"")
        if token and all(ch in _SHELL_PUNCTUATION for ch in token):
            if "<" in token or ">" in token:
                # `>&` / `<&` duplicate descriptors and heredocs take no path target.
                expect_target = token[-1] != "&" and token not in ("<<", "<<<")
            else:
                _finish_command()
                at_command, command_name, expect_target, expect_script = True, "", False, False
                requires_directory_target, saw_directory_target = False, False
            continue
        if expect_target:
            expect_target = False
            _check_shell_token(token, cwd)
            continue
        if expect_script:
            expect_script = False
            validate_shell_command(token, cwd)
            continue
        if at_command:
            if re.match(r"[A-Za-z_]\w*=", token):  # leading VAR=value assignment
                _check_shell_token(token, cwd)
                continue
            at_command = False
            command_name = re.sub(r"\.exe$", "", Path(token.replace("\\", "/")).name.lower())
            requires_directory_target = command_name in _DIR_CHANGE_COMMANDS
            saw_directory_target = False
            _check_shell_token(token, cwd, command_position=True)
            continue
        if command_name in _SHELL_INTERPRETERS and (re.fullmatch(r"-[A-Za-z]*c", token) or token.lower() in ("-command", "/c", "/k")):
            expect_script = True
            continue
        if command_name == "eval":
            validate_shell_command(token, cwd)
            continue
        if command_name in _LITERAL_ARG_COMMANDS:
            continue  # echo/printf arguments are text, not paths (redirections are still checked)
        if token == "/" and command_name in _ROOT_PATH_COMMANDS:
            raise ValueError("Root path '/' is outside the authorized workspace")
        if requires_directory_target:
            if token in ("-", "--") or token.startswith("-"):
                raise ValueError(f"'{command_name} {token}' is not allowed outside a fully isolated shell")
            saw_directory_target = True
        _check_shell_token(token, cwd)

    _finish_command()


_SHELL_PUNCTUATION = frozenset("();<>|&")
_SHELL_INTERPRETERS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "pwsh", "powershell", "cmd"})
_LITERAL_ARG_COMMANDS = frozenset({"echo", "printf"})
_DIR_CHANGE_COMMANDS = frozenset({"cd", "pushd", "popd"})
_ROOT_PATH_COMMANDS = frozenset({"cd", "pushd", "popd", "ls", "find", "cat"})
_SAFE_DEVICES = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "NUL"})
_HOME_ESCAPE_RE = re.compile(r"(?:\$(?:HOME|USERPROFILE)\b|%USERPROFILE%|%HOME%|\$\{(?:HOME|USERPROFILE)(?::-[^}]*)?\})", re.I)
_PWD_PATTERN_RE = re.compile(r"\$(?:PWD|OLDPWD)\b|\$\{(?:PWD|OLDPWD)(?::-[^}]*)?\}", re.I)
_PWD_RE = re.compile(r"\$(?:PWD)\b|\$\{PWD(?::-[^}]*)?\}", re.I)
_OLDPWD_RE = re.compile(r"\$(?:OLDPWD)\b|\$\{OLDPWD(?::-[^}]*)?\}", re.I)
_PARENT_SEGMENT_RE = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")


def _reject_unsafe_pwd_expansion(candidate: str) -> None:
    if not _PWD_PATTERN_RE.search(candidate):
        return
    if _OLDPWD_RE.search(candidate):
        raise ValueError("OLDPWD references may resolve outside the authorized workspace")
    if _PWD_RE.search(candidate) and _PARENT_SEGMENT_RE.search(candidate):
        raise ValueError("PWD parent traversal may resolve outside the authorized workspace")



def _split_unquoted_newlines(command: str) -> str:
    """Turn unquoted newlines into `;` separators and drop backslash-newline continuations."""
    out: list[str] = []
    quote = ""
    escaped = False
    posix = os.name != "nt"
    for ch in command:
        if escaped:
            escaped = False
            if ch in "\r\n":
                out.pop()  # shell removes backslash-newline entirely
                continue
            out.append(ch)
            continue
        if posix and ch == "\\" and quote != "'":
            escaped = True
            out.append(ch)
            continue
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in "\r\n":
            out.append(" ; ")
            continue
        out.append(ch)
    return "".join(out)


def _check_shell_token(token: str, cwd: Path, *, command_position: bool = False) -> None:
    """Reject a shell word (or its value after `=`) that points outside the workspace.

    Explicit path arguments (./x, ../x, /x, C:\\x) get the full symlink-resolving check.
    Command names and other path-like words get a lexical containment check only, so a
    workspace-relative launcher such as `.venv/bin/python` keeps working.
    """
    candidates = [token]
    if "=" in token:
        candidates.append(token.split("=", 1)[1])
    for candidate in candidates:
        if not candidate or candidate in _SAFE_DEVICES:
            continue
        _reject_unsafe_pwd_expansion(candidate)
        if candidate == "/":
            continue  # a lone "/" is often division; command-specific checks handle path commands
        explicit = (candidate == ".." or candidate.startswith(("./", "../", "/", "~", "\\\\"))
                    or re.match(r"[A-Za-z]:[\\/]", candidate))
        if not (explicit or "/" in candidate or "\\" in candidate):
            continue
        if candidate.startswith("~"):
            raise ValueError("Home-directory path escapes the workspace")
        target = candidate if Path(candidate).is_absolute() else cwd / candidate
        try:
            if explicit and not command_position:
                inside(target)
            else:
                Path(os.path.normpath(str(target))).relative_to(ROOT)
        except ValueError:
            raise ValueError(f"Command token '{candidate}' looks like a path outside the authorized workspace. If this is not a path (e.g. a division operator), rewrite the command to avoid ambiguity.")

def result_text(value: object) -> str:
    if isinstance(value, str):
        return value
    import json
    return json.dumps(value, ensure_ascii=False, indent=2)

def describe(text: str, *, include_working_directory: bool = False) -> str:
    working_directory = f" Working directory: {WORKING_DIRECTORY}." if include_working_directory else ""
    return (
        f"{text}{working_directory} Use relative paths."
    )
