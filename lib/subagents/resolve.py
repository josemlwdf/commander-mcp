from __future__ import annotations

import json
import os
from urllib.error import URLError
from urllib.request import Request, urlopen


EXISTING_LLM_ENV = (
    ("ANTHROPIC_API_KEY", "anthropic"),
    ("OPENAI_API_KEY", "openai"),
)


def ollama_up(timeout: float = 0.4) -> bool:
    req = Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:  # nosec - localhost only
            if resp.status != 200:
                return False
            body = resp.read().decode("utf-8", "replace")
            json.loads(body)
            return True
    except (URLError, TimeoutError, ValueError):
        return False


_CONTINUE_OFF_VALUES = frozenset({'0', 'false', 'no', 'off'})


def _continue_enabled() -> bool:
    # Default-on: auto mode consults the user's Continue config unless
    # SUBAGENT_CONTINUE is explicitly set to 0/false/no/off.
    return os.environ.get('SUBAGENT_CONTINUE', '').strip().lower() not in _CONTINUE_OFF_VALUES


def resolve_continue():
    # Return the usable Continue model (a ResolvedContinue) or None. Never raises.
    # Models whose provider the loop cannot call are skipped. The result holds the
    # API key, so callers must not log or return it.
    try:
        from .. import support
        from . import continue_config
        from .loop import continue_provider_supported

        return continue_config.resolve_continue_model(
            getattr(support, 'ROOT', None),
            accept=lambda m: continue_provider_supported(m.provider),
        )
    except Exception:  # noqa: BLE001 - any failure means no continue backend
        return None


def _continue_backend() -> str:
    resolved = resolve_continue()
    return f'continue:{resolved.model.provider}' if resolved else 'cooperative'


def resolve_backend(client_capabilities: dict | None = None) -> str:
    """Resolve the active subagent backend for this process/session.

    Cooperative mode remains the always-available floor.
    When available, sampling/direct/local backends are selected for loop execution.
    """
    mode = (os.environ.get("SUBAGENT_BACKEND") or "auto").strip().lower()
    caps = client_capabilities or {}
    sampling_declared = bool(caps.get("sampling"))

    if mode in {"sampling"}:
        return "sampling" if sampling_declared else "cooperative"

    if mode in {"direct"}:
        for env_name, provider in EXISTING_LLM_ENV:
            if os.environ.get(env_name, "").strip():
                return f"direct:{provider}"
        return "cooperative"

    if mode in {"local"}:
        return "local:ollama" if os.environ.get("SUBAGENT_LOCAL") == "1" and ollama_up() else "cooperative"

    if mode in {"continue"}:
        # Choosing this mode explicitly is the opt-in; SUBAGENT_CONTINUE=1 is not needed.
        return _continue_backend()

    if mode in {"cooperative"}:
        return "cooperative"

    # auto
    if sampling_declared:
        return "sampling"
    for env_name, provider in EXISTING_LLM_ENV:
        if os.environ.get(env_name, "").strip():
            return f"direct:{provider}"
    if _continue_enabled():
        found = _continue_backend()
        if found != 'cooperative':
            return found
    if os.environ.get("SUBAGENT_LOCAL") == "1" and ollama_up():
        return "local:ollama"
    return "cooperative"
