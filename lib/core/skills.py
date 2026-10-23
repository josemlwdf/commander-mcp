"""Skill library tool: list, read, and save reusable skill documents.

Skills are Markdown files with a small frontmatter block::

    ---
    name: my-skill
    description: One-line summary of when to use the skill.
    invokable: true
    ---

    Body of the skill...

They live in the project's ``skills`` directory (override with the
``HANDY-CODER_SKILLS_DIR`` environment variable). The file name stem is the
skill's identifier.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from ..support import describe

SKILLS_DIR_ENV = "HANDY-CODER_SKILLS_DIR"
BASELINE_SKILLS = frozenset({"grill-me", "handoff", "mcp-audit", "mcp-tools-only", "plan"})
MAX_SKILL_BYTES = 256 * 1024
MAX_DESCRIPTION_CHARS = 300
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _skills_dir() -> Path:
    override = os.environ.get(SKILLS_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[2] / "skills"


def _error(code: str, message: str, **extra) -> dict:
    return {"error_code": code, "message": message, **extra}


def _parse(text: str) -> tuple[dict[str, str], str]:
    """Split a skill document into (frontmatter metadata, body)."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    lines = text.split("\n")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            meta: dict[str, str] = {}
            for line in lines[1:index]:
                key, sep, value = line.partition(":")
                if sep and key.strip():
                    meta[key.strip().lower()] = value.strip()
            return meta, "\n".join(lines[index + 1:]).lstrip("\n")
    return {}, text


def _validate_name(name: str | None) -> dict | None:
    if not name:
        return _error("missing_argument", "name is required for this action")
    if not _NAME_RE.match(name):
        return _error(
            "invalid_name",
            "Skill name must be 1-64 chars of lowercase letters, digits and hyphens, "
            "starting with a letter or digit.",
        )
    return None


def _entry(name: str, text: str) -> dict:
    meta, _ = _parse(text)
    return {
        "name": name,
        "description": meta.get("description", ""),
        "invokable": meta.get("invokable", "true").lower() == "true",
        "tags": [tag.strip() for tag in meta.get("tags", "").split(",") if tag.strip()],
        "version": meta.get("version", "1"),
    }


def _list_skills() -> dict:
    directory = _skills_dir()
    skills: list[dict] = []
    errors: list[dict] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.md")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                errors.append(_error("binary_file", f"Skill file is not valid UTF-8: {path.name}", name=path.stem))
                continue
            except OSError as exc:
                errors.append(_error("io_error", f"{exc.strerror or 'Read failed'}: {path.name}", name=path.stem))
                continue
            skills.append(_entry(path.stem, text))
    return {"skills": skills, "count": len(skills), "errors": errors}


