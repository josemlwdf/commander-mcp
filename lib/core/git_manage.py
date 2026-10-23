from pathlib import Path
import os
import re
import shutil
import stat
import time
from typing import Annotated, Any, Literal

from pydantic import Field

from ..support import inside, run_process, describe, path_escape_message


_REPOSITORIES: tuple[str, ...] | None = None
_DISCOVERY_ERRORS: tuple[dict[str, str], ...] = ()
_DISCOVERY_ROOT: Path | None = None
_MCP: Any = None

_DEFAULT_EXCLUDE_DIRS = frozenset({
    "node_modules", "__pycache__", "dist", "build", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage", ".next",
    "target", "out", ".tox", "htmlcov",
})
MAX_DISCOVERY_DIRS = 5000
MAX_DISCOVERY_SECONDS = 15.0


def discover_repositories() -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """Find Git metadata rooted in the configured workspace, without following symlinks."""
    root = inside(".")
    root_marker = _git_marker(root)
    if root_marker:
        return ((".",), ())

    repositories = []
    errors = []
    pending = [root]
    start_time = time.monotonic()
    scanned = 0

    while pending:
        if time.monotonic() - start_time > MAX_DISCOVERY_SECONDS or scanned >= MAX_DISCOVERY_DIRS:
            errors.append({
                "path": ".",
                "message": "Repository discovery limit reached; workspace scan was incomplete",
            })
            break

        directory = pending.pop()
        scanned += 1
        relative_directory = directory.relative_to(root).as_posix()
        try:
            with os.scandir(directory) as entries:
                sorted_entries = sorted(entries, key=lambda entry: entry.name)
        except OSError as exc:
            errors.append({
                "path": relative_directory or ".",
                "message": exc.strerror or "Filesystem access failed",
            })
            continue

        children = []
        for entry in sorted_entries:
            if entry.name in _DEFAULT_EXCLUDE_DIRS:
                continue
            if entry.name == ".git":
                try:
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
                        repositories.append(relative_directory)
                except OSError as exc:
                    errors.append({
                        "path": (Path(relative_directory) / entry.name).as_posix(),
                        "message": exc.strerror or "Filesystem access failed",
                    })
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    children.append(Path(entry.path))
            except OSError as exc:
                errors.append({
                    "path": (Path(relative_directory) / entry.name).as_posix(),
                    "message": exc.strerror or "Filesystem access failed",
                })
        pending.extend(reversed(children))

    return tuple(repositories), tuple(errors)


def _git_marker(directory: Path) -> bool:
    try:
        mode = (directory / ".git").lstat().st_mode
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(mode) or stat.S_ISREG(mode)


def _maintain_gitignore(repository: Path) -> None:
    """Add required and detected generated-directory rules without replacing user content."""
    detected: set[str] = set()
    for current, directories, _files in os.walk(repository, followlinks=False):
        current_path = Path(current)
        safe_directories = [
            name for name in directories
            if name != ".git" and not (current_path / name).is_symlink()
        ]
        detected.update(name for name in safe_directories if name in _DEFAULT_EXCLUDE_DIRS)
        # Contents of an already ignored generated directory do not need scanning.
        directories[:] = [name for name in safe_directories if name not in _DEFAULT_EXCLUDE_DIRS]

    required = [".handy-coder/", "tmp/"]
    rules = required + [f"{name}/" for name in sorted(detected) if f"{name}/" not in required]
    ignore_path = repository / ".gitignore"
    try:
        existing = ignore_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""

    existing_rules = {line.strip() for line in existing.splitlines()}
    additions = [rule for rule in rules if rule not in existing_rules]
    if not additions:
        return
    separator = "" if not existing or existing.endswith(("\n", "\r")) else "\n"
    ignore_path.write_text(existing + separator + "\n".join(additions) + "\n", encoding="utf-8")


