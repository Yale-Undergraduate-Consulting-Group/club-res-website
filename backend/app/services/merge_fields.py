"""Turn one written message into one message per recipient.

The app has told members to write {first}, {last}, {company} and {title} in
templates since templates existed, and nothing has ever substituted them: a
follow-up step defined as "Hi {first}," would arrive in the recipient's inbox
exactly like that. Nothing had been sent, so nobody found out.

Two rules make this safe to run unattended:

A field the contact does not have is never quietly blanked. "Hi ," is worse
than not sending, because it is visibly generated and cannot be taken back, so
a row missing a field its message uses is reported and held out rather than
rendered. The one exception is {personalisation}: the member confirms each
company's line before sending, and confirming an empty one is a choice, so it
renders as nothing and the sentence before it stands on its own.

An unknown placeholder is an error, not literal text. A typo like {firstname}
would otherwise sail through and be mailed verbatim.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

# What a member may write. Kept deliberately short: every one of these is on
# the contact row, derivable from it, or given by the send itself (who sends
# it, the line the member confirmed for the company), so rendering cannot fail
# halfway through a send.
FIELDS = ("first", "last", "full_name", "title", "company", "date", "sender_first_name", "personalisation")

# Shown beside the template editor, so a member sees what each one becomes.
FIELD_HELP = {
    "first": "their first name",
    "last": "their last name (blank for a one-word name)",
    "full_name": "their full name",
    "title": "their job title",
    "company": "their employer",
    "date": "the day the email is written",
    "sender_first_name": "your first name, from your sign-off",
    "personalisation": "the one line you confirm for their company when sending (may be left empty)",
}

#: Fields that may render as nothing; every other field holds the recipient when blank.
OPTIONAL_FIELDS = frozenset({"personalisation"})

# The preview in the template editor renders against this made-up person, so
# no real contact's details appear while a member is still typing.
SAMPLE_RECIPIENT = {
    "name": "Alex Rivera",
    "title": "Head of Partnerships",
    "company": "Northwind Health",
    "email": "alex.rivera@example.com",
}
SAMPLE_PERSONALISATION = "Your confirmed line for Northwind Health goes here."

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_TRAILING_SPACE = re.compile(r"[ \t]+(?=\n|$)")


class MergeError(ValueError):
    """A message that cannot be rendered for this recipient."""


def first_name(name: str | None) -> str:
    parts = re.sub(r"\s+", " ", str(name or "").strip()).split(" ")
    return parts[0] if parts and parts[0] else ""


def available_fields(contact: dict[str, Any], *, sender_first_name: str = "", personalisation: str = "") -> dict[str, str]:
    name = re.sub(r"\s+", " ", str(contact.get("name") or "").strip())
    parts = [p for p in name.split(" ") if p]
    return {
        "first": parts[0] if parts else "",
        "last": parts[-1] if len(parts) >= 2 else "",
        "full_name": name,
        "title": str(contact.get("title") or "").strip(),
        "company": str(contact.get("company") or "").strip(),
        # The day it is rendered, which for a follow-up is the day it is sent
        # rather than the day it was written.
        "date": date.today().strftime("%-d %B %Y"),
        "sender_first_name": (sender_first_name or "").strip(),
        # One line in a paragraph: a pasted newline would split the sentence.
        "personalisation": re.sub(r"\s+", " ", personalisation or "").strip(),
    }


def used_fields(text: str) -> list[str]:
    return sorted({m.group(1) for m in _PLACEHOLDER.finditer(text or "")})


def unknown_fields(text: str) -> list[str]:
    return [name for name in used_fields(text) if name not in FIELDS]


def render(text: str, contact: dict[str, Any], *, sender_first_name: str = "", personalisation: str = "") -> str:
    """Substitute placeholders for one contact, or raise MergeError."""
    unknown = unknown_fields(text)
    if unknown:
        raise MergeError(
            f"{', '.join('{' + u + '}' for u in unknown)} is not a field. "
            f"Use {', '.join('{' + f + '}' for f in FIELDS)}."
        )
    values = available_fields(contact, sender_first_name=sender_first_name, personalisation=personalisation)
    missing = [name for name in used_fields(text) if not values.get(name) and name not in OPTIONAL_FIELDS]
    if "sender_first_name" in missing:
        raise MergeError("The message uses {sender_first_name}: add your name to your sign-off in Profile → Settings.")
    if missing:
        who = contact.get("email") or contact.get("name") or "this recipient"
        raise MergeError(
            f"{who} has no {', '.join(missing)} on record, and the message uses it."
        )
    # An empty line leaves the space before it; nothing else may end a line in spaces.
    return _TRAILING_SPACE.sub("", _PLACEHOLDER.sub(lambda m: values[m.group(1)], text or ""))


def render_for_each(subject: str, body: str, contacts: list[dict[str, Any]], *,
                    sender_first_name: str = "", personalisation: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
                "subject": render(subject, contact, sender_first_name=sender_first_name, personalisation=personalisation),
                "body": render(body, contact, sender_first_name=sender_first_name, personalisation=personalisation),
            })
        except MergeError as exc:
            held.append({
                "contact_id": contact.get("id"),
                "email": contact.get("email"),
                "name": contact.get("name"),
                "reason": str(exc),
            })
    return ready, held
