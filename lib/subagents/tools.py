from __future__ import annotations

import hashlib
import os
import threading
from typing import Annotated, Any

from pydantic import Field

from ..support import describe
from . import loop
from .manager import SubagentManager
from .resolve import resolve_backend
from .roles import Role, get_role, role_names


_MANAGER = SubagentManager()
_BACKEND = "cooperative"
_CLIENT_CAPABILITIES: dict[str, Any] | None = None
_SAMPLING_CALLBACK = None
_THREADS: dict[str, threading.Thread] = {}


def configure_runtime(*, client_capabilities: dict[str, Any] | None = None, sampling_callback=None) -> None:
    global _BACKEND, _CLIENT_CAPABILITIES, _SAMPLING_CALLBACK
    _CLIENT_CAPABILITIES = client_capabilities
    _SAMPLING_CALLBACK = sampling_callback
    _BACKEND = resolve_backend(client_capabilities)


def _backend() -> str:
    return _BACKEND or "cooperative"


def _available_roles_text() -> str:
    return ", ".join(role_names())


def _instructions(role_name: str, task: str, context: str | None, deliverable: str | None) -> str:
    role = get_role(role_name)
    if role is None:
        raise ValueError(f"Unknown role: {role_name}")
    parts = [
        f"You are now acting as subagent role '{role.name}'.",
        role.system_prompt,
        f"Goal: {task}",
        f"Allowed tools: {', '.join(role.allowed_tools)}",
        f"Tool-call budget: {role.max_tool_calls}",
        f"Expected result format: {role.result_format}",
    ]
    if context:
        parts.append(f"Context: {context}")
    if deliverable:
        parts.append(f"Deliverable: {deliverable}")
    parts.append("Finish by calling subagent_complete with your final result.")
    parts.append("Do not resume the parent task before subagent_complete succeeds.")
    return "\n".join(parts)


def _wait_seconds() -> float:
    raw = (os.environ.get("SUBAGENT_WAIT_SECONDS") or "15").strip()
    try:
        return max(0.5, min(float(raw), 240.0))
    except ValueError:
        return 15.0


def _loop_runner(task_id: str, role_obj: Role) -> None:
    try:
        task_obj = _MANAGER.start_task(task_id)
        result, turns, tokens = loop.run_loop(
            task_obj.backend,
            role_obj,
            task_obj.task,
            task_obj.context,
            task_obj.deliverable,
            sampling_fn=_SAMPLING_CALLBACK,
        )
        invalid = role_obj.validator(result)
        if invalid:
            raise ValueError(invalid)
        _MANAGER.complete_task(task_id, result, turns=turns, tokens=tokens)
    except Exception as exc:  # noqa: BLE001
        _MANAGER.fail_task(task_id, str(exc))
    finally:
        _THREADS.pop(task_id, None)


def _launch_loop(task_id: str, role_obj: Role) -> None:
    thread = threading.Thread(target=_loop_runner, args=(task_id, role_obj), daemon=True, name=f"subagent-{task_id}")
    _THREADS[task_id] = thread
    thread.start()


def _terminal_payload(task_id: str) -> dict:
    task_obj = _MANAGER.get_task(task_id)
    if task_obj.state == "done":
        return {
            "status": "completed",
            "task_id": task_obj.task_id,
            "backend": task_obj.backend,
            "result": task_obj.result or "",
            "usage": {"turns": task_obj.turns_used, "tokens": task_obj.tokens_used},
        }
    if task_obj.state in {"failed", "cancelled"}:
        return {
            "status": "failed",
            "task_id": task_obj.task_id,
            "backend": task_obj.backend,
            "error": task_obj.error or f"task ended with state {task_obj.state}",
        }
    return {
        "status": "running",
        "task_id": task_obj.task_id,
        "backend": task_obj.backend,
        "poll": "subagent_status",
    }


def subagent_run(
    role: Annotated[str, Field(description="Registered subagent role name, e.g. generalist, coder, explorer, reviewer, planner.")],
    task: Annotated[str, Field(description="Task for the subagent to perform in isolation from the parent flow.")],
    context: Annotated[str | None, Field(description="Optional context to pass to the subagent, such as branch, files, or constraints.")] = None,
    deliverable: Annotated[str | None, Field(description="Optional explicit deliverable format for the subagent result.")] = None,
    wait: Annotated[bool, Field(description="When true (default), wait for completion when the backend can finish quickly. Cooperative mode always delegates to host.")] = True,
) -> dict:
    selected_backend = _backend()
    role_obj = get_role(role)
    if role_obj is None:
        raise ValueError(f"Unknown role '{role}'. Available roles: {_available_roles_text()}")

    task_obj = _MANAGER.create_task(role, task, selected_backend, context, deliverable)

    if selected_backend == "cooperative":
        frame = _MANAGER.make_frame(task_obj.task_id, role_obj, task)
        _MANAGER.mark_delegated(task_obj.task_id, frame)
        return {
            "status": "delegated_to_host",
            "task_id": task_obj.task_id,
            "backend": selected_backend,
            "instructions": _instructions(role, task, context, deliverable),
            "wait": bool(wait),
        }

    _launch_loop(task_obj.task_id, role_obj)
    if wait:
        thread = _THREADS.get(task_obj.task_id)
        if thread is not None:
            thread.join(timeout=_wait_seconds())
    return _terminal_payload(task_obj.task_id)