def _tool_description() -> str:
    if _REPOSITORIES is None:
        state = "Git repository discovery runs when this tool is first called."
    elif _REPOSITORIES:
        if len(_REPOSITORIES) == 1:
            state = (
                f"Git repository available at workspace-relative path "
                f"'{_REPOSITORIES[0]}'. repo_path is optional when selecting this repository."
            )
        else:
            paths = "\n- " + "\n- ".join(f"'{path}'" for path in _REPOSITORIES)
            state = (
                f"Multiple Git repositories are available at these workspace-relative paths:{paths}\n"
                "For every action, set repo_path to the path of the repository to work on (for example, repo_path='projects/api')."
            )
        if _DISCOVERY_ERRORS:
            paths = ", ".join(error["path"] for error in _DISCOVERY_ERRORS)
            state += f" Discovery was incomplete; these paths could not be scanned: {paths}."
    elif _DISCOVERY_ERRORS:
        paths = ", ".join(error["path"] for error in _DISCOVERY_ERRORS)
        state = (
            "No Git repository was confirmed because some workspace directories could not be scanned "
            f"({paths}). Do not initialize a repository until discovery can complete."
        )
    else:
        state = (
            "No Git repository was found in the configured workspace. Warn the user and ask whether "
            "they want a new repository initialized before calling git_manage with action='init'."
        )
    return describe(
        "Run a validated workspace Git action: status, diff, log, add, commit, push, pull, merge, rebase, checkout, branch, stash, blame, or init. Successful init creates test/ and tmp/ and maintains the repository .gitignore; add updates .gitignore before staging. These updates preserve existing content, add .handy-coder/ and tmp/, and ignore generated directories from the common exclusion list when present. status returns structured staged/working-tree and untracked summaries; diff preserves raw text and accepts mode unstaged/staged/both plus repository-relative file_paths; log includes recent commit history. pull accepts an optional remote and branch; merge/rebase require one or more refs. These three actions require a clean worktree (including untracked files). "
        "Pass action-specific Git options as separate strings in args. "
        f"{state} Use workspace-relative repo_path when selecting a repository. "
        "When repo_path is given and has a .git marker, workspace scan is bypassed for faster execution. "
        "When git exits non-zero, any output it printed to stdout is returned in stderr and stdout is empty. "
        "When to use: Review or manage local changes."
    )


def _set_inventory(
    repositories: tuple[str, ...],
    errors: tuple[dict[str, str], ...],
) -> None:
    global _REPOSITORIES, _DISCOVERY_ERRORS, _DISCOVERY_ROOT
    _REPOSITORIES = repositories
    _DISCOVERY_ERRORS = errors
    _DISCOVERY_ROOT = inside(".")


def _refresh_inventory(mcp=None, *, force: bool = False) -> None:
    target_mcp = mcp or _MCP
    if not force and _REPOSITORIES is not None and _DISCOVERY_ROOT == inside("."):
        return
    repositories, errors = discover_repositories()
    if repositories != _REPOSITORIES or errors != _DISCOVERY_ERRORS:
        _set_inventory(repositories, errors)
        if target_mcp is not None:
            _update_tool_description(target_mcp, _tool_description())
    elif _DISCOVERY_ROOT != inside("."):
        _set_inventory(repositories, errors)


