from __future__ import annotations

import json
import os
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..core import apply_edits, code_search, create_file, diagnostics, find_files, read_files, run_tests
from .roles import Role

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
            raise ValueError("model response was not valid JSON action")
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


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float = 35.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
        raise RuntimeError(f"HTTP {exc.code} from backend: {details[:300]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Backend connection failed: {exc}") from exc

    try:
        return json.loads(text)
    except ValueError as exc:
        raise RuntimeError("Backend returned non-JSON response") from exc


def _complete_openai_compatible(base_url: str, model: str, api_key: str | None, system_prompt: str, messages: list[dict[str, str]], max_tokens: int) -> str:
    prompt_messages = [{"role": "system", "content": system_prompt}] + messages
    payload = {
        "model": model,
        "messages": prompt_messages,
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    base = base_url.rstrip("/")
    data = _post_json(f"{base}/chat/completions", payload, headers)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenAI-compatible backend returned no choices")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    return _strip_text(message.get("content"))


def _complete_anthropic(api_key: str, system_prompt: str, messages: list[dict[str, str]], max_tokens: int) -> str:
    payload = {
        "model": os.environ.get("SUBAGENT_MODEL_ANTHROPIC", "claude-3-5-haiku-latest"),
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    data = _post_json("https://api.anthropic.com/v1/messages", payload, headers)
    return _strip_text(data.get("content"))


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
) -> str:
    system_prompt = "\n".join(
        [
            f"You are the '{role.name}' subagent.",
            role.system_prompt,
            f"Allowed tools: {', '.join(role.allowed_tools)}",
            "Return only JSON with one of these shapes:",
            '{"tool": "<tool_name>", "args": { ... }}',
            '{"final": "<final result text>"}',
            f"Target result format: {role.result_format}",
            "Never call tools outside the allowlist.",
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

    raise RuntimeError(f"Unsupported subagent backend: {backend}")


def run_loop(
    backend: str,
    role: Role,
    task: str,
    context: str | None,
    deliverable: str | None,
    *,
    sampling_fn: SamplingFn | None = None,
) -> tuple[str, int, int]:
    history: list[dict[str, str]] = []
    tool_calls = 0
    token_estimate = 0

    for turn in range(1, role.max_turns + 1):
        assistant_text = complete_once(
            backend,
            role,
            task,
            context,
            deliverable,
            history,
            sampling_fn=sampling_fn,
        )
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
        tool_fn = _ALLOWED_TOOLS.get(tool_name)
        if tool_fn is None:
            raise RuntimeError(f"tool '{tool_name}' is not available in loop backends")

        try:
            tool_result = tool_fn(**action["args"])
        except Exception as exc:  # noqa: BLE001
            tool_result = {"error": str(exc)}

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
