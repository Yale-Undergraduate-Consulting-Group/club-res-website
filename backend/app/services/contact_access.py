"""One contact visibility rule shared by list, detail, drafting and campaign APIs."""
from fastapi import HTTPException


async def require_contact_access(db, contact_id: int, user: dict):
    row = await (await db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))).fetchone()
    if not row:
        raise HTTPException(404, "Contact not found")
    owner_id = row["owner_id"]
    if user.get("role") != "admin" and owner_id is not None and int(owner_id) != int(user["id"]):
        raise HTTPException(404, "Contact not found")
    return row
