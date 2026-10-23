from __future__ import annotations

import fnmatch
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from ..support import describe, inside, run_process


NudgeLevel = Literal["none", "soft", "firm"]
NudgeReason = Literal["threshold", "green", "switch", "firm"]


@dataclass
class Thresholds:
    lines: int
    files: int
    calls: int
    tokens: int


@dataclass
class GreenTrigger:
    min_lines: int


@dataclass
class HardLimit:
    lines: int
    files: int


@dataclass
class CommitNudgeConfig:
    floor_lines: int = 10
    cooldown_calls: int = 3
    firm_repeat_calls: int = 3
    soft: Thresholds = field(default_factory=lambda: Thresholds(lines=80, files=4, calls=6, tokens=3000))
    firm: Thresholds = field(default_factory=lambda: Thresholds(lines=200, files=8, calls=12, tokens=8000))
    hard: HardLimit = field(default_factory=lambda: HardLimit(lines=300, files=15))
    green: GreenTrigger = field(default_factory=lambda: GreenTrigger(min_lines=20))
    switch_min_lines: int = 30
    enforcement: Literal["off", "nudge", "gate"] = "nudge"


@dataclass
class DiffMetrics:
    lines: int
    files: int
    changed_files: list[str]
    git_available: bool


@dataclass
class CommitTrackerState:
    base_head: str | None = None
    baseline_deleted_files: set[str] = field(default_factory=set)
    write_calls: int = 0
    payload_chars: int = 0
    last_nudge_at_call: int = 0
    last_nudge_level: NudgeLevel = "none"
    last_touched_file: str | None = None
    files_touched_in_order: list[str] = field(default_factory=list)
    last_check_status: Literal["none", "pass", "fail"] = "none"
    modules_seen: set[str] = field(default_factory=set)
    pending_switch_from_module: str | None = None
    defer_remaining_calls: int = 0
    defer_used_in_cycle: bool = False
    gate_block_next_write: bool = False
    gate_rejections_in_row: int = 0
    gate_flagged_for_review: bool = False


CONFIG = CommitNudgeConfig()
STATE = CommitTrackerState()
STATE_LOCK = threading.Lock()
DEFER_LOG: list[dict[str, Any]] = []

_WRITE_TOOLS = frozenset({"apply_edits", "create_file"})
_CHECK_TOOLS = frozenset({"run_tests", "code_get_diagnostics", "code_format_lint"})
_IGNORE_PATH_PATTERNS = (
    "**/package-lock.json",
    "**/pnpm-lock.yaml",
    "**/yarn.lock",
    "**/poetry.lock",
    "**/Pipfile.lock",
    "dist/**",
    "build/**",
    "**/__pycache__/**",
    "**/generated/**",
)


def _approx_tokens(state: CommitTrackerState) -> int:
    return state.payload_chars // 4


