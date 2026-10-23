from __future__ import annotations

import json
import os
import re
import threading
from typing import TYPE_CHECKING, Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..core import apply_edits, code_search, create_file, diagnostics, find_files, read_files, run_tests
from ..core import commit_nudge, git_manage
from .roles import Role

if TYPE_CHECKING:
    from .continue_config import ContinueModel

ToolFn = Callable[..., dict]
SamplingFn = Callable[[str, list[dict[str, str]], int], str]

_ALLOWED_TOOLS: dict[str, ToolFn] = {
    "read_files": read_files.read_files,
    "search_text": code_search.search_text,
    "find_files": find_files.find_files,
    "create_file": create_file.create_file,
    "apply_edits": apply_edits.apply_edits,
    "code_get_diagnostics": diagnostics.code_get_diagnostics,
    "run_tests": run_tests.run_tests,
    "defer_commit": commit_nudge.defer_commit,
    "git_manage": git_manage.git_manage,
    # Resolve these lazily: tools imports loop during module initialization.
    "subagent_status": lambda **args: _call_subagent_tool("subagent_status", args),
    "subagent_result": lambda **args: _call_subagent_tool("subagent_result", args),
}


def _call_subagent_tool(name: str, args: dict[str, Any]) -> dict:
    from . import tools

    return getattr(tools, name)(**args)


_TOOL_ARGUMENTS = {
    "read_files": '{"paths": ["workspace-relative path"], "line_ranges"?: [{"path": "...", "start_line": 1, "end_line": 20}], "start_line"?: 1, "end_line"?: 20, "encoding"?: "utf-8", "include_metadata"?: false}',
    "search_text": '{"query": "text or regex", "is_regex"?: true, "path_glob"?: "...", "max_results"?: 100, "context_lines"?: 0, "include_generated"?: false, "case_insensitive"?: false, "extensions"?: [".py"]}',
    "find_files": '{"glob_pattern": "**/*.py", "exclude_pattern"?: "...", "include_generated"?: false, "file_type"?: ".py", "min_size"?: 0, "max_size"?: 10000, "sort_by"?: "path|size|modified", "include_metadata"?: false}',
    "create_file": '{"path": "workspace-relative path", "content"?: "full file content", "overwrite"?: false, "create_parents"?: true, "permissions"?: 420, "backup"?: false, "format_content"?: false, "post_process_python"?: false}',
    "apply_edits": '{"edits": [{"path": "...", "pattern": "Python regex", "replacement": "text", "position"?: {"line": 1, "col": 1}}], "replace_all"?: false, "max_replacements"?: 1, "case_insensitive"?: false, "dry_run"?: false, "expand_groups"?: false}',
    "code_get_diagnostics": '{"path"?: "...", "check"?: "ruff", "timeout_ms"?: 30000}',
    "run_tests": '{"test_framework"?: "jest|pytest|vitest", "filter_path"?: "test/test_file.py", "fail_fast"?: false, "parallel"?: 1, "coverage"?: false, "verbose"?: false}',
    "defer_commit": '{"action"?: "defer|cancel|status", "reason"?: "why commit must wait", "calls"?: 5}',
    "git_manage": '{"action": "status|diff|log|add|commit|push|pull|merge|rebase|checkout|branch|stash|blame|init", "args"?: ["..."], "repo_path"?: "...", "mode"?: "unstaged|staged|both", "file_paths"?: ["..."]}',
    "subagent_status": '{"task_id": "t_1"}',
    "subagent_result": '{"task_id": "t_1", "offset"?: 0}',
}

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _strip_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    out.append(item["text"])
                elif isinstance(item.get("content"), str):
                    out.append(item["content"])
            elif isinstance(item, str):
                out.append(item)
        return "\n".join(part for part in out if part)
    if value is None:
        return ""
    return str(value)


