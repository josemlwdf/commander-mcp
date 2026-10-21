from pathlib import Path
import os
import shutil
import stat
from typing import Annotated, Literal

from pydantic import Field

from ..support import inside, run_process, describe


_REPOSITORIES: tuple[str, ...] | None = None
_DISCOVERY_ERRORS: tuple[dict[str, str], ...] = ()
_DISCOVERY_ROOT: Path | None = None


def discover_repositories() -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """Find Git metadata rooted in the configured workspace, without following symlinks."""
    root = inside(".")
    root_marker = _git_marker(root)
    if root_marker:
        return ((".",), ())

    repositories = []
    errors = []
    pending = [root]
    while pending:
        directory = pending.pop()
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
                "For every action, set repo_path to the path of the repository to work on."
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
        "Run a validated workspace Git action: status, diff, log, commit, checkout, or init. "
        "Pass action-specific Git options as separate strings in args. "
        f"{state} Use workspace-relative repo_path when selecting a repository. "
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


def _refresh_inventory(mcp, *, force: bool = False) -> None:
    if not force and _REPOSITORIES is not None and _DISCOVERY_ROOT == inside("."):
        return
    repositories, errors = discover_repositories()
    if repositories != _REPOSITORIES or errors != _DISCOVERY_ERRORS:
        _set_inventory(repositories, errors)
        _update_tool_description(mcp, _tool_description())
    elif _DISCOVERY_ROOT != inside("."):
        _set_inventory(repositories, errors)


def _update_tool_description(mcp, description: str) -> None:
    descriptions = getattr(mcp, "descriptions", None)
    if isinstance(descriptions, dict) and "git_manage" in descriptions:
        descriptions["git_manage"] = description
        return

    manager = getattr(mcp, "_tool_manager", None)
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


def register(mcp):
    global _REPOSITORIES, _DISCOVERY_ERRORS, _DISCOVERY_ROOT
    root = inside(".")
    if _DISCOVERY_ROOT != root:
        _REPOSITORIES = None
        _DISCOVERY_ERRORS = ()
        _DISCOVERY_ROOT = None

    @mcp.tool(description=_tool_description())
    def git_manage(
        action: Annotated[
            Literal["status", "diff", "log", "commit", "checkout", "init"],
            Field(description=(
                "Git operation to run. status and init take no args; diff and log accept "
                "validated options; commit requires -m and a message; checkout requires "
                "one branch or ref. init initializes the configured workspace root."
            )),
        ],
        args: Annotated[
            list[str] | None,
            Field(description=(
                "Optional action-specific Git arguments, each as a separate string. "
                "Use no arguments for status or init. diff supports --cached, --stat, "
                "--name-only, and workspace-relative paths after --; log supports -n, --oneline, "
                "--decorate, --all, --, and a count. commit requires ['-m', 'message'] "
                "and optionally accepts -a; checkout takes exactly one branch or ref."
            )),
        ] = None,
        repo_path: Annotated[
            str | None,
            Field(description=(
                "Workspace-relative path to the Git repository root. Optional when one "
                "repository is available, required when multiple repositories are found, "
                "and must be omitted for init."
            )),
        ] = None,
    ) -> dict:
        args = args or []
        if action not in ("status", "diff", "log", "commit", "checkout", "init"):
            raise ValueError("Unsupported Git action")
        if action == "status" and args:
            raise ValueError("status accepts no args")
        if action == "init" and args:
            raise ValueError("init accepts no args")
        if action == "diff" and any(
            arg.startswith("-") and arg not in ("--cached", "--stat", "--name-only", "--")
            for arg in args
        ):
            raise ValueError("Unsupported diff option")
        if action == "log" and any(
            arg.startswith("-") and arg not in ("-n", "--oneline", "--decorate", "--all", "--")
            and not arg.isdigit()
            for arg in args
        ):
            raise ValueError("Unsupported log option")
        if action == "commit":
            if not args or "-m" not in args or args.count("-m") != 1 or args.index("-m") == len(args) - 1:
                raise ValueError("commit requires -m followed by a commit message")
            message_index = args.index("-m")
            if any(arg.startswith("-") for arg in args[:message_index] if arg != "-a") or any(
                arg.startswith("-") for arg in args[message_index + 2:]
            ):
                raise ValueError("Only -a and -m commit options are accepted")
        if action == "checkout" and (len(args) != 1 or args[0].startswith("-")):
            raise ValueError("checkout requires exactly one branch or ref")
        for arg in args:
            if arg.startswith(("../", "/", "~/")) or "/../" in arg or arg == "..":
                try:
                    inside(arg)
                except ValueError:
                    raise ValueError(f"Git argument escapes workspace: {arg}")

        git = shutil.which("git")
        if not git:
            return {
                "supported": False,
                "error_code": "git_unavailable",
                "message": "Git is not installed or is unavailable on PATH.",
                "exit_code": 127,
            }

        if action == "init":
            if repo_path:
                raise ValueError("init always initializes the configured workspace root; omit repo_path")
            _refresh_inventory(mcp, force=True)
            if _DISCOVERY_ERRORS:
                return {
                    "supported": True,
                    "error_code": "repository_discovery_incomplete",
                    "message": "Cannot initialize because repository discovery could not scan the whole workspace.",
                    "exit_code": 1,
                }
            if _REPOSITORIES:
                raise ValueError("A Git repository is already available; init is only allowed when none are found")
            root = inside(".")
            result = run_process([git, "init"], cwd=root)
            if result["exit_code"] == 0:
                new_repositories, new_errors = discover_repositories()
                _set_inventory(new_repositories, new_errors)
                _update_tool_description(mcp, _tool_description())
            return result

        if _REPOSITORIES is None or _DISCOVERY_ROOT != inside("."):
            _refresh_inventory(mcp)
        if _DISCOVERY_ERRORS and not _REPOSITORIES:
            return {
                "supported": True,
                "error_code": "repository_discovery_incomplete",
                "message": "Cannot select a Git repository because workspace discovery was incomplete.",
                "exit_code": 1,
            }

        if repo_path:
            requested_path = Path(repo_path)
            if requested_path.is_absolute():
                raise ValueError("repo_path must be relative to the configured workspace")
            selected_path = inside(repo_path, must_exist=True)
            relative_path = selected_path.relative_to(inside(".")).as_posix() or "."
            if relative_path not in _REPOSITORIES:
                raise ValueError(
                    "repo_path must match one of the discovered repository paths: "
                    + ", ".join(_REPOSITORIES)
                )
        else:
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
                "exit_code": check["exit_code"],
            }
        if check["exit_code"] != 0 or check["stdout"].strip() != "true":
            if "not a git repository" in check["stderr"].lower():
                return {
                    "supported": True,
                    "error_code": "not_a_git_repository",
                    "message": "The selected workspace path is not inside a Git repository.",
                    "exit_code": check["exit_code"] or 128,
                }
            return {
                "supported": True,
                "error_code": "git_repository_check_failed",
                "message": "Could not verify that the selected workspace path is a Git repository.",
                "exit_code": check["exit_code"] or 1,
            }
        return run_process([git, action, *args], cwd=selected_path)
