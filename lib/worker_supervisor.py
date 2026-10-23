"""Worker subprocess supervisor for bounded and isolated tool-handler execution."""
from __future__ import annotations

import functools
import importlib
import inspect
import multiprocessing
import os
import signal
import threading
import time
from typing import Any, Callable

from .core import commit_nudge
from .subagents import tools as subagent_tools

_MP_CTX = multiprocessing.get_context("spawn")
_CONCURRENT_WORKER_LIMIT = 8
_SEMAPHORE = threading.Semaphore(_CONCURRENT_WORKER_LIMIT)
DEFAULT_WORKER_DEADLINE = 30.0  # seconds
_NO_WORKER_TOOLS = {
    "defer_commit",
    "subagent_run",
    "subagent_status",
    "subagent_result",
    "subagent_complete",
    "subagent_cancel",
}


def _worker_entrypoint(mod_name: str, func_name: str, args: tuple, kwargs: dict, conn, root_str: str | None, shell_str: str | None) -> None:
    """Entry point in worker process; creates separate process group and executes tool."""
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            try:
                os.setpgrp()
            except OSError:
                pass

    from . import support
    if root_str:
        try:
            support.configure(root_str, shell_str)
        except Exception:
            pass

    try:
        mod = importlib.import_module(mod_name)
        fn = getattr(mod, func_name)
        result = fn(*args, **kwargs)

        metadata = {}
        try:
            from .core import git_manage
            if git_manage._REPOSITORIES is not None:
                metadata["git_description"] = git_manage._tool_description()
        except Exception:
            pass

        conn.send(("ok", result, metadata))
    except Exception as exc:
        conn.send(("exception", exc))
    finally:
        conn.close()


def run_tool_in_worker(
    fn: Callable,
    args: tuple = (),
    kwargs: dict | None = None,
    deadline: float = DEFAULT_WORKER_DEADLINE,
    mcp: Any = None,
) -> dict:
    """Run a tool callable in an isolated worker subprocess with a strict deadline."""
    kwargs = kwargs or {}
    start_time = time.monotonic()
    parent_conn, child_conn = _MP_CTX.Pipe(duplex=False)

    from . import support
    root_str = str(support.ROOT) if support.ROOT else None
    shell_str = support.SHELL

    mod_name = fn.__module__
    func_name = fn.__name__

    with _SEMAPHORE:
        proc = _MP_CTX.Process(
            target=_worker_entrypoint,
            args=(mod_name, func_name, args, kwargs, child_conn, root_str, shell_str),
            daemon=True,
        )
        proc.start()
        child_conn.close()

        timed_out = False
        try:
            if parent_conn.poll(deadline):
                status, payload, *extra = parent_conn.recv()
            else:
                timed_out = True
                status, payload = "timeout", None
                extra = []
        except (EOFError, BrokenPipeError):
            status, payload = "crash", None
            extra = []
        finally:
            parent_conn.close()

        duration_ms = round((time.monotonic() - start_time) * 1000)

        if timed_out:
            if proc.is_alive():
                try:
                    if os.name != "nt":
                        try:
                            pgid = os.getpgid(proc.pid)
                            os.killpg(pgid, signal.SIGKILL)
                        except (ProcessLookupError, OSError):
                            proc.kill()
                    else:
                        proc.kill()
                except (ProcessLookupError, OSError):
                    pass
            proc.join(timeout=2)
            return {
                "error_code": "timeout",
                "message": f"Tool execution timed out after {deadline:g}s",
                "duration_ms": duration_ms,
                "timed_out": True,
                "timeout_ms": int(deadline * 1000),
            }

        proc.join(timeout=3)

        if status == "ok":
            if extra and isinstance(extra[0], dict) and "git_description" in extra[0] and mcp is not None:
                try:
                    from .core import git_manage
                    git_manage._update_tool_description(mcp, extra[0]["git_description"])
                except Exception:
                    pass
            return payload

        if status == "exception":
            raise payload

        return {
            "error_code": "worker_crash",
            "message": f"Tool worker process terminated unexpectedly (exit code {proc.exitcode})",
            "duration_ms": duration_ms,
        }


def supervise_fastmcp_tools(mcp: Any) -> None:
    """Wrap registered FastMCP tools so incoming tool calls run through the worker supervisor."""
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:
        return
    tools = getattr(manager, "_tools", {})
    for name, tool in list(tools.items()):
        original_fn = tool.fn
        original_signature = inspect.signature(original_fn)

        @functools.wraps(original_fn)
        def supervised_wrapper(*args, _target_fn=original_fn, _tool_name=name, _sig=original_signature, **kwargs):
            try:
                bound = _sig.bind_partial(*args, **kwargs)
                bound_args = dict(bound.arguments)
            except TypeError:
                bound_args = dict(kwargs)

            scope_block = subagent_tools.before_tool_call(_tool_name, bound_args)
            if scope_block is not None:
                return scope_block

            gate_block = commit_nudge.before_tool_call(_tool_name, bound_args)
            if gate_block is not None:
                return subagent_tools.after_tool_call(_tool_name, gate_block)

            if _tool_name in _NO_WORKER_TOOLS:
                result = _target_fn(*args, **kwargs)
            else:
                deadline = DEFAULT_WORKER_DEADLINE
                if _tool_name in {"terminal_exec", "build_project"}:
                    timeout_ms = kwargs.get("timeout_ms")
                    if timeout_ms is not None:
                        try:
                            ms = int(timeout_ms)
                            deadline = max(30.0, (ms / 1000.0) + 5.0)
                        except (ValueError, TypeError):
                            pass
                result = run_tool_in_worker(_target_fn, args, kwargs, deadline=deadline, mcp=mcp)
            result = commit_nudge.after_tool_call(_tool_name, bound_args, result)
            return subagent_tools.after_tool_call(_tool_name, result, bound_args)

        supervised_wrapper.__signature__ = inspect.signature(original_fn)
        supervised_wrapper.__annotations__ = getattr(original_fn, "__annotations__", {})
        tool.fn = supervised_wrapper