def _parse_json_action(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("model returned an empty response")
    try:
        data = json.loads(raw)
    except ValueError:
        match = _JSON_BLOCK.search(raw)
        if not match:
            # Some otherwise capable models ignore the JSON instruction when
            # they have no action to take. Treat ordinary prose as the final.
            return {"kind": "final", "text": raw}
        data = json.loads(match.group(1))

    if not isinstance(data, dict):
        raise ValueError("model response must be a JSON object")
    if isinstance(data.get("final"), str):
        return {"kind": "final", "text": data["final"].strip()}

    tool = data.get("tool")
    args = data.get("args", {})
    if isinstance(tool, str) and isinstance(args, dict):
        return {"kind": "tool", "tool": tool.strip(), "args": args}

    raise ValueError("model JSON must include either string key 'final' or 'tool' + object 'args'")


def _json_preview(payload: Any, limit: int = 5000) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except ValueError:
        text = str(payload)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _redact(text: Any, secrets: Any = ()) -> str:
    out = str(text)
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4:
            out = out.replace(secret, "[redacted]")
    return out


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float = 35.0, secrets: Any = ()) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    hidden = [*secrets, *headers.values()]
    req = Request(url, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
        raise RuntimeError(_redact(f"HTTP {exc.code} from backend: {details[:300]}", hidden)) from None
    except URLError as exc:
        raise RuntimeError(_redact(f"Backend connection failed: {exc}", hidden)) from None

    try:
        return json.loads(text)
    except ValueError:
        raise RuntimeError("Backend returned non-JSON response") from None


CONTINUE_OPENAI_COMPATIBLE_BASES: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "lmstudio": "http://127.0.0.1:1234/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}


def continue_provider_supported(provider: str) -> bool:
    return provider in CONTINUE_OPENAI_COMPATIBLE_BASES or provider == "anthropic"


def _has_header(headers: dict[str, str], name: str) -> bool:
    return any(key.lower() == name for key in headers)


def _complete_openai_compatible(base_url: str, model: str, api_key: str | None, system_prompt: str, messages: list[dict[str, str]], max_tokens: int, *, headers: dict[str, str] | None = None, timeout: float | None = None) -> str:
    prompt_messages = [{"role": "system", "content": system_prompt}] + messages
    payload = {
        "model": model,
        "messages": prompt_messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    merged = dict(headers or {})
    if api_key and not _has_header(merged, "authorization"):
        merged["Authorization"] = f"Bearer {api_key}"
    base = base_url.rstrip("/")
    data = _post_json(
        f"{base}/chat/completions",
        payload,
        merged,
        timeout if timeout is not None else 35.0,
        [api_key] if api_key else [],
    )
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenAI-compatible backend returned no choices")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    return _strip_text(message.get("content"))


def _anthropic_url(base_url: str | None) -> str:
    if not base_url:
        return "https://api.anthropic.com/v1/messages"
    base = base_url.rstrip("/")
    return base + ("/messages" if base.endswith("/v1") else "/v1/messages")


def _complete_anthropic(api_key: str, system_prompt: str, messages: list[dict[str, str]], max_tokens: int, *, model: str | None = None, base_url: str | None = None, headers: dict[str, str] | None = None, timeout: float | None = None) -> str:
    payload = {
        "model": model or os.environ.get("SUBAGENT_MODEL_ANTHROPIC", "claude-3-5-haiku-latest"),
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
    }
    merged = {k: v for k, v in (headers or {}).items() if k.lower() not in {"authorization", "x-api-key"}}
    merged["x-api-key"] = api_key
    merged["anthropic-version"] = "2023-06-01"
    data = _post_json(
        _anthropic_url(base_url),
        payload,
        merged,
        timeout if timeout is not None else 35.0,
        [api_key],
    )
    return _strip_text(data.get("content"))


def _continue_base_url(provider: str, api_base: str | None) -> str:
    if not api_base:
        return CONTINUE_OPENAI_COMPATIBLE_BASES[provider]
    base = api_base.rstrip("/")
    if provider in {"ollama", "lmstudio"} and not base.endswith("/v1"):
        base += "/v1"
    return base


def _default_sampling(system_prompt: str, messages: list[dict[str, str]], max_tokens: int) -> str:
    url = os.environ.get("SUBAGENT_SAMPLING_URL", "").strip()
    if not url:
        raise RuntimeError("sampling backend is unavailable in this runtime")
    model = os.environ.get("SUBAGENT_SAMPLING_MODEL", "sampling-default")
    key = os.environ.get("SUBAGENT_SAMPLING_API_KEY", "").strip() or None
    return _complete_openai_compatible(url, model, key, system_prompt, messages, max_tokens)


def complete_once(
    backend: str,
    role: Role,
    task: str,
    context: str | None,
    deliverable: str | None,
    history: list[dict[str, str]],
    *,
    sampling_fn: SamplingFn | None = None,
    continue_model: "ContinueModel | None" = None,
) -> str:
    system_prompt = "\n".join(
        [
            f"You are the '{role.name}' subagent.",
            role.system_prompt,
            f"Allowed tools: {', '.join(role.allowed_tools)}",
            "Tool argument shapes (optional fields may be omitted):\n"
            + "\n".join(f"{name}: {_TOOL_ARGUMENTS[name]}" for name in role.allowed_tools if name in _TOOL_ARGUMENTS),
            "Return only JSON with one of these shapes:",
            '{"tool": "<tool_name>", "args": { ... }}',
            '{"final": "<final result text>"}',
            f"Target result format: {role.result_format}",
            "Never call tools outside the allowlist.",
            "For apply_edits, every edit uses pattern and replacement (not old_str/new_str).",
        ]
    )
    if not history:
        goal = [f"Task: {task}"]
        if context:
            goal.append(f"Context: {context}")
        if deliverable:
            goal.append(f"Deliverable: {deliverable}")
        history.append({"role": "user", "content": "\n".join(goal)})

    max_tokens = max(256, min(role.max_tokens, 4096))

    if backend == "sampling":
        fn = sampling_fn or _default_sampling
        return fn(system_prompt, history, max_tokens)

    if backend == "local:ollama":
        model = os.environ.get("SUBAGENT_MODEL_LOCAL", "llama3.1")
        return _complete_openai_compatible("http://127.0.0.1:11434/v1", model, None, system_prompt, history, max_tokens)

    if backend.startswith("direct:"):
        provider = backend.split(":", 1)[1]
        if provider == "openai":
            key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not key:
                raise RuntimeError("OPENAI_API_KEY is not set")
            model = os.environ.get("SUBAGENT_MODEL_OPENAI", "gpt-4o-mini")
            return _complete_openai_compatible("https://api.openai.com/v1", model, key, system_prompt, history, max_tokens)
        if provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set")
            return _complete_anthropic(key, system_prompt, history, max_tokens)
        raise RuntimeError(f"Unsupported direct provider: {provider}")

    if backend.startswith("continue:"):
        if continue_model is None:
            raise RuntimeError("continue backend requires a resolved model")
        provider = continue_model.provider
        if not continue_provider_supported(provider):
            raise RuntimeError(f"Unsupported continue provider: {provider}")
        limit = max_tokens
        if continue_model.max_tokens:
            limit = min(max_tokens, continue_model.max_tokens)
        if provider == "anthropic":
            return _complete_anthropic(
                continue_model.api_key or "",
                system_prompt,
                history,
                limit,
                model=continue_model.model,
                base_url=continue_model.api_base,
                headers=continue_model.headers,
            )
        return _complete_openai_compatible(
            _continue_base_url(provider, continue_model.api_base),
            continue_model.model,
            continue_model.api_key,
            system_prompt,
            history,
            limit,
            headers=continue_model.headers,
        )

    raise RuntimeError(f"Unsupported subagent backend: {backend}")


class SubagentCancelled(RuntimeError):
    pass


LOOP_WRITE_TOOLS = ("create_file", "apply_edits", "git_manage")

# Tool calls from loop backends run in a server thread with a deadline. They are
# NOT isolated in the worker process (importing worker_supervisor here would be
# circular), so write-capable roles are gated behind SUBAGENT_LOOP_WRITE=1.


def _call_tool(
    fn: ToolFn,
    args: dict[str, Any],
    deadline: float = 60.0,
    *,
    tool_name: str | None = None,
) -> dict:
    name = tool_name or fn.__name__
    gate_block = commit_nudge.before_tool_call(name, args)
    if gate_block is not None:
        return gate_block

    box: dict[str, Any] = {}

    def target() -> None:
        try:
            result = fn(**args)
        except Exception as exc:  # noqa: BLE001
            box["result"] = {"error": str(exc)}
        else:
            box["result"] = commit_nudge.after_tool_call(name, args, result)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(deadline)
    if thread.is_alive():
        return {"error": f"tool timed out after {deadline:.0f}s"}
    return box.get("result", {"error": "tool returned no result"})


def _check_cancel(cancel_event: Any) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise SubagentCancelled("cancelled")


def run_loop(
    backend: str,
    role: Role,
    task: str,
    context: str | None,
    deliverable: str | None,
    *,
    sampling_fn: SamplingFn | None = None,
    continue_model: "ContinueModel | None" = None,
    cancel_event: Any = None,
    allow_write: bool | None = None,
) -> tuple[str, int, int]:
    if allow_write is None:
        allow_write = os.environ.get("SUBAGENT_LOOP_WRITE") == "1"
    if not allow_write and any(t in role.allowed_tools for t in LOOP_WRITE_TOOLS):
        raise RuntimeError(
            f"role '{role.name}' can write and loop write access is not enabled (set SUBAGENT_LOOP_WRITE=1)"
        )

    history: list[dict[str, str]] = []
    tool_calls = 0
    token_estimate = 0

    for turn in range(1, role.max_turns + 1):
        _check_cancel(cancel_event)
        assistant_text = complete_once(
            backend,
            role,
            task,
            context,
            deliverable,
            history,
            sampling_fn=sampling_fn,
            continue_model=continue_model,
        )
        _check_cancel(cancel_event)
        token_estimate += max(1, len(assistant_text) // 4)
        history.append({"role": "assistant", "content": assistant_text})

        action = _parse_json_action(assistant_text)
        if action["kind"] == "final":
            final = action["text"]
            if not final:
                raise RuntimeError("final result must not be empty")
            return final, turn, token_estimate

        tool_calls += 1
        if tool_calls > role.max_tool_calls:
            raise RuntimeError("tool-call budget exceeded")

        tool_name = action["tool"]
        if tool_name not in role.allowed_tools:
            raise RuntimeError(f"tool '{tool_name}' is outside role allowlist")
        _check_cancel(cancel_event)
        tool_fn = _ALLOWED_TOOLS.get(tool_name)
        if tool_fn is None:
            tool_result: Any = {"error": "tool not available in loop backends"}
        else:
            tool_result = _call_tool(tool_fn, action["args"], tool_name=tool_name)

        history.append(
            {
                "role": "user",
                "content": (
                    f"Tool '{tool_name}' result JSON: {_json_preview(tool_result)}\n"
                    "Continue. Return JSON only."
                ),
            }
        )

    raise RuntimeError("max turns exceeded before final result")
