"""Turn one written message into one message per recipient.

The app has told members to write {first}, {last}, {company} and {title} in
templates since templates existed, and nothing has ever substituted them: a
follow-up step defined as "Hi {first}," would arrive in the recipient's inbox
exactly like that. Nothing had been sent, so nobody found out.

Two rules make this safe to run unattended:

A field the contact does not have is never quietly blanked. "Hi ," is worse
than not sending, because it is visibly generated and cannot be taken back, so
a row missing a field its message uses is reported and held out rather than
rendered.

An unknown placeholder is an error, not literal text. A typo like {firstname}
would otherwise sail through and be mailed verbatim.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

# What a member may write. Kept deliberately short: every one of these is
# either on the contact row or derivable from it without a lookup, so
# rendering cannot fail halfway through a send.
FIELDS = ("first", "last", "full_name", "title", "company", "date")

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


class MergeError(ValueError):
    """A message that cannot be rendered for this recipient."""


def available_fields(contact: dict[str, Any]) -> dict[str, str]:
    name = re.sub(r"\s+", " ", str(contact.get("name") or "").strip())
    parts = [p for p in name.split(" ") if p]
    first = parts[0] if parts else ""
    last = parts[-1] if len(parts) >= 2 else ""
    return {
        "first": first,
        "last": last,
        "full_name": name,
        "title": str(contact.get("title") or "").strip(),
        "company": str(contact.get("company") or "").strip(),
        # The day it is rendered, which for a follow-up is the day it is sent
        # rather than the day it was written.
        "date": date.today().strftime("%-d %B %Y"),
    }


def used_fields(text: str) -> list[str]:
    return sorted({m.group(1) for m in _PLACEHOLDER.finditer(text or "")})


def unknown_fields(text: str) -> list[str]:
    return [name for name in used_fields(text) if name not in FIELDS]


def render(text: str, contact: dict[str, Any]) -> str:
    """Substitute placeholders for one contact, or raise MergeError."""
    unknown = unknown_fields(text)
    if unknown:
        raise MergeError(
            f"{', '.join('{' + u + '}' for u in unknown)} is not a field. "
            f"Use {', '.join('{' + f + '}' for f in FIELDS)}."
        )
    values = available_fields(contact)
    missing = [name for name in used_fields(text) if not values.get(name)]
    if missing:
        who = contact.get("email") or contact.get("name") or "this recipient"
        raise MergeError(
            f"{who} has no {', '.join(missing)} on record, and the message uses it."
        )
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], text or "")


def render_for_each(subject: str, body: str, contacts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Render one message per contact.

    Returns (ready, held). A recipient whose message cannot be rendered is
    held with the reason rather than silently dropped or half-rendered, so the
    member can fix the template or the contact and see who is affected.
    """
    ready: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for contact in contacts:
        try:
            ready.append({
                "contact_id": contact.get("id"),
                "email": contact.get("email"),
                "subject": render(subject, contact),
                "body": render(body, contact),
            })
        except MergeError as exc:
            held.append({
                "contact_id": contact.get("id"),
                "email": contact.get("email"),
                "name": contact.get("name"),
                "reason": str(exc),
            })
    return ready, held
