from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


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
    "explorer": Role(
        name="explorer",
        system_prompt=(
            "You are an explorer subagent. Collect evidence quickly, cite file paths, "
            "and avoid speculative claims."
        ),
        allowed_tools=("read_files", "search_text", "find_files", "subagent_status", "subagent_result"),
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
            "read_files",
            "search_text",
            "find_files",
            "code_get_diagnostics",
            "run_tests",
            "subagent_status",
            "subagent_result",
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
            "You are a planner subagent. Break work into concrete steps with dependencies "
            "and test checkpoints."
        ),
        allowed_tools=("read_files", "search_text", "find_files", "subagent_status", "subagent_result"),
        max_turns=6,
        max_tokens=1200,
        max_tool_calls=10,
        result_format="Ordered checklist with rationale and risk notes.",
        validator=_non_empty,
    ),
}


def get_role(name: str):
    return ROLES.get(name)
