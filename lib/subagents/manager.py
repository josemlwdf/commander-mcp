from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .roles import Role


TaskState = Literal["queued", "running", "delegated", "done", "failed", "cancelled"]


@dataclass
class Task:
    task_id: str
    role: str
    backend: str
    task: str
    deliverable: str | None
    context: str | None
    state: TaskState
    created_at: float
    updated_at: float
    result: str | None = None
    error: str | None = None
    turns_used: int = 0
    tokens_used: int = 0
    expires_at: float = 0.0


@dataclass
class Frame:
    task_id: str
    role: Role
    parent_goal: str
    tool_calls_left: int
    opened_at: float
    expires_at: float


@dataclass
class SubagentLimits:
    concurrent_tasks: int = 3
    tasks_per_session: int = 20
    task_ttl_seconds: int = 1200
    result_page_chars: int = 8000


class SubagentManager:
    def __init__(self, limits: SubagentLimits | None = None) -> None:
        self.limits = limits or SubagentLimits()
        self._lock = threading.RLock()
        self._tasks: dict[str, Task] = {}
        self._stack: list[Frame] = []
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"t_{self._seq}"

    def _sweep_locked(self) -> None:
        now = time.time()
        stale_ids = [task_id for task_id, task in self._tasks.items() if task.expires_at and now > task.expires_at and task.state in {"queued", "running", "delegated"}]
        for task_id in stale_ids:
            task = self._tasks[task_id]
            task.state = "failed"
            task.error = "task expired"
            task.updated_at = now

        while self._stack and now > self._stack[-1].expires_at:
            frame = self._stack.pop()
            task = self._tasks.get(frame.task_id)
            if task and task.state == "delegated":
                task.state = "failed"
                task.error = "cooperative frame expired before subagent_complete"
                task.updated_at = now

    def create_task(self, role: str, task_text: str, backend: str, context: str | None, deliverable: str | None) -> Task:
        with self._lock:
            self._sweep_locked()
            if len(self._tasks) >= self.limits.tasks_per_session:
                raise ValueError("subagent task limit reached for this session")
            running_count = sum(1 for t in self._tasks.values() if t.state in {"queued", "running", "delegated"})
            if running_count >= self.limits.concurrent_tasks:
                raise ValueError("subagent concurrency cap reached")
            now = time.time()
            task_obj = Task(
                task_id=self._next_id(),
                role=role,
                backend=backend,
                task=task_text,
                deliverable=deliverable,
                context=context,
                state="queued",
                created_at=now,
                updated_at=now,
                expires_at=now + self.limits.task_ttl_seconds,
            )
            self._tasks[task_obj.task_id] = task_obj
            return task_obj

    def mark_delegated(self, task_id: str, frame: Frame) -> None:
        with self._lock:
            self._sweep_locked()
            task = self._tasks[task_id]
            task.state = "delegated"
            task.updated_at = time.time()
            self._stack.append(frame)

    def complete_task(self, task_id: str, result: str, *, turns: int = 0, tokens: int = 0) -> Task:
        with self._lock:
            self._sweep_locked()
            task = self._tasks[task_id]
            task.result = result
            task.state = "done"
            task.turns_used = turns
            task.tokens_used = tokens
            task.updated_at = time.time()
            if self._stack and self._stack[-1].task_id == task_id:
                self._stack.pop()
            return task

    def fail_task(self, task_id: str, message: str) -> Task:
        with self._lock:
            self._sweep_locked()
            task = self._tasks[task_id]
            task.state = "failed"
            task.error = message
            task.updated_at = time.time()
            self._stack = [f for f in self._stack if f.task_id != task_id]
            return task

    def cancel_task(self, task_id: str) -> Task:
        with self._lock:
            self._sweep_locked()
            task = self._tasks[task_id]
            task.state = "cancelled"
            task.updated_at = time.time()
            self._stack = [f for f in self._stack if f.task_id != task_id]
            return task

    def get_task(self, task_id: str) -> Task:
        with self._lock:
            self._sweep_locked()
            if task_id not in self._tasks:
                raise ValueError(f"Unknown task_id: {task_id}")
            return self._tasks[task_id]

    def get_result_page(self, task_id: str, offset: int = 0) -> dict[str, Any]:
        with self._lock:
            self._sweep_locked()
            task = self.get_task(task_id)
            text = task.result or ""
            if offset < 0:
                raise ValueError("offset must be >= 0")
            page = text[offset:offset + self.limits.result_page_chars]
            next_offset = offset + self.limits.result_page_chars
            if next_offset >= len(text):
                next_offset = None
            return {
                "task_id": task_id,
                "backend": task.backend,
                "state": task.state,
                "offset": offset,
                "result": page,
                "total_length": len(text),
                "next_offset": next_offset,
            }

    def open_frame(self) -> Frame | None:
        with self._lock:
            self._sweep_locked()
            return self._stack[-1] if self._stack else None

    def make_frame(self, task_id: str, role: Role, parent_goal: str) -> Frame:
        now = time.time()
        return Frame(
            task_id=task_id,
            role=role,
            parent_goal=parent_goal,
            tool_calls_left=role.max_tool_calls,
            opened_at=now,
            expires_at=now + self.limits.task_ttl_seconds,
        )

    def consume_frame_tool_call(self, tool_name: str) -> tuple[bool, str | None]:
        with self._lock:
            self._sweep_locked()
            frame = self._stack[-1] if self._stack else None
            if not frame:
                return True, None
            if tool_name in {"subagent_status", "subagent_result"}:
                return True, None
            if tool_name not in frame.role.allowed_tools and tool_name not in {"subagent_complete", "subagent_cancel"}:
                return False, (
                    f"Tool '{tool_name}' is outside role allowlist for open subagent frame {frame.task_id}. "
                    "Call subagent_complete when done."
                )
            if tool_name == "subagent_run":
                return False, "subagent nesting depth is limited to 1; finish current frame first"
            if tool_name not in {"subagent_complete", "subagent_cancel"}:
                if frame.tool_calls_left <= 0:
                    return False, (
                        f"Subagent frame {frame.task_id} exceeded its tool-call budget. "
                        "Call subagent_complete with current findings."
                    )
                frame.tool_calls_left -= 1
            return True, None

    def reminder(self) -> str | None:
        frame = self.open_frame()
        if not frame:
            return None
        return f"[subagent {frame.task_id} open: call subagent_complete when done]"
