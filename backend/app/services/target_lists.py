"""Target lists (outreach_campaigns): who may read or change one, and who is on it."""
from __future__ import annotations

from fastapi import HTTPException


async def require_worklist(db, list_id: int, user: dict, *, write: bool = False):
    """A club list is readable by everyone and changed by administrators; an
    individual list belongs to its owner (and administrators). Someone else's
    individual list is a 404, so its existence is not confirmed."""
    row = await (await db.execute("SELECT * FROM outreach_campaigns WHERE id=?", (list_id,))).fetchone()
    if not row:
        raise HTTPException(404, "Target list not found")
    if row["type"] == "individual" and row["owner_id"] != user["id"] and user.get("role") != "admin":
        raise HTTPException(404, "Target list not found")
    if write and row["type"] == "community" and user.get("role") != "admin":
        raise HTTPException(403, "Administrator required to change a club target list")
    return row
