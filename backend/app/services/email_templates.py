"""Who may see and use a saved email template.

One rule, enforced here for every caller: a template is the club's when an
administrator owns it (or nobody does, as with the starter templates), and
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


# Written for a Yale undergraduate consulting group writing to people it has
# not met: plain, one question each, and nothing claimed that is not true of
# every recipient. The sender's signature is added when mail is sent, and a
# recipient missing a field a template uses is held, not mailed with a gap.
STARTER_TEMPLATES = (
    ("Intro", "intro",
     "Introduction from the Yale Undergraduate Consulting Group",
     "Hi {first},\n\n"
     "I'm a member of the Yale Undergraduate Consulting Group (YUCG), a student-run consulting group at Yale. "
     "Our teams take on short analytical projects for outside organisations, and I'm writing to introduce us to {company}.\n\n"
     "I'd value hearing what {company} is working through at the moment, and whether a student team could be useful. "
     "Would you be open to a 15-minute call in the next few weeks?"),
    ("Alumni connection (only if you know they studied at Yale)", "alumni connection",
     "Yale student reaching out",
     "Hi {first},\n\n"
     "I'm a student at Yale and a member of the Yale Undergraduate Consulting Group (YUCG). "
     "I'm writing to you directly because you are a Yale graduate.\n\n"
     "YUCG teams work on short analytical projects for outside organisations. As {title} at {company}, "
     "you will have a clear view of where that kind of help is useful and where it is not. "
     "Would you be willing to spend 15 minutes telling me?"),
    ("Project pitch", "project pitch",
     "A student project for {company}?",
     "Hi {first},\n\n"
     "I'm a member of the Yale Undergraduate Consulting Group (YUCG). Our teams of undergraduates take on analytical "
     "projects for outside organisations, for example market sizing, customer research or a review of a process.\n\n"
     "If {company} has a question like that waiting on a backlog, a YUCG team could take a first pass at it. "
     "Is there one you would be willing to talk through for 15 minutes?"),
    ("Follow-up (after an earlier email)", "follow-up",
     "Following up: Yale Undergraduate Consulting Group",
     "Hi {first},\n\n"
     "I wrote a little while ago about the Yale Undergraduate Consulting Group (YUCG) and wanted to follow up in case "
     "my note was buried.\n\n"
     "A short call with someone at {company} would be a real help to us. "
     "Would a 15-minute call in the next two weeks suit you?"),
)


async def add_approach_tracking(db) -> None:
    """Migration outreach:5. Which saved template (and which approach) a
    campaign row was written from, for Admin → Metrics "What earns replies".
    Per row, not per campaign, because one campaign can carry a different
    message to each company. Rows from before this keep NULL: their approach
    was never recorded. Also seeds the club's starter templates, once, when
    the club has none of its own."""
    have = {r[1] for r in await (await db.execute("PRAGMA table_info(campaign_contacts)")).fetchall()}
    for column, kind in (("template_id", "INTEGER"), ("angle", "TEXT")):
        if column not in have:
            await db.execute(f"ALTER TABLE campaign_contacts ADD COLUMN {column} {kind}")
    club = await (await db.execute(
        """SELECT 1 FROM email_templates t LEFT JOIN users u ON u.id = t.user_id
           WHERE t.user_id IS NULL OR u.role = 'admin' LIMIT 1""")).fetchone()
    if not club:
        await db.executemany(
            "INSERT INTO email_templates (name, use_case, subject, body, user_id) VALUES (?, ?, ?, ?, NULL)",
            STARTER_TEMPLATES,
        )
