from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

# Reads the user's Continue config to find a model/key for subagent loops.
# Stdlib only at import time; PyYAML is imported lazily and is optional.
# Nothing in this module logs or prints. Keys and header values live only in
# ContinueModel fields declared repr=False.

MAX_CONFIG_BYTES = 1024 * 1024
MAX_ENV_BYTES = 64 * 1024
LOCAL_PROVIDERS = frozenset({"ollama", "lmstudio", "llamafile", "llama.cpp"})
_SECRET_REF = re.compile(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}")
_FULL_SECRET_REF = re.compile(r"^\s*\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}\s*$")


@dataclass(frozen=True)
class ContinueModel:
    name: str
    provider: str
    model: str
    api_base: str | None
    api_key: str | None = field(repr=False, compare=False, default=None)
    roles: tuple[str, ...] = ("chat",)
    headers: dict[str, str] = field(repr=False, compare=False, default_factory=dict)
    max_tokens: int | None = None

    def safe_summary(self) -> dict[str, str]:
        """Only non-secret identifiers; never the key or header values."""
        return {"provider": self.provider, "model": self.model, "name": self.name}


@dataclass(frozen=True)
class ResolvedContinue:
    model: ContinueModel
    config_path: str


# ---------------------------------------------------------------- discovery

def _current_user(env: Mapping[str, str]) -> str:
    return (env.get("USERNAME") or env.get("USER") or "").strip()


def _safe_user_component(user: str) -> bool:
    return bool(user) and user not in {".", ".."} and "/" not in user and "\\" not in user


def _candidates(
    workspace_root: str | Path | None,
    env: Mapping[str, str],
    home: Path | None,
) -> list[tuple[Path, bool]]:
    """Return (path, trusted) pairs in priority order.

    A workspace-local config is untrusted: a repository can ship one, so it
    must not be able to make us send environment secrets to its apiBase.
    """
    out: list[tuple[Path, bool]] = []

    override = (env.get("SUBAGENT_CONTINUE_CONFIG") or "").strip()
    if override:
        out.append((Path(override).expanduser(), True))

    global_dir = (env.get("CONTINUE_GLOBAL_DIR") or "").strip()
    if global_dir:
        out.append((Path(global_dir).expanduser() / "config.yaml", True))

    if home is None:
        try:
            home = Path.home()
        except (RuntimeError, KeyError):
            home = None
    if home is not None:
        out.append((home / ".continue" / "config.yaml", True))
        out.append((home / ".continue" / "config.json", True))

    user = _current_user(env)
    if _safe_user_component(user):
        wsl_dir = Path("/mnt/c/Users") / user / ".continue"
        if wsl_dir.is_dir():
            out.append((wsl_dir / "config.yaml", True))
            out.append((wsl_dir / "config.json", True))

    profile = (env.get("USERPROFILE") or "").strip()
    if profile:
        win_dir = Path(profile) / ".continue"
        out.append((win_dir / "config.yaml", True))
        out.append((win_dir / "config.json", True))

    if workspace_root:
        out.append((Path(workspace_root) / ".continue" / "config.yaml", False))

    seen: set[str] = set()
    unique: list[tuple[Path, bool]] = []
    for path, trusted in out:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append((path, trusted))
    return unique


