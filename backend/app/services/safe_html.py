"""Small server-side allow-list for alumni post and event rich text.

The browser sanitises again before rendering. This boundary is deliberately
stricter than the email composer: no images, styles, embedded content, data URLs,
or relative links are stored.
"""
from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

_ALLOWED = frozenset({
    "p", "br", "strong", "b", "em", "i", "u", "s", "blockquote", "pre",
    "code", "h2", "h3", "ul", "ol", "li", "a",
})
_VOID = frozenset({"br"})
_DROP_CONTENT = frozenset({"script", "style", "iframe", "object", "embed", "svg", "math", "template"})


def _safe_href(value: str) -> str | None:
    """An absolute http(s) or mailto link, else None. Whitespace and control
    characters inside a link are refused: browsers drop them, which is how
    ``java&#9;script:`` slips past a naive scheme check."""
    value = value.strip()
    if not value or len(value) > 2000 or any(ord(c) < 0x21 or ord(c) == 0x7F for c in value):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme in {"http", "https"}:
        return value if parts.netloc else None
    if scheme == "mailto":
        return value if parts.path else None
    return None


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.stack: list[str] = []
        self.drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.drop_depth:
            if tag in _DROP_CONTENT:
                self.drop_depth += 1
            return
        if tag in _DROP_CONTENT:
            self.drop_depth = 1
            return
        if tag not in _ALLOWED:
            return
        if tag == "a":
            values = {name.lower(): value or "" for name, value in attrs}
            href = _safe_href(values.get("href", ""))
            title = values.get("title", "").strip()[:200]
            safe_attrs = ""
            if href:
                safe_attrs += f' href="{escape(href, quote=True)}" rel="noopener noreferrer"'
            if title:
                safe_attrs += f' title="{escape(title, quote=True)}"'
            self.output.append(f"<a{safe_attrs}>")
        else:
            self.output.append(f"<{tag}>")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.drop_depth:
            if tag in _DROP_CONTENT:
                self.drop_depth -= 1
            return
        if tag not in self.stack:
            return
        while self.stack:
            current = self.stack.pop()
            self.output.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.drop_depth:
            self.output.append(escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.output.append(f"</{self.stack.pop()}>")
        return "".join(self.output).strip()


def sanitize_rich_text(value: str) -> str:
    """Return safe, balanced HTML from untrusted rich text."""
    parser = _Sanitizer()
    parser.feed(value or "")
    parser.close()
    return parser.result()
