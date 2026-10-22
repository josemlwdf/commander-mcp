from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from pydantic import Field

from .. import support
from ..support import describe


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _relative(path: Path) -> str:
    return str(path.relative_to(support.ROOT)).replace("\\", "/")


def _plan_content(user_task: str, context: str | None) -> str:
    context_text = context.strip() if context and context.strip() else "(none provided)"
    generated_at = _now_utc()
    return (
        "# Implementation Plan\n\n"
        f"_Generated: {generated_at}_\n\n"
        "## User Task\n\n"
        f"{user_task.strip()}\n\n"
        "## Context\n\n"
        f"{context_text}\n\n"
        "## Orchestrator Workflow\n\n"
        "- [ ] Spawn `subagent_run(role=\"generalist\", ...)` to assess scope, assumptions, and risks.\n"
        "- [ ] Save the generalist output into this implementation plan under **Generalist Assessment**.\n"
        "- [ ] Spawn `subagent_run(role=\"planner\", ...)` using this plan as context to produce non-interfering tasks.\n"
        "- [ ] Write planner output into `.handy_coder/tasks.md` with `[ ]` before each task item.\n"
        "- [ ] Execute tasks one-by-one by spawning `coder` subagents for implementation details.\n"
        "- [ ] Track task outcomes (`non_started`, `interrupted`, `complete`, `failed`) in `tasks.md`.\n\n"
        "## Orchestrator Memory Policy\n\n"
        "- Keep only minimal coordination context in the parent thread (current task, status, blockers).\n"
        "- Delegate substantial analysis or implementation to subagents instead of solving inline.\n"
        "- Prefer another subagent run whenever work spans multiple files or requires more than one reasoning step.\n\n"
        "## Generalist Assessment\n\n"
        "_Pending: fill with subagent output._\n\n"
        "## Validation Strategy\n\n"
        "_Pending: define diagnostics/tests to run once implementation tasks are complete._\n"
    )


def _tasks_content(user_task: str) -> str:
    generated_at = _now_utc()
    return (
        "# Task Breakdown\n\n"
        f"_Generated: {generated_at}_\n\n"
        "Source goal:\n\n"
        f"> {user_task.strip()}\n\n"
        "Use this format for each line so status is explicit:\n"
        "`- [ ] [status: non_started|interrupted|complete|failed] <task>`\n\n"
        "- [ ] [status: non_started] Run `generalist` subagent and capture assessment into `implementation-plan.md`.\n"
        "- [ ] [status: non_started] Run `planner` subagent to create independent, non-interfering implementation tasks.\n"
        "- [ ] [status: non_started] Replace this bootstrap list with planner-produced tasks while preserving `[ ]` prefixes.\n"
        "- [ ] [status: non_started] Execute each task by spawning `coder` subagents and recording outcomes.\n"
        "- [ ] [status: non_started] Keep the parent agent as an orchestrator only; delegate non-trivial work to subagents.\n"
    )


def _write_text(path: Path, content: str, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(str(path))
    path.write_text(content, encoding="utf-8")


def orchestrator_bootstrap(
    user_task: Annotated[str, Field(min_length=3, description="High-level user request the orchestrator should break down into a plan and task tracker.")],
    context: Annotated[str | None, Field(description="Optional additional context to include in the generated implementation plan.")] = None,
    overwrite: Annotated[bool, Field(description="When true, overwrite existing `.handy_coder/implementation-plan.md` and `.handy_coder/tasks.md` files.")] = False,
) -> dict:
    state_dir = support.ensure_handy_coder_state_dir()
    plan_path = state_dir / "implementation-plan.md"
    tasks_path = state_dir / "tasks.md"

    existing = [p for p in (plan_path, tasks_path) if p.exists()]
    if existing and not overwrite:
        return {
            "error_code": "already_exists",
            "message": "Plan artifacts already exist. Pass overwrite=true to replace them.",
            "state_dir": _relative(state_dir),
            "existing_files": [_relative(p) for p in existing],
        }

    _write_text(plan_path, _plan_content(user_task, context), overwrite=True)
    _write_text(tasks_path, _tasks_content(user_task), overwrite=True)

    return {
        "created": True,
        "state_dir": _relative(state_dir),
        "implementation_plan": _relative(plan_path),
        "tasks_file": _relative(tasks_path),
        "next_steps": [
            "Run subagent_run with role='generalist' to assess scope and risks.",
            "Update implementation-plan.md with the generalist result.",
            "Run subagent_run with role='planner' to produce independent actionable tasks.",
            "Replace bootstrap tasks in tasks.md with planner output while keeping [ ] placeholders.",
            "Execute each task via coder subagents and update status labels.",
            "Keep the parent thread lightweight: orchestrate, summarize, and spawn more subagents when work expands.",
        ],
    }


def register(mcp):
    return mcp.tool(description=describe(
        "Create `.handy_coder/implementation-plan.md` and `.handy_coder/tasks.md` from a single high-level request. "
        "This bootstraps an orchestrator-first workflow where generalist/planner/coder subagents are the default execution path and the parent agent keeps minimal retained context. "
        "tasks.md lines start with [ ] and include status labels for non_started, interrupted, complete, or failed. "
        "When to use: Start orchestrated multi-step work with persistent plan artifacts and frequent subagent delegation."
    ))(orchestrator_bootstrap)