def _is_write_tool(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in _WRITE_TOOLS:
        return True
    if tool_name == "code_format_lint":
        return bool(args.get("fix"))
    return False


def _extract_touched_files(tool_name: str, args: dict[str, Any]) -> list[str]:
    touched: list[str] = []
    if tool_name == "create_file":
        path = args.get("path")
        if isinstance(path, str):
            touched.append(path)
    elif tool_name == "apply_edits":
        for edit in args.get("edits") or []:
            path = getattr(edit, "path", None)
            if path is None and isinstance(edit, dict):
                path = edit.get("path")
            if isinstance(path, str):
                touched.append(path)
    elif tool_name == "code_format_lint":
        for path in args.get("paths") or []:
            if isinstance(path, str):
                touched.append(path)
    return touched


def _module_for_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return "."
    return normalized.split("/", 1)[0]


def _is_ignored(path: str) -> bool:
    path = path.replace("\\", "/")
    for pattern in _IGNORE_PATH_PATTERNS:
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


def _git_ok() -> bool:
    root = inside(".")
    check = run_process(["git", "rev-parse", "--is-inside-work-tree"], cwd=root)
    return check.get("exit_code") == 0 and check.get("stdout", "").strip() == "true"


def _current_head() -> str | None:
    root = inside(".")
    head = run_process(["git", "rev-parse", "HEAD"], cwd=root)
    if head.get("exit_code") != 0:
        return None
    value = head.get("stdout", "").strip()
    return value or None


def _current_git_dir() -> Path | None:
    root = inside(".")
    res = run_process(["git", "rev-parse", "--git-dir"], cwd=root)
    if res.get("exit_code") != 0:
        return None
    rel = res.get("stdout", "").strip()
    if not rel:
        return None
    return (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel)


def _is_merge_or_rebase_in_progress() -> bool:
    git_dir = _current_git_dir()
    if git_dir is None:
        return False
    return any((git_dir / marker).exists() for marker in ("MERGE_HEAD", "rebase-apply", "rebase-merge"))


def _count_text_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return 0
    return len(text.splitlines())


def measure_diff(*, exclude_paths: set[str] | None = None) -> DiffMetrics:
    root = inside(".")
    if not _git_ok():
        return DiffMetrics(lines=0, files=0, changed_files=[], git_available=False)

    changed_files: set[str] = set()
    changed_lines = 0

    diff = run_process(["git", "diff", "--numstat", "HEAD"], cwd=root)
    if diff.get("exit_code") == 0:
        for line in diff.get("stdout", "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added, removed, path = parts[0], parts[1], parts[2]
            if _is_ignored(path) or path in (exclude_paths or set()):
                continue
            changed_files.add(path)
            if added.isdigit() and removed.isdigit():
                changed_lines += int(added) + int(removed)

    untracked = run_process(["git", "ls-files", "--others", "--exclude-standard"], cwd=root)
    if untracked.get("exit_code") == 0:
        for rel in (p.strip() for p in untracked.get("stdout", "").splitlines()):
            if not rel or _is_ignored(rel):
                continue
            changed_files.add(rel)
            changed_lines += _count_text_lines(root / rel)

    return DiffMetrics(
        lines=changed_lines,
        files=len(changed_files),
        changed_files=sorted(changed_files),
        git_available=True,
    )


def _hard_limit_exceeded(metrics: DiffMetrics) -> bool:
    return metrics.lines >= CONFIG.hard.lines or metrics.files >= CONFIG.hard.files


def _in_cooldown(state: CommitTrackerState) -> bool:
    return state.last_nudge_at_call > 0 and (state.write_calls - state.last_nudge_at_call) < CONFIG.cooldown_calls


def _evaluate(metrics: DiffMetrics, state: CommitTrackerState) -> tuple[NudgeLevel, NudgeReason | None]:
    lines_known = metrics.git_available
    tokens = _approx_tokens(state)

    if lines_known and metrics.lines < CONFIG.floor_lines:
        return "none", None

    firm_hit = (
        (lines_known and metrics.lines >= CONFIG.firm.lines)
        or (lines_known and metrics.files >= CONFIG.firm.files)
        or state.write_calls >= CONFIG.firm.calls
        or tokens >= CONFIG.firm.tokens
    )
    if firm_hit:
        if state.last_nudge_level == "firm" and (state.write_calls - state.last_nudge_at_call) < CONFIG.firm_repeat_calls:
            return "none", None
        return "firm", "firm"

    if _in_cooldown(state):
        return "none", None
    if state.last_check_status == "fail":
        return "none", None
    if state.last_check_status == "pass" and (not lines_known or metrics.lines >= CONFIG.green.min_lines):
        return "soft", "green"
    if state.pending_switch_from_module and (not lines_known or metrics.lines >= CONFIG.switch_min_lines):
        return "soft", "switch"

    soft_hit = (
        (lines_known and metrics.lines >= CONFIG.soft.lines)
        or (lines_known and metrics.files >= CONFIG.soft.files)
        or state.write_calls >= CONFIG.soft.calls
        or tokens >= CONFIG.soft.tokens
    )
    if soft_hit:
        if state.last_nudge_level == "soft" and (state.write_calls - state.last_nudge_at_call) <= 1:
            return "none", None
        return "soft", "threshold"
    return "none", None


def _reset_cycle(state: CommitTrackerState) -> None:
    state.base_head = _current_head()
    state.baseline_deleted_files = _deleted_paths_at_baseline()
    state.write_calls = 0
    state.payload_chars = 0
    state.last_nudge_at_call = 0
    state.last_nudge_level = "none"
    state.last_touched_file = None
    state.files_touched_in_order.clear()
    state.last_check_status = "none"
    state.modules_seen.clear()
    state.pending_switch_from_module = None
    state.defer_remaining_calls = 0
    state.defer_used_in_cycle = False
    state.gate_block_next_write = False
    state.gate_rejections_in_row = 0
    state.gate_flagged_for_review = False


def _ensure_cycle_initialized(state: CommitTrackerState) -> None:
    if state.base_head is None:
        state.base_head = _current_head()
        state.baseline_deleted_files = _deleted_paths_at_baseline()
        return
    head = _current_head()
    if head and state.base_head and head != state.base_head:
        _reset_cycle(state)


def _deleted_paths_at_baseline() -> set[str]:
    if not _git_ok():
        return set()
    status = run_process(["git", "diff", "--name-status", "HEAD"], cwd=inside("."))
    if status.get("exit_code") != 0:
        return set()
    return {parts[-1] for line in status.get("stdout", "").splitlines()
            if (parts := line.split("\t")) and parts[0] == "D" and len(parts) >= 2}


def _record_touch(state: CommitTrackerState, touched_files: list[str]) -> None:
    for path in touched_files:
        state.last_touched_file = path
        state.files_touched_in_order.append(path)
        module = _module_for_path(path)
        prev_module = _module_for_path(state.files_touched_in_order[-2]) if len(state.files_touched_in_order) > 1 else None
        if prev_module and module != prev_module and module not in state.modules_seen:
            state.pending_switch_from_module = prev_module
        state.modules_seen.add(module)


def _format_nudge(level: NudgeLevel, reason: NudgeReason | None, metrics: DiffMetrics, state: CommitTrackerState) -> str:
    files_preview = ", ".join(metrics.changed_files[:8])
    if len(metrics.changed_files) > 8:
        files_preview += f", +{len(metrics.changed_files) - 8} more"
    prefix = "[commit-nudge: firm]" if level == "firm" else "[commit-nudge]"
    if reason == "green":
        body = (
            f"Tests/lint are green and you have {metrics.lines} uncommitted changed lines across "
            f"{metrics.files} files. Consider committing this known-good checkpoint now."
        )
    elif reason == "switch":
        body = (
            f"You appear to be switching modules. Since last commit: {metrics.files} files, "
            f"{metrics.lines} lines, {state.write_calls} write calls. Consider committing the previous module chunk first."
        )
    elif level == "firm":
        body = (
            f"{metrics.files} files, {metrics.lines} lines changed since your last commit. "
            "Please call git_manage with action='commit' now. "
            "If you cannot commit yet, call defer_commit with a reason."
        )
    else:
        body = (
            f"Since your last commit: {metrics.files} files, {metrics.lines} lines changed, "
            f"{state.write_calls} write calls. Consider committing this chunk with git_manage action='commit'."
        )
    if files_preview:
        body += f" Files: {files_preview}."
    body += " Review .gitignore and add any missing ignore rules before committing."
    return f"{prefix} {body}"


def _append_nudge(result: Any, level: NudgeLevel, reason: NudgeReason | None, metrics: DiffMetrics, state: CommitTrackerState) -> Any:
    if not isinstance(result, dict):
        return result
    # Turn the former commit suggestion into a bounded commit task. Keep the
    # parent-facing payload to an action notice; the subagent owns inspection
    # and execution and never pushes.
    from ..subagents.tools import subagent_run

    try:
        task = subagent_run(
            role="committer",
            task="Review the repository .gitignore and add missing ignore rules for generated artifacts when needed, then review and commit the current workspace changes when they form a safe, coherent checkpoint. Do not push. If unrelated changes are present, leave the worktree untouched and report that no commit was made.",
            context=(
                f"Commit nudge: {level} ({reason or 'threshold'}); {metrics.files} changed files, "
                f"{metrics.lines} changed lines. Changed paths: {', '.join(metrics.changed_files)}"
            ),
            deliverable="Report only whether a commit was made, its short hash and message, or why it was skipped.",
            wait=False,
        )
        task_id = task.get("task_id")
        status = task.get("status", "failed")
        message = (
            f"[commit-action] Commit subagent started (task {task_id}); no push requested. "
            "Review .gitignore and add missing generated artifact rules when needed."
        )
    except Exception as exc:  # noqa: BLE001
        task_id = None
        status = "failed"
        message = f"[commit-action] Commit subagent could not start: {exc}"
    out = dict(result)
    out["commit_nudge"] = {
        "level": level,
        "reason": reason,
        "lines": metrics.lines,
        "files": metrics.files,
        "changed_files": metrics.changed_files,
        "message": message,
        "task_id": task_id,
        "task_status": status,
    }
    if isinstance(out.get("message"), str):
        out["message"] = out["message"].rstrip() + "\n" + message
    return out


def _extract_status_from_check(tool_name: str, result: Any) -> Literal["none", "pass", "fail"]:
    if not isinstance(result, dict):
        return "none"
    if tool_name == "run_tests":
        if not result.get("supported", False):
            return "none"
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int):
            return "pass" if exit_code == 0 else "fail"
    if tool_name == "code_get_diagnostics":
        ok = result.get("ok")
        if isinstance(ok, bool):
            return "pass" if ok else "fail"
    if tool_name == "code_format_lint":
        ok = result.get("ok")
        if isinstance(ok, bool):
            return "pass" if ok else "fail"
    return "none"


def before_tool_call(tool_name: str, args: dict[str, Any]) -> dict | None:
    if CONFIG.enforcement != "gate" or not _is_write_tool(tool_name, args):
        return None
    with STATE_LOCK:
        _ensure_cycle_initialized(STATE)
        if _is_merge_or_rebase_in_progress():
            STATE.gate_block_next_write = False
            return None
        if STATE.defer_remaining_calls > 0:
            return None
        if not STATE.gate_block_next_write:
            return None
        metrics = measure_diff(exclude_paths=STATE.baseline_deleted_files)
        if not _hard_limit_exceeded(metrics):
            STATE.gate_block_next_write = False
            STATE.gate_rejections_in_row = 0
            return None
        STATE.gate_rejections_in_row += 1
        if STATE.gate_rejections_in_row >= 3:
            STATE.gate_rejections_in_row = 0
            STATE.gate_block_next_write = False
            STATE.gate_flagged_for_review = True
            return None
        return {
            "error_code": "commit_gate_blocked",
            "message": (
                f"Write blocked: {metrics.lines} uncommitted lines exceeds the limit of {CONFIG.hard.lines}. "
                "Call git_manage with action='commit', or call defer_commit with a reason."
            ),
            "lines": metrics.lines,
            "files": metrics.files,
        }


def after_tool_call(tool_name: str, args: dict[str, Any], result: Any) -> Any:
    with STATE_LOCK:
        if tool_name in _CHECK_TOOLS:
            STATE.last_check_status = _extract_status_from_check(tool_name, result)
            return result

        if tool_name == "git_manage":
            action = args.get("action")
            if action == "commit" and isinstance(result, dict) and result.get("exit_code") == 0:
                _reset_cycle(STATE)
            return result

        if not _is_write_tool(tool_name, args):
            return result
        if isinstance(result, dict) and result.get("error_code"):
            return result
        if tool_name == "apply_edits" and args.get("dry_run") is True:
            return result

        _ensure_cycle_initialized(STATE)

        STATE.write_calls += 1
        STATE.payload_chars += len(json.dumps(args, default=str, ensure_ascii=False))

        touched = _extract_touched_files(tool_name, args)
        if touched:
            _record_touch(STATE, touched)

        if STATE.defer_remaining_calls > 0:
            STATE.defer_remaining_calls -= 1
            STATE.last_check_status = "none"
            return result

        metrics = measure_diff(exclude_paths=STATE.baseline_deleted_files)
        level, reason = _evaluate(metrics, STATE)

        if CONFIG.enforcement == "gate" and not _is_merge_or_rebase_in_progress() and _hard_limit_exceeded(metrics):
            STATE.gate_block_next_write = True

        if level == "none" or CONFIG.enforcement == "off":
            STATE.pending_switch_from_module = None
            STATE.last_check_status = "none"
            return result

        STATE.last_nudge_level = level
        STATE.last_nudge_at_call = STATE.write_calls
        STATE.pending_switch_from_module = None
        STATE.last_check_status = "none"
        return _append_nudge(result, level, reason, metrics, STATE)


def defer_commit(
    action: Annotated[Literal["defer", "cancel", "status"], Field(description="defer starts a deferral, cancel clears it, status reports the current deferral.")] = "defer",
    reason: Annotated[str | None, Field(description="Why this work cannot be committed yet; required for defer.")] = None,
    calls: Annotated[int, Field(ge=1, le=50, description="Number of write calls to snooze commit nudges (default 5).")]=5,
) -> dict:
    with STATE_LOCK:
        _ensure_cycle_initialized(STATE)
        if action == "status":
            return {"ok": True, "deferred_calls": STATE.defer_remaining_calls, "defer_used_in_cycle": STATE.defer_used_in_cycle}
        if action == "cancel":
            previous = STATE.defer_remaining_calls
            STATE.defer_remaining_calls = 0
            return {"ok": True, "cancelled": previous > 0, "previous_deferred_calls": previous}
        if not reason or len(reason.strip()) < 3:
            return {"ok": False, "error_code": "missing_argument", "message": "defer requires a reason of at least 3 characters."}
        if STATE.defer_used_in_cycle:
            return {
                "ok": False,
                "error_code": "defer_already_used",
                "message": "defer_commit was already used since the last commit. Commit first, then defer again if needed.",
            }
        STATE.defer_remaining_calls = int(calls)
        STATE.defer_used_in_cycle = True
        entry = {
            "reason": reason,
            "calls": int(calls),
            "at_write_calls": STATE.write_calls,
        }
        DEFER_LOG.append(entry)
        return {
            "ok": True,
            "deferred_calls": STATE.defer_remaining_calls,
            "message": f"Commit nudges deferred for {STATE.defer_remaining_calls} write call(s).",
            "entry": entry,
        }



def register(mcp):
    return mcp.tool(description=describe(
        "Temporarily defer commit nudges when work is mid-refactor and cannot be committed yet. "
        "Actions: defer (default, reason required), cancel, or status. Deferral defaults to 5 write calls. "
        "This can be used once per commit cycle and every deferral is logged."
    ))(defer_commit)


def _reset_for_tests() -> None:
    with STATE_LOCK:
        _reset_cycle(STATE)
        STATE.base_head = None
        DEFER_LOG.clear()
