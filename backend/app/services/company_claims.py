"""Who in the club is working which company.

The failure this prevents is the one a client notices: two members of the same
society cold-emailing the same organisation in the same week, each unaware of
the other. Nothing in the app said who was on what - a contact owned by someone
else was silently dropped from an import, with no name attached.

A claim is deliberately soft. It records that a member started work on a
company and when they last did something, and it goes stale on its own after
CLAIM_IDLE_DAYS so nobody has to remember to release anything. Discovery and
drafting are never blocked by someone else's claim: a member may well have a
reason. Sending is where it counts, and that check lives in send_gating, keyed
on the recipient rather than the company.
"""

from __future__ import annotations

import os
import re
from typing import Any

from app.database import get_db


def claim_idle_days() -> int:
    """How long a claim survives without activity.

    Long enough to cover a term's normal pace of work, short enough that a
    company someone touched once in October is free by December.
    """
    try:
        return max(1, min(int(os.getenv("CLAIM_IDLE_DAYS", "30") or 30), 365))
    except ValueError:
        return 30


def company_key(company_name: str | None, company_domain: str | None = None) -> str:
    """One key per company, preferring the domain.

    A domain is the only stable identifier here: "The Walt Disney Company",
    "Walt Disney Co" and "Disney (Studios)" are one company and three strings,
    and the register already holds several spellings of each.
    """
    domain = (company_domain or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain).split("/")[0]
    domain = domain[4:] if domain.startswith("www.") else domain
    if domain:
        return f"d:{domain}"
    name = re.sub(r"[^a-z0-9]+", " ", (company_name or "").lower()).strip()
    # Trailing company forms carry no identity and differ between sources.
    name = re.sub(r"\b(inc|llc|ltd|limited|plc|corp|corporation|co|company|the)\b", " ", name)
    return "n:" + re.sub(r"\s+", " ", name).strip()


async def claim_company(user_id: int, company_name: str, company_domain: str | None = None) -> dict[str, Any]:
    """Record that this member is working the company, and say who else is.

    Never refuses. The return says whether someone else holds it so the caller
    can show that, which is the whole point: the member finds out before they
    spend a week on it, not after the client mentions it.
    """
    key = company_key(company_name, company_domain)
    if key in ("n:", ""):
        return {"ok": False, "reason": "no company"}
    db = await get_db()
    try:
        row = await (await db.execute(
            f"""SELECT c.id, c.member_id, c.company_name, c.last_activity_at, u.email, u.name
                FROM company_claims c JOIN users u ON u.id = c.member_id
                WHERE c.company_key = ? AND c.released_at IS NULL
                  AND c.last_activity_at > datetime('now', '-{claim_idle_days()} days')""",
            (key,),
        )).fetchone()
        if row and int(row["member_id"]) != int(user_id):
            return {
                "ok": True,
                "held_by_other": True,
                "member_id": int(row["member_id"]),
                "member": row["name"] or row["email"],
                "since": row["last_activity_at"],
                "company_name": row["company_name"],
            }
        await db.execute(
            """INSERT INTO company_claims (company_key, company_name, member_id)
               VALUES (?, ?, ?)
               ON CONFLICT(company_key) DO UPDATE SET
                 member_id = excluded.member_id,
                 company_name = excluded.company_name,
                 last_activity_at = CURRENT_TIMESTAMP,
                 released_at = NULL""",
            (key, (company_name or "").strip(), int(user_id)),
        )
        await db.commit()
        return {"ok": True, "held_by_other": False, "member_id": int(user_id)}
    finally:
        await db.close()


async def claims_for(companies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Live claims for a page of companies, keyed by company_key."""
    keys = {company_key(c.get("company_name"), c.get("company_domain")) for c in companies}
    keys.discard("n:")
    keys.discard("")
    if not keys:
        return {}
    ordered = sorted(keys)
    placeholders = ",".join("?" * len(ordered))
    db = await get_db()
    try:
        rows = await (await db.execute(
            f"""SELECT c.company_key, c.member_id, c.last_activity_at, u.email, u.name
                FROM company_claims c JOIN users u ON u.id = c.member_id
                WHERE c.company_key IN ({placeholders}) AND c.released_at IS NULL
                  AND c.last_activity_at > datetime('now', '-{claim_idle_days()} days')""",
            ordered,
        )).fetchall()
    finally:
        await db.close()
    return {
        row["company_key"]: {
            "member_id": int(row["member_id"]),
            "member": row["name"] or row["email"],
            "since": row["last_activity_at"],
        }
        for row in rows
    }


async def release_company(user_id: int, company_name: str, company_domain: str | None = None) -> bool:
    """Hand a company back. Only the holder may."""
    key = company_key(company_name, company_domain)
    db = await get_db()
    try:
        cur = await db.execute(
            "UPDATE company_claims SET released_at = CURRENT_TIMESTAMP WHERE company_key = ? AND member_id = ?",
            (key, int(user_id)),
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()
