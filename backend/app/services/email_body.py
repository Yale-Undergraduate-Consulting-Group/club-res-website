"""Turn what a member wrote into what a recipient receives.

One module owns the boundary between the editor and the wire, because the two
sides disagreed: the Studio editor is a contentEditable producing HTML, while
the sender attached that same string as the ``text/plain`` alternative and
wrapped the HTML copy in ``white-space: pre-wrap``. A recipient on a
plain-text client was shown raw markup ("<p>Dear Ashley,</p>"), and a
recipient on an HTML client got doubled blank lines between paragraphs.

Everything a YUCG email is made of is assembled here in one order:

    message  ->  attachment list  ->  sign-off

so a member can drop a document in and have it appear where the club's
emails always put it, without the model being asked to write any of it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

# Tags the editor can legitimately produce. Anything else is dropped, so a
# pasted page of markup cannot smuggle script, style or remote content into a
# member's outgoing mail.
_ALLOWED_TAGS = {
    "p", "br", "b", "strong", "i", "em", "u", "s", "strike", "h1", "h2", "h3",
    "ul", "ol", "li", "blockquote", "pre", "code", "a", "span", "div",
}
_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "li", "blockquote", "pre", "tr"}

_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9]+)((?:\s[^<>]*)?)/?>")
_SCRIPTISH_RE = re.compile(r"<(script|style|iframe|object|embed)[^>]*>[\s\S]*?</\1>", re.I)
_EVENT_ATTR_RE = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_STYLE_ATTR_RE = re.compile(r"\sstyle\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.I)
_HREF_RE = re.compile(r"\shref\s*=\s*(\"([^\"]*)\"|'([^']*)')", re.I)

# Declarations a mail client renders, mirroring the composer's contract. A
# style attribute used to be copied through whole, which let a pasted page
# carry url() fetches into a member's outgoing mail.
_STYLE_PROPERTIES = frozenset({
    "color", "background-color", "font-family", "font-size", "font-weight",
    "font-style", "text-decoration", "text-decoration-line", "text-align",
    "line-height", "margin-left", "padding-left", "list-style-type",
})
_UNSAFE_STYLE_VALUE = re.compile(r"url\s*\(|expression|javascript:|@import|[<>{}\\]", re.I)


def safe_style(value: str) -> str:
    kept = []
    for declaration in (value or "").split(";"):
        name, separator, raw = declaration.partition(":")
        if not separator:
            continue
        prop, val = name.strip().lower(), raw.strip()
        if prop in _STYLE_PROPERTIES and val and not _UNSAFE_STYLE_VALUE.search(val):
            kept.append(f"{prop}: {val}")
    return "; ".join(kept)


def looks_like_html(body: str) -> bool:
    """True only for markup the editor can legitimately produce.

    Free text such as ``2 <angle> options`` is not HTML. Treating any
    angle-bracketed word as markup silently deleted text from the outgoing
    message.
    """
    return any(match.group(2).lower() in _ALLOWED_TAGS for match in _TAG_RE.finditer(body or ""))


def sanitize_html(body: str) -> str:
    """Strip anything that is not safe, renderable email formatting."""
    cleaned = _SCRIPTISH_RE.sub("", body or "")
    cleaned = _EVENT_ATTR_RE.sub("", cleaned)

    def keep(match: re.Match[str]) -> str:
        closing, tag, attrs = match.group(1), match.group(2).lower(), match.group(3) or ""
        if tag not in _ALLOWED_TAGS:
            return ""
        if closing:
            return f"</{tag}>"
        # Inline style carries the toolbar's colour, font, size and alignment,
        # but only declarations from the allowlist survive.
        style = _STYLE_ATTR_RE.search(attrs)
        declarations = safe_style(style.group(1) or style.group(2) or "") if style else ""
        safe_attrs = f' style="{html.escape(declarations, quote=True)}"' if declarations else ""
        if tag == "a":
            href = _HREF_RE.search(attrs)
            url = (href.group(2) or href.group(3) or "") if href else ""
            if url.lower().startswith(("http://", "https://", "mailto:")):
                safe_attrs += f' href="{html.escape(url, quote=True)}"'
        return f"<{tag}{safe_attrs}>"

    return _TAG_RE.sub(keep, cleaned)


def html_to_text(body: str) -> str:
    """A readable plain-text alternative, not a dump of the markup.

    Recipients whose client prefers text/plain were being shown tags. Lists
    become dashes and blocks become blank lines so the text copy reads as the
    same email rather than as debris.
    """
    text = _SCRIPTISH_RE.sub("", body or "")
    # List items are single-spaced: a bulleted list that double-spaces reads
    # as four separate paragraphs in the text copy.
    text = re.sub(r"</li\s*>", "", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    for tag in _BLOCK_TAGS - {"li"}:
        text = re.sub(rf"</{tag}\s*>", "\n\n", text, flags=re.I)
    text = re.sub(r"</(ul|ol)\s*>", "\n\n", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def text_to_html(body: str) -> str:
    """Escape typed text and give it real paragraphs rather than pre-wrap."""
    escaped = html.escape(body or "").strip()
    if not escaped:
        return ""
    paragraphs = [p.strip().replace("\n", "<br>") for p in re.split(r"\n\s*\n", escaped) if p.strip()]
    return "".join(f'<p style="margin:0 0 12px 0;">{p}</p>' for p in paragraphs)


# --- sign-off ----------------------------------------------------------

@dataclass(frozen=True)
class SignOff:
    """The block at the foot of every YUCG email.

    Modelled on what the club actually sends: logo beside the member's name
    and pronouns, their role, the organization, then contact links. It is
    assembled from stored fields rather than written by the model, because a
    sign-off is a fact about the sender and must never be a generated guess.
    """

    name: str = ""
    pronouns: str = ""
    role: str = ""
    organization: str = "Yale Undergraduate Consulting Group"
    linkedin_url: str = ""
    phone: str = ""
    logo_url: str = ""

    @property
    def is_empty(self) -> bool:
        return not any((self.name.strip(), self.role.strip(), self.linkedin_url.strip(), self.phone.strip()))


def sign_off_html(sign_off: SignOff) -> str:
    """Table-based so it survives Gmail, Outlook and Apple Mail alike."""
    if sign_off.is_empty:
        return ""
    e = lambda v: html.escape((v or "").strip())  # noqa: E731 - local shorthand

    name_line = e(sign_off.name)
    if sign_off.pronouns.strip():
        name_line += f' <em style="font-weight:400;">({e(sign_off.pronouns)})</em>'

    lines = [f'<div style="font-weight:700;color:#00356b;">{name_line}</div>']
    if sign_off.role.strip():
        lines.append(f'<div>{e(sign_off.role)}</div>')
    if sign_off.organization.strip():
        lines.append(f'<div>{e(sign_off.organization)}</div>')

    links = []
    if sign_off.linkedin_url.strip():
        url = sign_off.linkedin_url.strip()
        if url.lower().startswith(("http://", "https://")):
            links.append(f'<a href="{html.escape(url, quote=True)}" style="color:#00356b;">LinkedIn</a>')
    if sign_off.phone.strip():
        links.append(e(sign_off.phone))
    if links:
        lines.append(f'<div style="margin-top:2px;">{" | ".join(links)}</div>')

    logo_cell = ""
    if sign_off.logo_url.strip() and sign_off.logo_url.strip().lower().startswith(("http://", "https://", "cid:")):
        logo_cell = (
            f'<td style="vertical-align:top;padding-right:14px;">'
            f'<img src="{html.escape(sign_off.logo_url.strip(), quote=True)}" alt="YUCG" '
            f'width="72" style="display:block;width:72px;height:auto;border:0;"></td>'
        )

    return (
        '<table cellpadding="0" cellspacing="0" border="0" '
        'style="margin-top:20px;border-top:1px solid #c8dced;padding-top:12px;'
        'font-family:Lato,Helvetica,Arial,sans-serif;font-size:13px;line-height:1.45;color:#333;">'
        f'<tr>{logo_cell}<td style="vertical-align:top;">{"".join(lines)}</td></tr></table>'
    )


def sign_off_text(sign_off: SignOff) -> str:
    if sign_off.is_empty:
        return ""
    parts = []
    name = sign_off.name.strip()
    if sign_off.pronouns.strip():
        name = f"{name} ({sign_off.pronouns.strip()})"
    if name:
        parts.append(name)
    for value in (sign_off.role, sign_off.organization):
        if value.strip():
            parts.append(value.strip())
    links = [v.strip() for v in (sign_off.linkedin_url, sign_off.phone) if v.strip()]
    if links:
        parts.append(" | ".join(links))
    return "\n".join(parts)


# --- attachments -------------------------------------------------------

def attachment_list_html(filenames: list[str]) -> str:
    """Name what is attached, at the foot of the message.

    A file silently riding along in the MIME envelope is easy for a recipient
    to miss, so the club's emails say what was sent. This is rendered from the
    real attachment list, never from the model claiming a document exists.
    """
    names = [n.strip() for n in filenames if n and n.strip()]
    if not names:
        return ""
    items = "".join(f'<li style="margin:0 0 2px 0;">{html.escape(n)}</li>' for n in names)
    return (
        '<div style="margin-top:16px;font-size:13px;color:#52647a;">'
        f'<div style="font-weight:700;">Attached</div>'
        f'<ul style="margin:4px 0 0 18px;padding:0;">{items}</ul></div>'
    )


def attachment_list_text(filenames: list[str]) -> str:
    names = [n.strip() for n in filenames if n and n.strip()]
    if not names:
        return ""
    return "Attached:\n" + "\n".join(f"- {n}" for n in names)


# --- assembly ----------------------------------------------------------

def render_email(
    body: str,
    sign_off: SignOff | None = None,
    attachments: list[str] | None = None,
) -> tuple[str, str]:
    """Return ``(plain_text, html)`` for one outgoing message."""
    body = body or ""
    if looks_like_html(body):
        body_html = sanitize_html(body)
        body_text = html_to_text(body)
    else:
        body_html = text_to_html(body)
        body_text = body.strip()

    files = attachments or []
    attach_html = attachment_list_html(files)
    attach_text = attachment_list_text(files)

    sig_html = sign_off_html(sign_off) if sign_off else ""
    sig_text = sign_off_text(sign_off) if sign_off else ""

    text = "\n\n".join(p for p in (body_text, attach_text, sig_text and "--\n" + sig_text) if p)
    html_doc = (
        '<html><body style="margin:0;padding:0;font-family:Lato,Helvetica,Arial,sans-serif;'
        'font-size:14px;line-height:1.5;color:#1a1a1a;">'
        f"{body_html}{attach_html}{sig_html}</body></html>"
    )
    return text, html_doc
