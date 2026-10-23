"""HTML formatting helpers used by workspace write tools."""
from __future__ import annotations


def prettify_html(content: str) -> tuple[str, dict]:
    """Return BeautifulSoup formatted HTML and a non-throwing status object."""
    try:
        from bs4 import BeautifulSoup

        formatted = BeautifulSoup(content, "html.parser").prettify()
    except Exception as exc:
        return content, {
            "ok": False,
            "changed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return formatted, {"ok": True, "changed": formatted != content}
