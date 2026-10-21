"""Bounded public-web fetch and optional configured search API."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from html.parser import HTMLParser
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import Field
from ..support import describe


MAX_RESPONSE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 12


def _public_http_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP(S) URLs without embedded credentials are supported")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"Could not resolve URL host: {parsed.hostname}") from exc
    if not addresses:
        raise ValueError(f"URL host has no address: {parsed.hostname}")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Requests to local, private, or reserved network addresses are not allowed")
    return url


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_http_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []
        self.title = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style", "noscript", "svg"):
            self.hidden += 1
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() in ("p", "br", "div", "li", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style", "noscript", "svg") and self.hidden:
            self.hidden -= 1
        if tag.lower() == "title":
            self.in_title = False
        if tag.lower() in ("p", "div", "li", "h1", "h2", "h3", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if not self.hidden:
            self.parts.append(data)


def _request(url: str) -> tuple[bytes, str, str, int, bool]:
    """Returns (body, final_url, content_type, http_status, truncated)."""
    _public_http_url(url)
    request = Request(url, headers={"User-Agent": "HandyCoderMCP/2.0", "Accept": "text/html,application/json,text/plain"})
    opener = build_opener(_SafeRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type()
            http_status = response.status
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            truncated = len(raw) > MAX_RESPONSE_BYTES
            body = raw[:MAX_RESPONSE_BYTES]
            return body, response.geturl(), content_type, http_status, truncated
    except HTTPError as exc:
        raise RuntimeError(f"Web request failed with HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Web request failed: {exc.reason}") from exc


def _fetch(url: str) -> dict:
    body, final_url, content_type, http_status, truncated = _request(url)
    decoded = body.decode("utf-8", "replace")
    if content_type in ("text/html", "application/xhtml+xml"):
        parser = _TextExtractor()
        parser.feed(decoded)
        decoded = " ".join(" ".join(parser.parts).split())
        title = " ".join(" ".join(parser.title).split())
    else:
        if not content_type.startswith("text/") and content_type not in ("application/json", "application/xml"):
            raise ValueError(f"Unsupported non-text web response: {content_type}")
        title = ""
    content = decoded[:20000]
    return {
        "supported": True,
        "url": final_url,
        "http_status": http_status,
        "title": title,
        "content_type": content_type,
        "content": content,
        "truncated": truncated or len(decoded) > 20000,
    }


def _search(query: str) -> dict:
    endpoint = os.environ.get("HANDY_CODER_SEARCH_URL", "").strip()
    if not endpoint:
        return {"supported": False, "message": "Set HANDY_CODER_SEARCH_URL to a trusted JSON search endpoint to enable web search; direct URL fetching remains available."}
    parsed = urlparse(endpoint)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    params.append(("q", query))
    search_url = urlunparse(parsed._replace(query=urlencode(params)))
    body, final_url, content_type, http_status, truncated = _request(search_url)
    if content_type != "application/json":
        raise ValueError("Configured search endpoint must return application/json")
    payload = json.loads(body.decode("utf-8"))
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise ValueError("Configured search endpoint must return a JSON object with a results array")
    clean = []
    for item in results[:10]:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            clean.append({
                "title": str(item.get("title", ""))[:500],
                "url": item["url"][:2048],
                "snippet": str(item.get("snippet", ""))[:1200],
            })
    return {"supported": True, "search_url": final_url, "http_status": http_status, "results": clean}


def register(mcp):
    @mcp.tool(description=describe(
        "Fetch public HTTP(S) text or search via HANDY_CODER_SEARCH_URL; private targets are blocked. "
        "Response includes http_status, final_url, and truncated flag. "
        "When to use: Read external documentation or find web pages."
    ))
    def web_fetch_docs(
        url: Annotated[str | None, Field(description="Public HTTP(S) URL to fetch as text. Provide exactly one of url or search_query; private, local, and reserved network targets are blocked.")] = None,
        search_query: Annotated[str | None, Field(description="Non-empty web search query sent to the trusted endpoint configured by HANDY_CODER_SEARCH_URL. Provide exactly one of url or search_query.")] = None,
    ) -> dict:
        if bool(url) == bool(search_query):
            raise ValueError("Provide exactly one of url or search_query")
        if url:
            return _fetch(url)
        if not search_query or not search_query.strip():
            raise ValueError("search_query cannot be empty")
        return _search(search_query.strip())