def _get_skill(name: str | None) -> dict:
    problem = _validate_name(name)
    if problem:
        return problem
    path = _skills_dir() / f"{name}.md"
    if not path.is_file():
        return _error("not_found", f"Skill not found: {name}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _error("binary_file", f"Skill file is not valid UTF-8: {name}")
    except OSError as exc:
        return _error("io_error", f"{exc.strerror or 'Read failed'}: {name}")
    _, body = _parse(text)
    return {**_entry(name, text), "content": text, "body": body}


def _save_skill(
    name: str | None,
    description: str | None,
    content: str | None,
    invokable: bool,
    overwrite: bool,
    tags: list[str] | None = None,
    version: str = "1",
) -> dict:
    problem = _validate_name(name)
    if problem:
        return problem
    if not description or not description.strip():
        return _error("missing_argument", "description is required for save")
    description = description.strip()
    if "\n" in description or "\r" in description:
        return _error("invalid_description", "description must be a single line")
    if len(description) > MAX_DESCRIPTION_CHARS:
        return _error("invalid_description", f"description exceeds {MAX_DESCRIPTION_CHARS} characters")
    if not content or not content.strip():
        return _error("missing_argument", "content is required for save")
    body = content.replace("\r\n", "\n").strip("\n")
    if body.startswith("---\n") or body.strip() == "---":
        return _error(
            "content_has_frontmatter",
            "content must be the skill body only; frontmatter is generated from name, description and invokable.",
        )

    document = (
        f"---\nname: {name}\ndescription: {description}\n"
        f"invokable: {'true' if invokable else 'false'}\nversion: {version}\ntags: {','.join(tags or [])}\n---\n\n{body}\n"
    )
    encoded = document.encode("utf-8")
    if len(encoded) > MAX_SKILL_BYTES:
        return _error("skill_too_large", f"Skill exceeds {MAX_SKILL_BYTES // 1024} KiB limit")

    directory = _skills_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.md"
        existed = path.exists()
        if existed and not overwrite:
            return _error("already_exists", f"Skill already exists: {name}. Pass overwrite=true to replace it.")
        if overwrite:
            # Atomic replace: write a temp file in the same directory, then swap it in.
            fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                os.replace(tmp_name, path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        else:
            # Exclusive create guards against a concurrent save of the same name.
            with open(path, "xb") as handle:
                handle.write(encoded)
    except FileExistsError:
        return _error("already_exists", f"Skill already exists: {name}. Pass overwrite=true to replace it.")
    except OSError as exc:
        return _error("io_error", f"{exc.strerror or 'Write failed'}: {name}")
    return {"saved": True, "name": name, "created": not existed, "bytes": len(encoded)}


def skills_manage(
    action: Annotated[
        Literal["list", "get", "save", "search", "delete", "export"],
        Field(description="list: return skills. search: filter by query. get/export: retrieve full skill Markdown. save: create/update with optional tags and version. delete: remove a skill."),
    ],
    name: Annotated[
        str | None,
        Field(description="Skill identifier: lowercase letters, digits and hyphens, max 64 chars. Required for get and save."),
    ] = None,
    description: Annotated[
        str | None,
        Field(description="Single-line summary of what the skill does and when to use it. Required for save."),
    ] = None,
    content: Annotated[
        str | None,
        Field(description="Skill body in Markdown, without frontmatter (it is generated). Required for save."),
    ] = None,
    invokable: Annotated[
        bool,
        Field(description="For save: whether the skill can be invoked directly by the user. Defaults to true."),
    ] = True,
    overwrite: Annotated[
        bool,
        Field(description="For save: replace an existing skill with the same name. Defaults to false."),
    ] = False,
    query: Annotated[str | None, Field(description="Search text used by the search action.")] = None,
    tags: Annotated[list[str] | None, Field(description='Optional array of tag strings stored in skill metadata when saving, for example ["python", "testing"].')] = None,
    version: Annotated[str, Field(description="Skill metadata version, defaults to 1.")] = "1",
    confirm: Annotated[bool, Field(description="For delete: must be true to remove the skill; a backup is kept beside it.")] = False,
) -> dict:
    if action == "list":
        return _list_skills()
    if action == "get":
        return _get_skill(name)
    if action == "export":
        return _get_skill(name)
    if action == "search":
        needle = (query or "").lower()
        listed = _list_skills()
        listed["skills"] = [s for s in listed["skills"] if needle in s["name"].lower() or needle in s["description"].lower() or any(needle in t.lower() for t in s["tags"])]
        listed["count"] = len(listed["skills"])
        return listed
    if action == "delete":
        problem = _validate_name(name)
        if problem: return problem
        if name in BASELINE_SKILLS:
            return _error("protected_skill", f"Baseline skill cannot be deleted: {name}")
        if not confirm:
            return _error("confirmation_required", f"Call again with confirm=true to delete skill: {name}")
        try:
            directory = _skills_dir()
            path = directory / f"{name}.md"
            if not path.is_file():
                return _error("not_found", f"Skill not found: {name}")
            if path.is_symlink() or path.resolve().parent != directory.resolve():
                return _error("path_escape", f"Skill path escapes the skills directory: {name}")
            backup = path.with_suffix(path.suffix + ".bak")
            if backup.exists():
                return _error("backup_exists", f"Refusing to overwrite existing backup: {backup.name}")
            if backup.is_symlink() or backup.resolve().parent != directory.resolve():
                return _error("path_escape", f"Skill backup path escapes the skills directory: {name}")
            import shutil
            shutil.copy2(path, backup)
            path.unlink()
            return {"deleted": True, "name": name}
        except FileNotFoundError:
            return _error("not_found", f"Skill not found: {name}")
        except OSError as exc:
            return _error("io_error", f"{exc.strerror or 'Delete failed'}: {name}")
    if action == "save":
        # Metadata values are plain single-line strings in the simple frontmatter format.
        if not version.strip() or any(c in version for c in "\r\n:"):
            return _error("invalid_version", "version must be a non-empty single-line value without ':'")
        if tags and any(not tag.strip() or any(c in tag for c in ",\r\n") for tag in tags):
            return _error("invalid_tags", "tags must be non-empty single-line values without commas")
        return _save_skill(name, description, content, invokable, overwrite, tags, version)
    return _error("invalid_action", f"Unknown action: {action}")


def register(mcp):
    return mcp.tool(description=describe(
        "Manage reusable skills stored as Markdown files in the skills directory. "
        "Actions: list returns name, description, invokable, tags and version; search filters those fields; get/export returns full Markdown; delete requires confirm=true, protects baseline skills, and keeps a .bak backup; "
        'save creates a skill from name, description and content (optional tags as an array of strings, for example ["python", "testing"], and version; fails with already_exists '
        "unless overwrite=true). Failures return error_code (missing_argument, invalid_name, "
        "invalid_description, content_has_frontmatter, skill_too_large, not_found, already_exists, "
        "io_error, confirmation_required, protected_skill, backup_exists, path_escape). "
        "When to use: Discover, load, or record skills that guide how to perform a task."
    ))(skills_manage)
