"""Who may see and use a saved email template.

One rule, enforced here for every caller: a template is the club's when an
administrator owns it (or nobody does, as with the club's seeded Intro), and
private to its author otherwise. Members see their own and the club's; an
administrator can remove a club template, nobody else's private one.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

_SELECT = """SELECT t.id, t.name, t.subject, t.body, t.industry, t.use_case, t.user_id, t.created_at,
                    u.name AS owner_name,
                    CASE WHEN t.user_id IS NULL OR u.role = 'admin' THEN 'club' ELSE 'mine' END AS scope
             FROM email_templates t LEFT JOIN users u ON u.id = t.user_id"""
_VISIBLE = "(t.user_id = ? OR t.user_id IS NULL OR u.role = 'admin')"


def _shape(row: Any, user: dict) -> dict[str, Any]:
    template = dict(row)
    template["mine"] = template["user_id"] is not None and int(template["user_id"]) == int(user["id"])
    template["editable"] = template["mine"] or (template["scope"] == "club" and user.get("role") == "admin")
    return template


async def visible_templates(db, user: dict, industry: str | None = None) -> list[dict[str, Any]]:
    sql = f"{_SELECT} WHERE {_VISIBLE}"
    args: list[Any] = [user["id"]]
    if industry:
        sql += " AND (t.industry = ? OR t.industry IS NULL)"
        args.append(industry)
    rows = await (await db.execute(f"{sql} ORDER BY t.name", args)).fetchall()
    return [_shape(r, user) for r in rows]


async def require_template(db, template_id: int, user: dict) -> dict[str, Any]:
    """The template if this member may use it; otherwise 404, so another
    member's private template is not confirmed to exist."""
    row = await (await db.execute(f"{_SELECT} WHERE t.id = ? AND {_VISIBLE}", (template_id, user["id"]))).fetchone()
    if not row:
        raise HTTPException(404, "Template not found")
    return _shape(row, user)


# The club's one template, which every member sends: Amber's introduction.
# {first} is the recipient's first name, {sender_first_name} the sender's (from
# their sign-off), and {personalisation} the line the member confirms for each
# company before sending. The signature is added when mail is sent.
CLUB_TEMPLATE_NAME = "Intro"
CLUB_TEMPLATE_SUBJECT = "Introduction from the Yale Undergraduate Consulting Group"
CLUB_TEMPLATE_BODY = (
    "Hi {first},\n\n"
    "I hope you're doing well!\n\n"
    "I'm {sender_first_name} and I'm reaching out from the Yale Undergraduate Consulting Group. "
    "We're a team of undergraduate students who work with companies to better understand their customers, "
    "explore new markets, and identify opportunities for growth.\n\n"
    "I'd love to work with {company} on a project in the new year. {personalisation}\n\n"
    "If this sounds interesting, I'd love to hop on a 15 minute call to talk about specific project ideas "
    "further and answer any questions. Please do let me know when you'd be available!\n\n"
    "Thank you!\n\n"
    "Best,\n"
    "{sender_first_name}"
)


async def add_approach_tracking(db) -> None:
    """Migration outreach:5. Which saved template (and which approach) a
    campaign row was written from, for Admin → Metrics "What earns replies".
    Per row, not per campaign, because one campaign can carry a different
    message to each company. Rows from before this keep NULL: their approach
    was never recorded. (It used to seed four starter templates; migration 7
    now puts the club's one template in place.)"""
    have = {r[1] for r in await (await db.execute("PRAGMA table_info(campaign_contacts)")).fetchall()}
    for column, kind in (("template_id", "INTEGER"), ("angle", "TEXT")):
        if column not in have:
            await db.execute(f"ALTER TABLE campaign_contacts ADD COLUMN {column} {kind}")


async def one_club_template(db) -> None:
    """Migration outreach:7. Every member sends the same template, so the
    seeded starters (the only rows with no author: members' and
    administrators' own templates always carry their user_id) become one. The
    seeded Intro keeps its id, so its sends stay together in Admin → Metrics,
    and its subject; its body becomes the club's. The other starters are
    removed; their sends keep their approach. Templates anyone saved are not
    touched."""
    seeded = await (await db.execute("SELECT id, name FROM email_templates WHERE user_id IS NULL ORDER BY id")).fetchall()
    keep = next((row[0] for row in seeded if (row[1] or "").strip().lower() == CLUB_TEMPLATE_NAME.lower()), None)
    if keep is None:
        cursor = await db.execute(
            "INSERT INTO email_templates (name, use_case, subject, body, user_id) VALUES (?, 'intro', ?, ?, NULL)",
            (CLUB_TEMPLATE_NAME, CLUB_TEMPLATE_SUBJECT, CLUB_TEMPLATE_BODY))
        keep = cursor.lastrowid
    else:
        await db.execute("UPDATE email_templates SET body = ?, use_case = 'intro' WHERE id = ?", (CLUB_TEMPLATE_BODY, keep))
    await db.execute("DELETE FROM email_templates WHERE user_id IS NULL AND id != ?", (keep,))
