from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


_READ_ONLY_TOOLS = ("read_files", "search_text", "find_files", "subagent_status", "subagent_result")
_WRITE_TOOLS = ("create_file", "apply_edits")


Validator = Callable[[str], str | None]


@dataclass(frozen=True)
class Role:
    name: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    max_turns: int
    max_tokens: int
    max_tool_calls: int
    result_format: str
    validator: Validator


def _non_empty(result: str) -> str | None:
    if not result.strip():
        return "result must not be empty"
    return None


def _reviewer_result(result: str) -> str | None:
    if not result.strip():
        return "result must not be empty"
    if "finding" not in result.lower() and "none" not in result.lower():
        return "reviewer results should mention findings (or explicitly say none)"
    return None


ROLES: dict[str, Role] = {
    "generalist": Role(
        name="generalist",
        system_prompt=(
            "You are a generalist subagent. Assess user goals, clarify scope, and produce an "
            "implementation plan focused on high-leverage execution steps. Optimize for an orchestrator that retains only minimal context and delegates the rest."
        ),
        allowed_tools=_READ_ONLY_TOOLS,
        max_turns=8,
        max_tokens=1800,
        max_tool_calls=12,
        result_format=(
            "Markdown implementation plan with sections: Objective, Assumptions, Scope, Risks, "
            "Execution Strategy, and Validation."
        ),
        validator=_non_empty,
    ),
    "explorer": Role(
        name="explorer",
        system_prompt=(
            "You are an explorer subagent. Collect evidence quickly, cite file paths, "
            "and avoid speculative claims."
        ),
        allowed_tools=_READ_ONLY_TOOLS,
        max_turns=8,
        max_tokens=1500,
        max_tool_calls=12,
        result_format="Bullet list with evidence and one concise conclusion.",
        validator=_non_empty,
    ),
    "reviewer": Role(
        name="reviewer",
        system_prompt=(
            "You are a reviewer subagent. Audit the requested surface area, identify risks, "
            "and separate confirmed defects from suggestions."
        ),
        allowed_tools=(
            *_READ_ONLY_TOOLS,
            "code_get_diagnostics",
            "run_tests",
        ),
        max_turns=8,
        max_tokens=1800,
        max_tool_calls=16,
        result_format="Sections: Findings, Evidence, Suggested next step.",
        validator=_reviewer_result,
    ),
    "planner": Role(
        name="planner",
        system_prompt=(
            "You are a planner subagent. Turn an implementation plan into independent, "
            "actionable tasks that can be executed without interfering with each other. Design tasks so an orchestrator can dispatch them one-by-one to coder subagents."
        ),
        allowed_tools=(
            *_READ_ONLY_TOOLS,
            *_WRITE_TOOLS,
        ),
        max_turns=6,
        max_tokens=1200,
        max_tool_calls=10,
        result_format=(
            "Markdown checklist for tasks.md where every task starts with [ ] and includes "
            "target files plus a short non-interference note."
        ),
        validator=_non_empty,
    ),
    "coder": Role(
        name="coder",
        system_prompt=(
            "You are a coder subagent. Execute one scoped task at a time, propose concrete "
            "file changes, and include validation steps for the orchestrator. Keep output self-contained so the parent agent does not need to retain implementation detail."
        ),
        allowed_tools=(
            *_READ_ONLY_TOOLS,
            *_WRITE_TOOLS,
            "code_get_diagnostics",
            "run_tests",
        ),
        max_turns=10,
        max_tokens=2200,
        max_tool_calls=18,
        result_format=(
            "Implementation notes with exact file paths, patch intent, validation output, and "
            "any follow-up risks."
        ),
        validator=_non_empty,
    ),
    "committer": Role(
        name="committer",
        system_prompt=(
            "You are a commit subagent. Inspect the repository status and diff before acting. "
            "Review .gitignore and add missing rules for generated artifacts when needed. "
            "Stage only the changed files belonging to this task, write a concise commit message, "
            "and create the commit with git_manage. Never push. If the worktree includes unrelated "
            "changes or the intended files cannot be identified safely, do not commit and report why."
        ),
        allowed_tools=(*_READ_ONLY_TOOLS, "git_manage"),
        max_turns=8,
        max_tokens=1200,
        max_tool_calls=12,
        result_format="One short status: commit hash and message, or why no commit was made.",
        validator=_non_empty,
    ),
}


def get_role(name: str):
    return ROLES.get(name)


def role_names() -> tuple[str, ...]:
    return tuple(ROLES.keys())
