"""Worker subprocess supervisor for bounded tool-handler execution."""
from __future__ import annotations
import multiprocessing
import os
import signal
import time
import threading

_SEMAPHORE = threading.Semaphore(8)
_DEFAULT_DEADLINE = 30.0  # seconds


def _worker_target(fn, args, kwargs, result_conn):
    """Run inside the worker process; send result or exception back."""
    import traceback
    try:
        result = fn(*args, **kwargs)
        result_conn.send(("ok", result))
    except Exception as exc:
        result_conn.send(("error", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"))
    finally:
        result_conn.close()


def run_in_worker(fn, args=(), kwargs=None, deadline: float = _DEFAULT_DEADLINE) -> dict:
    """
    Run fn(*args, **kwargs) in a subprocess with a hard deadline.
    Returns the fn's return value dict, or a structured error dict.
    """
    kwargs = kwargs or {}
    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    start = time.monotonic()

    with _SEMAPHORE:
        proc = multiprocessing.Process(
            target=_worker_target,
            args=(fn, args, kwargs, child_conn),
            daemon=True,
        )
        # Start in new session so we can killpg on timeout
        if hasattr(proc, '_target'):
            pass  # multiprocessing handles this
        proc.start()
        child_conn.close()

        timed_out = False
        try:
            if parent_conn.poll(deadline):
                status, payload = parent_conn.recv()
            else:
                timed_out = True
                status, payload = "timeout", None
        except EOFError:
            status, payload = "crash", "Worker closed connection unexpectedly"
        finally:
            parent_conn.close()

        duration_ms = round((time.monotonic() - start) * 1000)

        if timed_out:
            # Kill the process and all its descendants
            if proc.is_alive():
                try:
                    if os.name != "nt":
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        except (ProcessLookupError, OSError):
                            proc.kill()
                    else:
                        proc.kill()
                except (ProcessLookupError, OSError):
                    pass
            proc.join(timeout=2)
            return {
                "error_code": "timeout",
                "message": f"Tool handler exceeded {deadline:.0f}s deadline",
                "duration_ms": duration_ms,
                "timed_out": True,
            }

        proc.join(timeout=5)

        if status == "ok":
            return payload
        elif status == "error":
            return {
                "error_code": "worker_crash",
                "message": str(payload),
                "duration_ms": duration_ms,
            }
        else:
            return {
                "error_code": "worker_crash",
                "message": f"Unexpected worker status: {status}",
                "duration_ms": duration_ms,
            }