def subagent_status(
    task_id: Annotated[str, Field(description="Task identifier returned by subagent_run.")],
) -> dict:
    task_obj = _MANAGER.get_task(task_id)
    payload = {
        "task_id": task_obj.task_id,
        "state": task_obj.state,
        "backend": task_obj.backend,
        "usage": {"turns": task_obj.turns_used, "tokens": task_obj.tokens_used},
    }
    if task_obj.error:
        payload["error"] = task_obj.error
    return payload


def subagent_result(
    task_id: Annotated[str, Field(description="Task identifier returned by subagent_run.")],
    offset: Annotated[int | None, Field(ge=0, description="Optional 0-based character offset for paging long results.")] = None,
) -> dict:
    return _MANAGER.get_result_page(task_id, int(offset or 0))


def subagent_complete(
    task_id: Annotated[str, Field(description="Open delegated task id to complete in cooperative mode.")],
    result: Annotated[str, Field(max_length=100000, description="Final subagent result text, at most 100,000 characters. Keep it concise or page supporting detail with subagent_result. Must match the role's expected output format.")],
) -> dict:
    task_obj = _MANAGER.get_task(task_id)
    if task_obj.backend != "cooperative":
        raise ValueError("subagent_complete is only valid for cooperative mode tasks")
    role = get_role(task_obj.role)
    if role is None:
        raise ValueError(f"Unknown role on task: {task_obj.role}")
    invalid = role.validator(result)
    if invalid:
        raise ValueError(f"Result validation failed: {invalid}")

    done = _MANAGER.complete_task(task_id, result)
    digest = hashlib.sha1(result.encode("utf-8")).hexdigest()[:10]
    first_line = result.strip().splitlines()[0] if result.strip() else ""
    return {
        "status": "completed",
        "task_id": done.task_id,
        "backend": done.backend,
        "digest": f"{digest}: {first_line[:180]}",
        "resume": f"Subagent {done.task_id} completed. Resume parent goal: {done.task}",
    }


def subagent_cancel(
    task_id: Annotated[str, Field(description="Task identifier to cancel.")],
    reason: Annotated[str | None, Field(max_length=2000, description="Optional cancellation reason recorded with the task.")] = None,
    confirm: Annotated[bool, Field(description="Must be true to confirm cancellation; false returns a confirmation-required response.")] = False,
) -> dict:
    if not confirm:
        return {"task_id": task_id, "cancelled": False, "confirmation_required": True, "message": "Call again with confirm=true to cancel this task."}
    if thread := _THREADS.get(task_id):
        if thread.is_alive():
            # Cancellation is cooperative: mark task and let running call end naturally.
            pass
    task_obj = _MANAGER.cancel_task(task_id, reason=reason)
    return {
        "task_id": task_obj.task_id,
        "state": task_obj.state,
        "backend": task_obj.backend,
        **({"reason": reason} if reason else {}),
    }


def before_tool_call(tool_name: str, _: dict[str, Any]) -> dict | None:
    if tool_name == "subagent_complete":
        return None
    ok, reason = _MANAGER.consume_frame_tool_call(tool_name)
    if ok:
        return None
    return {
        "error_code": "subagent_scope_violation",
        "message": reason,
        "backend": _backend(),
    }


def after_tool_call(tool_name: str, result: Any, args: dict[str, Any] | None = None) -> Any:
    if tool_name.startswith("subagent_"):
        return result
    frame = _MANAGER.open_frame()
    if (
        tool_name == "git_manage"
        and isinstance(args, dict)
        and args.get("action") == "commit"
        and isinstance(result, dict)
        and result.get("exit_code") == 0
        and frame is not None
        and frame.role.name == "committer"
    ):
        summary = "Commit completed successfully."
        output = result.get("stdout")
        if isinstance(output, str) and output.strip():
            summary += " " + output.strip()
        completed = _MANAGER.complete_task(frame.task_id, summary)
        out = dict(result)
        out["subagent_completion"] = {
            "status": "completed",
            "task_id": completed.task_id,
            "summary": summary,
        }
        return out
    reminder = _MANAGER.reminder()
    if not reminder or not isinstance(result, dict):
        return result
    out = dict(result)
    out["subagent_reminder"] = reminder
    if isinstance(out.get("message"), str):
        out["message"] = out["message"].rstrip() + "\n" + reminder
    return out


def register(mcp):
    configure_runtime(client_capabilities=None)
    base = (
        "Delegate multi-step work to an internal subagent contract with stable status shapes. "
        "Default to orchestration-first behavior: keep parent context minimal and delegate analysis, planning, implementation, and review to subagents whenever a task is not a single quick lookup. "
        "Use this especially for broad searches, independent reviews, summaries of large inputs, and any change touching multiple files or steps. "
        "When status is completed, consume result; when running, poll subagent_status; "
        "when delegated_to_host, follow instructions then call subagent_complete. Cooperative tasks expire after five minutes; successful git_manage commits by a committer close that frame automatically, while skipped or unsuccessful commits and other roles require subagent_complete. Streaming and checkpoint/resume are unsupported. Roles: generalist/explorer are read-only, reviewer adds diagnostics/tests, planner can also create/edit, coder can create/edit plus diagnostics/tests. Result pages include offset, total_length, and next_offset metadata."
    )
    mcp.tool(description=describe(base + " Start a task."))(subagent_run)
    mcp.tool(description=describe(base + " Check task state and usage."))(subagent_status)
    mcp.tool(description=describe(base + " Fetch full or paged task result."))(subagent_result)
    mcp.tool(description=describe(base + " Complete a cooperative delegated frame with final result text."))(subagent_complete)
    return mcp.tool(description=describe(base + " Cancel a task and close any open frame."))(subagent_cancel)