def candidate_config_paths(
    workspace_root: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> list[Path]:
    environ = os.environ if env is None else env
    return [path for path, _ in _candidates(workspace_root, environ, home)]


# ------------------------------------------------------------------ parsing

def load_config_file(path: str | Path) -> dict | None:
    try:
        p = Path(path)
        st = p.stat()
        if not p.is_file() or st.st_size > MAX_CONFIG_BYTES:
            return None
        text = p.read_bytes().decode("utf-8")
        if p.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            import yaml  # optional dependency; ImportError -> candidate skipped

            data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - any failure means skip this candidate
        return None
    return data if isinstance(data, dict) else None


def _read_dotenv(path: Path) -> dict[str, str]:
    # NOTE: whether Continue itself reads a .env next to the config is UNVERIFIED.
    try:
        if not path.is_file() or path.stat().st_size > MAX_ENV_BYTES:
            return {}
        text = path.read_bytes().decode("utf-8")
    except Exception:  # noqa: BLE001
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if name:
            values[name] = value
    return values


def _lookup_secret(
    name: str, config_dir: Path, environ: Mapping[str, str], allow_refs: bool
) -> str | None:
    if not allow_refs:
        return None
    value = (environ.get(name) or "").strip()
    if value:
        return value
    value = _read_dotenv(config_dir / ".env").get(name, "").strip()
    return value or None


def _expand(
    value: str, config_dir: Path, environ: Mapping[str, str], allow_refs: bool
) -> str | None:
    """Return value with a whole-string secret reference resolved, or None.

    A string that still contains an unresolved reference is never returned.
    """
    if not _SECRET_REF.search(value):
        return value
    match = _FULL_SECRET_REF.match(value)
    if not match:
        return None
    return _lookup_secret(match.group(1), config_dir, environ, allow_refs)


def _string_headers(entry: dict) -> dict[str, str]:
    options = entry.get("requestOptions")
    headers = options.get("headers") if isinstance(options, dict) else None
    if not isinstance(headers, dict):
        return {}
    return {
        str(k): v for k, v in headers.items() if isinstance(v, str) and v.strip()
    }


def _resolve_api_key(
    entry: dict,
    config_dir: Path,
    environ: Mapping[str, str],
    allow_secret_refs: bool = True,
) -> str | None:
    raw_key = entry.get("apiKey")
    raw_key = raw_key.strip() if isinstance(raw_key, str) else ""

    # 1) literal apiKey
    if raw_key and not _SECRET_REF.search(raw_key):
        return raw_key

    # 2) Authorization header
    for name, value in _string_headers(entry).items():
        if name.lower() != "authorization":
            continue
        token = value.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        expanded = _expand(token, config_dir, environ, allow_secret_refs)
        if expanded:
            return expanded

    # 3) ${{ secrets.X }} in apiKey
    if raw_key:
        return _expand(raw_key, config_dir, environ, allow_secret_refs)
    return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def list_usable_models(
    config: dict,
    config_dir: Path,
    environ: Mapping[str, str],
    allow_secret_refs: bool = True,
) -> list[ContinueModel]:
    models = config.get("models")
    if not isinstance(models, list):
        return []
    usable: list[ContinueModel] = []
    for entry in models:
        if not isinstance(entry, dict) or "uses" in entry:
            continue
        provider = entry.get("provider")
        model = entry.get("model")
        if not isinstance(provider, str) or not provider.strip():
            continue
        if not isinstance(model, str) or not model.strip():
            continue
        provider = provider.strip().lower()
        model = model.strip()

        raw_roles = entry.get("roles")
        if isinstance(raw_roles, list):
            roles = tuple(r for r in raw_roles if isinstance(r, str))
            if "chat" not in roles:
                continue
        else:
            roles = ("chat",)

        key = _resolve_api_key(entry, config_dir, environ, allow_secret_refs)
        if key is None and provider not in LOCAL_PROVIDERS:
            continue

        headers: dict[str, str] = {}
        for h_name, h_value in _string_headers(entry).items():
            expanded = _expand(h_value, config_dir, environ, allow_secret_refs)
            if expanded:
                headers[h_name] = expanded

        name = entry.get("name")
        api_base = entry.get("apiBase")
        options = entry.get("defaultCompletionOptions")
        usable.append(
            ContinueModel(
                name=name.strip() if isinstance(name, str) and name.strip() else model,
                provider=provider,
                model=model,
                api_base=api_base.strip() if isinstance(api_base, str) and api_base.strip() else None,
                api_key=key,
                roles=roles,
                headers=headers,
                max_tokens=_positive_int(options.get("maxTokens")) if isinstance(options, dict) else None,
            )
        )
    return usable


def select_model(
    models: list[ContinueModel], preferred: str | None
) -> ContinueModel | None:
    wanted = (preferred or "").strip().lower()
    if wanted:
        for item in models:
            if item.name.lower() == wanted or item.model.lower() == wanted:
                return item
        return None
    return models[0] if models else None


# ---------------------------------------------------------------- resolution

_CACHE_LOCK = threading.Lock()
_PARSE_CACHE: dict[tuple[str, int, int], dict | None] = {}


def clear_cache() -> None:
    with _CACHE_LOCK:
        _PARSE_CACHE.clear()


def _load_cached(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _CACHE_LOCK:
        if key in _PARSE_CACHE:
            return _PARSE_CACHE[key]
    config = load_config_file(path)
    with _CACHE_LOCK:
        # Drop stale entries for the same path so edits re-parse.
        for old in [k for k in _PARSE_CACHE if k[0] == key[0]]:
            del _PARSE_CACHE[old]
        _PARSE_CACHE[key] = config
    return config


def resolve_continue_model(
    workspace_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    accept: Callable[[ContinueModel], bool] | None = None,
) -> ResolvedContinue | None:
    env = os.environ if environ is None else environ
    preferred = env.get("SUBAGENT_CONTINUE_MODEL")
    for path, trusted in _candidates(workspace_root, env, home):
        config = _load_cached(path)
        if config is None:
            continue
        models = list_usable_models(config, path.parent, env, allow_secret_refs=trusted)
        if accept is not None:
            models = [m for m in models if accept(m)]
        chosen = select_model(models, preferred)
        if chosen is not None:
            return ResolvedContinue(model=chosen, config_path=str(path))
    return None