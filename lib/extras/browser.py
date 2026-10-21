"""Optional Playwright-based browser automation."""
from __future__ import annotations

import base64
import ipaddress
import socket
import threading
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field
from ..support import describe


_LOCK = threading.Lock()
_PLAYWRIGHT = None
_BROWSER = None
_PAGE = None


def _safe_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Browser navigation requires an HTTP(S) URL")
    if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
        return
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"Could not resolve browser URL host: {parsed.hostname}") from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Browser navigation to private or reserved network addresses is not allowed")


def _ensure_page():
    global _PLAYWRIGHT, _BROWSER, _PAGE
    if _PAGE is not None:
        return _PAGE
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    try:
        _PLAYWRIGHT = sync_playwright().start()
        _BROWSER = _PLAYWRIGHT.chromium.launch(headless=True)
        context = _BROWSER.new_context()

        def guard(route):
            try:
                _safe_url(route.request.url)
            except ValueError:
                route.abort()
            else:
                route.continue_()

        context.route("**/*", guard)
        _PAGE = context.new_page()
        _PAGE.set_default_timeout(15000)
        return _PAGE
    except Exception:
        _close()
        raise


def _close():
    global _PLAYWRIGHT, _BROWSER, _PAGE
    try:
        if _BROWSER is not None:
            _BROWSER.close()
    finally:
        try:
            if _PLAYWRIGHT is not None:
                _PLAYWRIGHT.stop()
        finally:
            _PLAYWRIGHT = _BROWSER = _PAGE = None


def register(mcp):
    @mcp.tool(description=describe(
        "Use optional Playwright for navigation, clicks, screenshots, or page HTML; private networks are blocked except localhost. "
        "When to use: Inspect or interact with a web page."
    ))
    def browser_action(
        action: Annotated[
            Literal["navigate", "click", "screenshot", "get_html", "close"],
            Field(description="Browser operation: navigate to a URL, click a selector, capture a screenshot, return page HTML, or close the browser session."),
        ],
        url: Annotated[
            str | None,
            Field(description="Public HTTP(S) URL required for navigate. Localhost is allowed; private and reserved network addresses are blocked."),
        ] = None,
        selector: Annotated[
            str | None,
            Field(description="CSS selector required for click; the first matching element is clicked."),
        ] = None,
    ) -> dict:
        global _PAGE
        with _LOCK:
            if action == "close":
                _close()
                return {"supported": True, "action": "close"}
            if action == "navigate":
                if not url:
                    raise ValueError("url is required for navigate")
                _safe_url(url)
            if action == "click" and not selector:
                raise ValueError("selector is required for click")
            page = _ensure_page()
            if page is None:
                return {"supported": False, "message": "Install the optional Playwright package and Chromium browser to enable browser automation."}
            if action == "navigate":
                response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                return {
                    "supported": True,
                    "url": page.url,
                    "status": response.status if response else None,
                    "title": page.title(),
                }
            if action == "click":
                page.locator(selector).first.click()
                return {"supported": True, "url": page.url, "title": page.title()}
            if action == "screenshot":
                image = page.screenshot(type="png", full_page=False)
                if len(image) > 2 * 1024 * 1024:
                    raise ValueError("Screenshot exceeds the 2 MiB response limit")
                return {"supported": True, "url": page.url, "content_type": "image/png", "base64": base64.b64encode(image).decode("ascii")}
            return {"supported": True, "url": page.url, "html": page.content()[:50000]}