def _update_tool_description(mcp, description: str) -> None:
    target_mcp = mcp or _MCP
    if target_mcp is None:
        return
    catalog_update = getattr(target_mcp, "_tool_details_update", None)
    if callable(catalog_update):
        catalog_update("git_manage", description)
        return
    descriptions = getattr(target_mcp, "descriptions", None)
    if isinstance(descriptions, dict) and "git_manage" in descriptions:
        descriptions["git_manage"] = description
        return

    manager = getattr(target_mcp, "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    if isinstance(tools, dict) and "git_manage" in tools:
        tool = tools["git_manage"]
        try:
            tool.description = description
        except (AttributeError, TypeError, ValueError):
            if hasattr(tool, "model_copy"):
                tools["git_manage"] = tool.model_copy(update={"description": description})
            elif hasattr(tool, "copy"):
                tools["git_manage"] = tool.copy(update={"description": description})
            else:
                raise RuntimeError("The MCP server cannot update the git_manage tool description")
        return

    raise RuntimeError("The MCP server cannot update the git_manage tool description")


def _route_failure_to_stderr(result: dict) -> dict:
    """Git prints some failures (e.g. 'nothing to commit') to stdout; surface them on stderr."""
    if result.get("exit_code") == 0:
        return result
    out = result.get("stdout", "")
    if out:
        err = result.get("stderr", "")
        result["stderr"] = out.rstrip("\n") + ("\n" + err if err else "\n")
        result["stdout"] = ""
    return result


def git_manage(
    action: Annotated[
        Literal["status", "diff", "log", "add", "commit", "push", "pull", "merge", "rebase", "checkout", "branch", "stash", "blame", "init"],
        Field(description=(
            "Git operation to run. status and init take no args; diff accepts mode unstaged/staged/both and optional file_paths; diff and log accept "
            "validated options; add requires one or more workspace-relative paths (options are rejected); commit requires -m and a message; push accepts optional remote/refspec arguments with limited flags; branch lists/creates, stash accepts list/push/pop/apply/drop, blame requires one relative file; checkout requires "
            "exactly one branch, tag, or commit (file paths are rejected with error_code not_a_ref); pull accepts up to remote and branch; merge/rebase require refs. pull, merge, and rebase require a clean worktree. init initializes the workspace root or a repo_path subdirectory, then creates test/ and tmp/ and maintains .gitignore. add maintains .gitignore before staging."
        )),
    ],
    args: Annotated[
        list[str] | None,
        Field(description=(
            "Optional action-specific Git arguments, each as a separate string. Relative file paths for add and diff are resolved against repo_path (or the selected repository) and must stay inside that repository. "
            "Use no arguments for status or init. diff supports --cached, --stat, "
            "--name-only, and workspace-relative paths after --; log supports -n, --oneline, "
            "--decorate, --all, --, and a count. commit requires ['-m', 'message'] "
            "and optionally accepts -a; add takes one or more workspace-relative paths; push supports -u/--set-upstream, --force-with-lease, --tags, --dry-run plus optional <remote> <refspec>; checkout takes exactly one branch or ref. branch takes --list or one branch name; stash takes list/push/pop/apply/drop; blame takes one workspace-relative file path."
        )),
    ] = None,
    repo_path: Annotated[
        str | None,
        Field(description=(
            "Workspace-relative path to the Git repository root. Optional when one "
            "repository is available, required when multiple repositories are found. Relative paths are resolved from the workspace root. "
            "and, for init, optionally names the subdirectory to initialize (defaults to the workspace root)."
        )),
    ] = None,
    mode: Annotated[Literal["unstaged", "staged", "both"], Field(description="Diff selection: unstaged, staged, or both. Applies to diff; ignored by other review actions.")] = "unstaged",
    file_paths: Annotated[list[str] | None, Field(description="Optional repository-relative files to limit diff review; every path must remain within the selected repository.")] = None,
) -> dict:
    target_mcp = _MCP
    args = args or []
    file_paths = file_paths or []
    if action not in ("status", "diff", "log", "add", "commit", "push", "pull", "merge", "rebase", "checkout", "branch", "stash", "blame", "init"):
        raise ValueError("Unsupported Git action")
    if action == "status" and args:
        raise ValueError("status accepts no args")
    if action == "init" and args:
        raise ValueError("init accepts no args")
    if action != "diff" and (mode != "unstaged" or file_paths):
        raise ValueError("mode and file_paths are only valid for diff")
    if action == "diff" and mode not in {"unstaged", "staged", "both"}:
        raise ValueError("mode must be unstaged, staged, or both")
    if action == "diff" and any(
        arg.startswith("-") and arg not in ("--cached", "--stat", "--name-only", "--")
        for arg in args
    ):
        bad = next(
            arg for arg in args
            if arg.startswith("-") and arg not in ("--cached", "--stat", "--name-only", "--")
        )
        raise ValueError(
            f"Unsupported diff option: '{bad}'. "
            "Allowed flags: --cached, --stat, --name-only, and '--' (separator before file paths)"
        )
    if action == "log" and any(
        arg.startswith("-") and arg not in ("-n", "--oneline", "--decorate", "--all", "--")
        and not re.fullmatch(r"-\d+", arg)
        for arg in args
    ):
        bad = next(
            arg for arg in args
            if arg.startswith("-") and arg not in ("-n", "--oneline", "--decorate", "--all", "--")
            and not re.fullmatch(r"-\d+", arg)
        )
        raise ValueError(
            f"Unsupported log option: '{bad}'. "
            "Allowed flags: -n <count>, -<count> (e.g. -3), --oneline, --decorate, --all, and '--' (separator before file paths)"
        )
    if action == "commit":
        if not args or "-m" not in args or args.count("-m") != 1 or args.index("-m") == len(args) - 1:
            raise ValueError("commit requires -m followed by a commit message")
        message_index = args.index("-m")
        if any(arg.startswith("-") for arg in args[:message_index] if arg != "-a") or any(
            arg.startswith("-") for arg in args[message_index + 2:]
        ):
            raise ValueError("Only -a and -m commit options are accepted")
    if action == "add" and (not args or any(arg.startswith("-") and arg != "--" for arg in args)):
        raise ValueError("add requires one or more workspace-relative paths and accepts no options")
    if action == "push":
        allowed_push_flags = {"-u", "--set-upstream", "--force-with-lease", "--tags", "--dry-run"}
        positional = [arg for arg in args if not arg.startswith("-")]
        bad = next((arg for arg in args if arg.startswith("-") and arg not in allowed_push_flags), None)
        if bad:
            raise ValueError(
                f"Unsupported push option: '{bad}'. Allowed flags: -u, --set-upstream, --force-with-lease, --tags, --dry-run"
            )
        if len(positional) > 2:
            raise ValueError("push accepts at most a remote and optional refspec")
    if action == "pull":
        if len(args) > 2 or any(arg.startswith("-") for arg in args):
            raise ValueError("pull accepts at most a remote and branch, with no options")
    if action in ("merge", "rebase"):
        if not args or any(arg.startswith("-") for arg in args):
            raise ValueError(f"{action} requires one or more refs and accepts no options")
    if action == "checkout" and (len(args) != 1 or args[0].startswith("-")):
        raise ValueError("checkout requires exactly one branch or ref")
    if action == "branch" and (len(args) > 1 or (args and args[0].startswith("-") and args[0] != "--list")):
        raise ValueError("branch accepts no args, --list, or one new branch name")
    if action == "stash" and (not args or args[0] not in {"list", "push", "pop", "apply", "drop"}):
        raise ValueError("stash requires list, push, pop, apply, or drop")
    if action == "blame" and len(args) != 1:
        raise ValueError("blame requires exactly one workspace-relative file path")
    for arg in args:
        if arg.startswith(("../", "/", "~/")) or "/../" in arg or arg == "..":
            try:
                inside(arg)
            except ValueError:
                return {"supported": True, "error_code": "path_escape", "message": path_escape_message(arg), "path": arg, "exit_code": 1}

    git = shutil.which("git")
    if not git:
        return {
            "supported": False,
            "error_code": "git_unavailable",
            "message": "Git is not installed or is unavailable on PATH.",
            "hint": "Install Git and confirm `git --version` works in this environment.",
            "exit_code": 127,
        }

    if action == "init":
        if repo_path:
            # Allow init in a specific subdirectory instead of always using workspace root.
            try:
                init_path = inside(repo_path)
            except ValueError:
                return {"supported": True, "error_code": "path_escape", "message": path_escape_message(repo_path), "path": repo_path, "exit_code": 1}
            init_path.mkdir(parents=True, exist_ok=True)
            if not init_path.is_dir():
                raise ValueError(f"repo_path for init must be a directory: {repo_path}")
        else:
            init_path = inside(".")
        _refresh_inventory(target_mcp, force=True)
        if _DISCOVERY_ERRORS:
            return {
                "supported": True,
                "error_code": "repository_discovery_incomplete",
                "message": "Cannot initialize because repository discovery could not scan the whole workspace.",
                "hint": "Retry after narrowing --root or fixing unreadable directories, then run git_manage status.",
                "exit_code": 1,
            }
        if _REPOSITORIES:
            raise ValueError("A Git repository is already available; init is only allowed when none are found")
        result = run_process([git, "init"], cwd=init_path)
        if result["exit_code"] == 0:
            (init_path / "test").mkdir(exist_ok=True)
            (init_path / "tmp").mkdir(exist_ok=True)
            _maintain_gitignore(init_path)
            new_repositories, new_errors = discover_repositories()
            _set_inventory(new_repositories, new_errors)
            _update_tool_description(target_mcp, _tool_description())
        return _route_failure_to_stderr(result)

    # Fast path: when repo_path is explicitly given and a .git marker exists there,
    # bypass the potentially slow workspace-wide discovery scan entirely.
    if repo_path:
        requested_path = Path(repo_path)
        if requested_path.is_absolute():
            return {"supported": True, "error_code": "path_escape", "message": path_escape_message(repo_path), "path": repo_path, "exit_code": 1}
        try:
            selected_path = inside(repo_path, must_exist=True)
        except ValueError:
            return {"supported": True, "error_code": "path_escape", "message": path_escape_message(repo_path), "path": repo_path, "exit_code": 1}
        except FileNotFoundError:
            return {"supported": True, "error_code": "not_found", "message": f"Repository path not found: {repo_path}", "path": repo_path, "exit_code": 1}
        relative_path = selected_path.relative_to(inside(".")).as_posix() or "."
        if _git_marker(selected_path):
            # .git confirmed — skip discovery, proceed directly to git call.
            pass
        else:
            # No .git marker — fall back to discovery for a meaningful error.
            if _REPOSITORIES is None or _DISCOVERY_ROOT != inside("."):
                _refresh_inventory(target_mcp)
            if relative_path not in (_REPOSITORIES or ()):
                _refresh_inventory(target_mcp, force=True)
                if relative_path not in (_REPOSITORIES or ()):
                    raise ValueError(
                        f"'{relative_path}' is not a recognised Git repository. "
                        "Discovered repositories: "
                        + (", ".join(f"'{p}'" for p in (_REPOSITORIES or ())) or "(none)")
                    )
    else:
        # No repo_path — must run discovery to select the right repo.
        if _REPOSITORIES is None or _DISCOVERY_ROOT != inside("."):
            _refresh_inventory(target_mcp)
        if _DISCOVERY_ERRORS and not _REPOSITORIES:
            return {
                "supported": True,
                "error_code": "repository_discovery_incomplete",
                "message": "Cannot select a Git repository because workspace discovery was incomplete.",
                "hint": "Fix unreadable workspace paths or provide repo_path explicitly if known.",
                "exit_code": 1,
            }
        if len(_REPOSITORIES) > 1:
            raise ValueError(
                "repo_path is required when multiple repositories are available: "
                + ", ".join(_REPOSITORIES)
            )
        relative_path = _REPOSITORIES[0] if _REPOSITORIES else "."
        selected_path = inside(relative_path, must_exist=True)

    check = run_process([git, "rev-parse", "--is-inside-work-tree"], cwd=selected_path)
    if check["stderr"].startswith("Failed to start process:"):
        return {
            "supported": False,
            "error_code": "git_unavailable",
            "message": "Git could not be started.",
            "hint": "Check PATH and permissions, then run `git --version` in the same shell.",
            "exit_code": check["exit_code"],
        }
    if check["exit_code"] != 0 or check["stdout"].strip() != "true":
        if "not a git repository" in check["stderr"].lower():
            return {
                "supported": True,
                "error_code": "not_a_git_repository",
                "message": "The selected workspace path is not inside a Git repository.",
                "hint": "Run git_manage action='status' at the repository root, or use action='init' if this workspace should be initialized.",
                "exit_code": check["exit_code"] or 128,
            }
        return {
            "supported": True,
            "error_code": "git_repository_check_failed",
            "message": "Could not verify that the selected workspace path is a Git repository.",
            "hint": "Run git_manage action='status' and inspect stderr for Git diagnostics.",
            "exit_code": check["exit_code"] or 1,
        }
    if action == "checkout":
        ref = args[0]
        resolved = run_process(
            [git, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=selected_path
        )
        if resolved["exit_code"] != 0:
            # Allow DWIM checkout of a branch that exists only as a remote-tracking ref.
            remote = run_process(
                [git, "for-each-ref", "--format=%(refname)", f"refs/remotes/*/{ref}"],
                cwd=selected_path,
            )
            if remote["exit_code"] != 0 or not remote["stdout"].strip():
                return {
                    "supported": True,
                    "error_code": "not_a_ref",
                    "message": (
                        f"'{ref}' is not a branch, tag, or commit. checkout only switches refs; "
                        "restoring file contents is not supported because it discards uncommitted edits."
                    ),
                    "hint": "Check available refs with git_manage action='log' args=['--oneline','-n','20'].",
                    "exit_code": 1,
                }
        # '--' forces git to treat the argument as a ref, never as a file path.
        return _route_failure_to_stderr(run_process([git, "checkout", ref, "--"], cwd=selected_path))
    if action in ("pull", "merge", "rebase"):
        state = run_process([git, "status", "--porcelain", "--untracked-files=all"], cwd=selected_path)
        if state["exit_code"] != 0:
            return _route_failure_to_stderr(state)
        if state.get("stdout", "").strip():
            return {
                "supported": True,
                "error_code": "dirty_worktree",
                "message": f"Cannot {action}: the worktree has staged, unstaged, or untracked changes.",
                "stdout": "",
                "stderr": "Worktree must be clean before this action.",
                "exit_code": 1,
            }
    # Resolve file operands explicitly from the selected repository. This avoids
    # Git's cwd-dependent interpretation changing when repo_path is supplied.
    git_args = list(args)
    if action == "log":
        # Rewrite the -<N> shorthand (e.g. -3) to ['-n', '3']; anything after '--' is a path.
        sep = git_args.index("--") if "--" in git_args else len(git_args)
        git_args = [
            part for a in git_args[:sep]
            for part in (["-n", a[1:]] if re.fullmatch(r"-\d+", a) else [a])
        ] + git_args[sep:]
    if action == "diff":
        git_args = [arg for arg in git_args if arg != "--cached"]
        if mode == "staged":
            git_args.insert(0, "--cached")
        elif mode == "both":
            # Both passes are combined below so staged and unstaged raw text is preserved.
            pass
        if file_paths:
            separator = git_args.index("--") if "--" in git_args else len(git_args)
            git_args = git_args[:separator] + ["--", *(git_args[separator + 1:] if separator < len(git_args) else []), *file_paths]
    path_indexes = []
    if action == "add":
        path_indexes = list(range(len(git_args)))
    elif action == "diff" and "--" in git_args:
        separator = git_args.index("--")
        path_indexes = list(range(separator + 1, len(git_args)))
    elif action == "blame":
        path_indexes = [0]
    for index in path_indexes:
        raw = Path(git_args[index]).expanduser()
        resolved = (raw if raw.is_absolute() else selected_path / raw).resolve()
        try:
            resolved.relative_to(selected_path.resolve())
        except ValueError:
            return {"supported": True, "error_code": "path_escape", "message": path_escape_message(git_args[index]), "path": git_args[index], "exit_code": 1}
        if not resolved.exists() and not (action == "diff" and resolved.parent.exists()):
            return {"supported": True, "error_code": "not_found", "message": f"Git path not found: {git_args[index]}", "path": git_args[index], "exit_code": 1}
        git_args[index] = str(resolved)
    if action == "add":
        _maintain_gitignore(selected_path)
    if action == "diff":
        if mode == "both":
            staged = run_process([git, "diff", "--cached", *[arg for arg in git_args if arg != "--cached"]], cwd=selected_path)
            unstaged = run_process([git, "diff", *[arg for arg in git_args if arg != "--cached"]], cwd=selected_path)
            result = {"stdout": staged.get("stdout", "") + unstaged.get("stdout", ""), "stderr": staged.get("stderr", "") + unstaged.get("stderr", ""), "exit_code": staged.get("exit_code", 1) or unstaged.get("exit_code", 1)}
        else:
            result = run_process([git, action, *git_args], cwd=selected_path)
        result = _route_failure_to_stderr(result)
        result["mode"] = mode
        return result
    result = _route_failure_to_stderr(run_process([git, action, *git_args], cwd=selected_path))
    if action == "status" and result.get("exit_code") == 0:
        porcelain = run_process([git, "status", "--porcelain=v1", "--untracked-files=all"], cwd=selected_path)
        entries = []
        for line in porcelain.get("stdout", "").splitlines():
            if len(line) >= 4:
                entries.append({"index": line[0], "worktree": line[1], "path": line[3:], "untracked": line[:2] == "??"})
        result["working_tree"] = [item for item in entries if item["worktree"] != " " or item["untracked"]]
        result["staged"] = [item for item in entries if item["index"] not in (" ", "?")]
        result["untracked_files"] = [item["path"] for item in entries if item["untracked"]]
        result["untracked_summary"] = {"count": len(result["untracked_files"])}
    elif action == "log" and result.get("exit_code") == 0:
        history_args = list(git_args)
        separator = history_args.index("--") if "--" in history_args else len(history_args)
        history_args[separator:separator] = ["--format=%H%x09%h%x09%an%x09%aI%x09%s"]
        history = run_process([git, "log", *history_args], cwd=selected_path)
        result["recent_commits"] = [dict(zip(("hash", "short_hash", "author", "date", "subject"), line.split("\t", 4))) for line in history.get("stdout", "").splitlines()]
    return result


def register(mcp):
    global _REPOSITORIES, _DISCOVERY_ERRORS, _DISCOVERY_ROOT, _MCP
    _MCP = mcp
    root = inside(".")
    if _DISCOVERY_ROOT != root:
        _REPOSITORIES = None
        _DISCOVERY_ERRORS = ()
        _DISCOVERY_ROOT = None
    return mcp.tool(description=_tool_description())(git_manage)

