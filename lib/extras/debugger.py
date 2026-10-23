"""Optional client for a locally configured Debug Adapter Protocol server."""
from __future__ import annotations

import ipaddress
import json
import multiprocessing
import os
import socket
import threading
import time
from collections import deque
from typing import Annotated, Literal

from pydantic import Field
from ..support import inside, describe, setup_required


_LOCK = threading.Lock()
_WORKER_PROC: multiprocessing.Process | None = None
_PARENT_CONN = None


class _DapClient:
    def __init__(self, host: str, port: int):
        self.socket = socket.create_connection((host, port), timeout=5)
        self.socket.settimeout(10)
        self.reader = self.socket.makefile("rb")
        self.seq = 1
        self.events = deque()

    def close(self):
        try:
            self.reader.close()
        finally:
            self.socket.close()

    def _receive(self) -> dict:
        length = None
        while True:
            line = self.reader.readline()
            if not line:
                raise RuntimeError("DAP server closed the connection")
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode("ascii").partition(":")
            if key.lower() == "content-length":
                length = int(value.strip())
        if length is None or not 1 <= length <= 2 * 1024 * 1024:
            raise RuntimeError("DAP server returned an invalid Content-Length")
        payload = self.reader.read(length)
        if len(payload) != length:
            raise RuntimeError("DAP server closed during a response")
        return json.loads(payload)

    def request(self, command: str, arguments: dict | None = None) -> dict:
        seq = self.seq
        self.seq += 1
        payload = json.dumps({
            "seq": seq, "type": "request", "command": command, "arguments": arguments or {},
        }).encode()
        self.socket.sendall(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
        while True:
            message = self._receive()
            if message.get("type") == "event":
                self.events.append(message)
                continue
            if message.get("request_seq") != seq:
                continue
            if not message.get("success", False):
                raise RuntimeError(f"DAP {command} failed: {message.get('message', 'unknown adapter error')}")
            return message.get("body") or {}

    def wait_event(self, name: str, timeout: float = 10) -> dict:
        for event in list(self.events):
            if event.get("event") == name:
                self.events.remove(event)
                return event
        self.socket.settimeout(timeout)
        try:
            while True:
                message = self._receive()
                if message.get("type") == "event":
                    if message.get("event") == name:
                        return message
                    self.events.append(message)
        finally:
            self.socket.settimeout(10)

    def stopped_thread(self) -> int:
        for event in reversed(self.events):
            if event.get("event") == "stopped":
                return int(event.get("body", {}).get("threadId"))
        event = self.wait_event("stopped")
        return int(event.get("body", {}).get("threadId"))


def _configured_endpoint() -> tuple[str, int] | None:
    host = os.environ.get("HANDY-CODER_DAP_HOST", "127.0.0.1").strip()
    raw_port = os.environ.get("HANDY-CODER_DAP_PORT", "").strip()
    if not raw_port:
        return None
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("HANDY-CODER_DAP_PORT must be an integer") from exc
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        if host != "localhost":
            raise ValueError("HANDY-CODER_DAP_HOST must be localhost or a loopback IP address") from exc
        address = ipaddress.ip_address("127.0.0.1")
    if not address.is_loopback:
        raise ValueError("DAP connections are restricted to localhost")
    if not 1 <= port <= 65535:
        raise ValueError("HANDY-CODER_DAP_PORT must be between 1 and 65535")
    return host, port


def _start(client: _DapClient, breakpoint_file: str | None, line: int | None, raw_config: str, adapter_id: str) -> None:
    if not raw_config:
        raise ValueError("Set HANDY-CODER_DAP_START_REQUEST to a JSON launch/attach request before starting a session")
    config = json.loads(raw_config)
    if not isinstance(config, dict) or config.get("command") not in ("launch", "attach") or not isinstance(config.get("arguments"), dict):
        raise ValueError("HANDY-CODER_DAP_START_REQUEST must contain command=launch|attach and an arguments object")
    arguments = config["arguments"]
    if breakpoint_file is None and line is not None:
        raise ValueError("breakpoint_file is required when line is provided")
    if isinstance(arguments.get("program"), str):
        program = inside(arguments["program"], must_exist=True)
        if not program.is_file():
            raise ValueError("Configured DAP program must be a workspace file")
        arguments["program"] = str(program)
    if isinstance(arguments.get("cwd"), str):
        working_directory = inside(arguments["cwd"], must_exist=True)
        if not working_directory.is_dir():
            raise ValueError("Configured DAP cwd must be a workspace directory")
        arguments["cwd"] = str(working_directory)
    client.request("initialize", {
        "clientID": "handy-coder-mcp",
        "clientName": "Handy Coder MCP",
        "adapterID": adapter_id,
        "pathFormat": "path",
        "linesStartAt1": True,
        "columnsStartAt1": True,
        "supportsVariableType": True,
        "supportsRunInTerminalRequest": False,
    })
    client.wait_event("initialized")
    if breakpoint_file:
        if line is None or line < 1:
            raise ValueError("line must be 1 or greater when breakpoint_file is provided")
        path = inside(breakpoint_file, must_exist=True)
        if not path.is_file():
            raise ValueError(f"Not a file: {breakpoint_file}")
        client.request("setBreakpoints", {
            "source": {"path": str(path)},
            "breakpoints": [{"line": line}],
            "sourceModified": False,
        })
    client.request(config["command"], arguments)
    client.request("configurationDone")


def _debugger_worker_main(conn, root_str: str | None = None, shell_str: str | None = None) -> None:
    """Supervised worker process loop maintaining DAP session state across calls."""
    from .. import support
    if root_str:
        try:
            support.configure(root_str, shell_str)
        except Exception:
            pass
    active_client: _DapClient | None = None
    try:
        while True:
            try:
                msg = conn.recv()
            except EOFError:
                break
            if msg == "shutdown":
                break
            action, breakpoint_file, line, endpoint, raw_config, adapter_id = msg
            try:
                if action == "start":
                    if active_client is not None:
                        raise ValueError("A debugger session is already active; stop it before starting another")
                    client = _DapClient(*endpoint)
                    try:
                        _start(client, breakpoint_file, line, raw_config, adapter_id)
                    except Exception:
                        client.close()
                        raise
                    active_client = client
                    conn.send(("ok", {
                        "supported": True,
                        "action": "start",
                        "message": "DAP session started; execution will stop at a configured breakpoint or adapter entry point.",
                    }))
                    continue

                if active_client is None:
                    raise ValueError("No active debugger session; use action='start' first")

                client = active_client
                if action == "stop":
                    try:
                        client.request("disconnect", {"terminateDebuggee": False})
                    finally:
                        client.close()
                        active_client = None
                    conn.send(("ok", {"supported": True, "action": "stop"}))
                    continue

                if action in ("step_over", "step_into"):
                    thread_id = client.stopped_thread()
                    client.request("next" if action == "step_over" else "stepIn", {"threadId": thread_id})
                    conn.send(("ok", {"supported": True, "action": action, "thread_id": thread_id}))
                    continue

                thread_id = client.stopped_thread()
                stack = client.request("stackTrace", {"threadId": thread_id, "startFrame": 0, "levels": 1})
                frames = stack.get("stackFrames", [])
                if not frames:
                    conn.send(("ok", {"supported": True, "variables": []}))
                    continue
                scopes = client.request("scopes", {"frameId": frames[0]["id"]}).get("scopes", [])
                variables = []
                for scope in scopes:
                    response = client.request("variables", {"variablesReference": scope["variablesReference"]})
                    variables.extend(response.get("variables", []))
                conn.send(("ok", {
                    "supported": True,
                    "thread_id": thread_id,
                    "frame": frames[0],
                    "variables": variables[:200],
                }))
            except Exception as exc:
                conn.send(("error", exc))
    finally:
        if active_client is not None:
            try:
                active_client.close()
            except Exception:
                pass
        conn.close()


_MP_CTX = multiprocessing.get_context("spawn")


def _ensure_worker() -> None:
    global _WORKER_PROC, _PARENT_CONN
    if _WORKER_PROC is not None and _WORKER_PROC.is_alive():
        return
    parent_conn, child_conn = _MP_CTX.Pipe(duplex=True)
    from .. import support
    root_str = str(support.ROOT) if support.ROOT else None
    shell_str = support.SHELL
    proc = _MP_CTX.Process(
        target=_debugger_worker_main,
        args=(child_conn, root_str, shell_str),
        daemon=True,
    )
    proc.start()
    child_conn.close()
    _WORKER_PROC = proc
    _PARENT_CONN = parent_conn


def _stop_worker() -> None:
    global _WORKER_PROC, _PARENT_CONN
    if _PARENT_CONN is not None:
        try:
            _PARENT_CONN.send("shutdown")
            _PARENT_CONN.close()
        except Exception:
            pass
        _PARENT_CONN = None
    if _WORKER_PROC is not None:
        try:
            _WORKER_PROC.join(timeout=1)
            if _WORKER_PROC.is_alive():
                _WORKER_PROC.kill()
                _WORKER_PROC.join(timeout=1)
        except Exception:
            pass
        _WORKER_PROC = None


def register(mcp):
    @mcp.tool(description=describe(
        "Control a local DAP adapter configured with HANDY-CODER_DAP_HOST/PORT and HANDY-CODER_DAP_START_REQUEST. "
        "When to use: Start, step through, inspect, or stop a debug session."
    ))
    def debug_session_step(
        action: Annotated[
            Literal["start", "step_over", "step_into", "inspect_vars", "stop"],
            Field(description=(
                "Debugger operation. "
                "start: begin a new session using HANDY-CODER_DAP_START_REQUEST (launch or attach). "
                "step_over: advance past the current line without entering function calls. "
                "step_into: step into the function call on the current line. "
                "inspect_vars: return variables visible in the current stack frame. "
                "stop: disconnect from the adapter and end the session."
            )),
        ],
        breakpoint_file: Annotated[
            str | None,
            Field(description="Optional workspace-relative source file for a breakpoint when starting the session; required if line is supplied."),
        ] = None,
        line: Annotated[
            int | None,
            Field(description="Optional 1-based breakpoint line used with breakpoint_file when starting a session."),
        ] = None,
    ) -> dict:
        endpoint = _configured_endpoint()
        if endpoint is None:
            response = setup_required(
                missing_requirements=["env:HANDY-CODER_DAP_PORT", "env:HANDY-CODER_DAP_START_REQUEST", "dap-adapter"],
                remediation_commands=["Set HANDY-CODER_DAP_PORT and HANDY-CODER_DAP_START_REQUEST for your adapter."],
                documentation_url="docs/SETUP.md#dap-debug_session_step",
                message="Configure a local DAP adapter to enable debugging.", error_code="not_configured",
            )
            response["env_var"] = "HANDY-CODER_DAP_PORT"
            response["hint"] = "HANDY-CODER_DAP_START_REQUEST must be JSON with command=launch|attach and arguments."
            return response
        # Validate cross-field constraint only after confirming the tool is configured.
        if line is not None and breakpoint_file is None:
            raise ValueError("breakpoint_file is required when line is provided")

        start_request = os.environ.get("HANDY-CODER_DAP_START_REQUEST", "").strip()
        adapter_id = os.environ.get("HANDY-CODER_DAP_ADAPTER_ID", "python")

        with _LOCK:
            _ensure_worker()
            start_time = time.monotonic()
            req = (action, breakpoint_file, line, endpoint, start_request, adapter_id)
            try:
                _PARENT_CONN.send(req)
                if not _PARENT_CONN.poll(30.0):
                    _stop_worker()
                    duration_ms = round((time.monotonic() - start_time) * 1000)
                    return {
                        "error_code": "timeout",
                        "message": "Debugger action timed out after 30.0s",
                        "duration_ms": duration_ms,
                        "timed_out": True,
                        "timeout_ms": 30000,
                    }
                status, payload = _PARENT_CONN.recv()
            except EOFError:
                _stop_worker()
                duration_ms = round((time.monotonic() - start_time) * 1000)
                return {
                    "error_code": "worker_crash",
                    "message": "Debugger worker process crashed",
                    "duration_ms": duration_ms,
                }

            if status == "error":
                raise payload
            return payload
