"""Dependency-free structural checker for common HTML syntax mistakes."""
from __future__ import annotations

from html.parser import HTMLParser
from typing import Annotated

from pydantic import Field

from .. import support
from ..support import describe, inside

VOID_ELEMENTS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
OPTIONAL_END = frozenset("html head body li dt dd p rt rp optgroup option colgroup thead tbody tfoot tr td th".split())
_P_CLOSES_ON_START = frozenset("address article aside blockquote details div dl fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr main menu nav ol p pre section table ul".split())


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.findings: list[dict] = []
        self.comment_start: int | None = None

    def add(self, message: str, line: int | None = None):
        self.findings.append({"line": line or self.getpos()[0], "message": message})

    def handle_comment(self, data):
        if "--" in data or data.endswith("-"):
            self.add("Malformed HTML comment")

    def handle_data(self, data):
        line = self.getpos()[0]
        if "<" in data:
            self.add("Malformed opening tag delimiter", line)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        line = self.getpos()[0]
        if tag == "li" and self.stack and self.stack[-1][0] == "li":
            self.stack.pop()
        if tag in {"dt", "dd"} and self.stack and self.stack[-1][0] in {"dt", "dd"}:
            self.stack.pop()
        if tag == "option" and self.stack and self.stack[-1][0] == "option":
            self.stack.pop()
        if tag in {"td", "th"} and self.stack and self.stack[-1][0] in {"td", "th"}:
            self.stack.pop()
        if tag == "tr" and self.stack and self.stack[-1][0] == "tr":
            self.stack.pop()
        if tag in {"thead", "tbody", "tfoot"} and self.stack and self.stack[-1][0] in {"thead", "tbody", "tfoot"}:
            self.stack.pop()
        if tag in _P_CLOSES_ON_START and self.stack and self.stack[-1][0] == "p":
            self.stack.pop()
        if tag not in VOID_ELEMENTS:
            self.stack.append((tag, line))

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        tag = tag.lower()
        line = self.getpos()[0]
        if tag in VOID_ELEMENTS:
            self.add(f"Void element </{tag}> must not have an end tag", line)
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                intervening = self.stack[index + 1:]
                for name, opened in intervening:
                    if name not in OPTIONAL_END:
                        self.add(f"Unclosed <{name}> opened here", opened)
                del self.stack[index:]
                return
        if tag not in OPTIONAL_END:
            self.add(f"Unmatched </{tag}>", line)

    def unknown_decl(self, data):
        self.add(f"Malformed declaration <!{data}>")


def check_html(content: str) -> dict:
    checker = _Checker()
    try:
        checker.feed(content)
        checker.close()
    except Exception as exc:
        checker.add(f"Malformed HTML: {exc}")
    for tag, line in checker.stack:
        if tag not in OPTIONAL_END:
            checker.add(f"Unclosed <{tag}> opened here", line)
    # HTMLParser treats unterminated comments as data; detect comment openers that
    # have no matching closer while preserving the opening line.
    offset = 0
    while True:
        start = content.find("<!--", offset)
        if start < 0:
            break
        end = content.find("-->", start + 4)
        if end < 0:
            checker.add("Unclosed HTML comment", content.count("\n", 0, start) + 1)
            break
        offset = end + 3
    # HTMLParser leaves malformed closing delimiters such as </p or </p x> in
    # data; catch those explicitly and attribute them to their source line.
    import re
    for match in re.finditer(r"</\s*[A-Za-z][^>\n]*(?:\n|$)", content):
        checker.findings.append({"line": content.count("\n", 0, match.start()) + 1,
                                 "message": "Malformed closing tag delimiter"})
    for match in re.finditer(r"<[A-Za-z][^>\n]*(?:\n|$)", content):
        checker.findings.append({"line": content.count("\n", 0, match.start()) + 1,
                                 "message": "Malformed opening tag delimiter"})
    return {"ok": not checker.findings, "findings": checker.findings}


def html_syntax_check(path: Annotated[str, Field(description="Workspace-relative HTML file path to check.")]) -> dict:
    try:
        file_path = inside(path)
        if not file_path.is_file():
            return {"ok": False, "error": "not_a_file", "path": path, "findings": []}
        content = file_path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "path": path, "findings": []}
    return {"path": str(file_path.relative_to(support.ROOT)), **check_html(content)}


def register(mcp):
    return mcp.tool(description=describe(
        "Check a workspace HTML file for malformed tag delimiters and comments, unclosed or unmatched tags, and report line numbers. "
        "Accounts for void elements and common optional end tags. Findings do not modify the file. "
        "When to use: Check HTML syntax independently of create_file."
    ))(html_syntax_check)
