"""Member-owned email drafts and controlled test delivery."""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.auth_deps import get_current_user, get_current_user_optional

router = APIRouter()


class TestSendRequest(BaseModel):
    to_email: str
    subject: str
    body: str
    attachment_ids: Optional[list[int]] = None

class DraftSaveRequest(BaseModel):
    contact_id: int
    subject: str = ""
    body: str = ""


class DraftUpdateRequest(BaseModel):
    subject: str
    body: str


@router.post("/test-send")
async def test_send_email(req: TestSendRequest, user: dict = Depends(get_current_user)):
    """Send a one-off test of exactly what a recipient would receive."""
    from app.services.gmail_api import send_via_gmail_api_multipart
    try:
        attachments_data = []
        if req.attachment_ids:
            from app.routers.attachments import get_attachment_data_for_send
            attachments_data = await get_attachment_data_for_send(req.attachment_ids, user["id"])
        from app.services.settings_service import load_sign_off
        await send_via_gmail_api_multipart(
            user_id=user["id"],
            to_email=req.to_email,
            subject=req.subject,
            body=req.body,
            attachments=attachments_data if attachments_data else None,
            sign_off=await load_sign_off(user["id"]),
        )
        return {"ok": True, "message": f"Test email sent to {req.to_email}"}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        err = str(e)
        raise HTTPException(500, f"Failed to send: {err}")


@router.post("/generated")
async def save_generated_email_draft(
    req: DraftSaveRequest,
    user: dict = Depends(get_current_user),
):
    """Persist a member-owned Studio draft for an existing shared contact."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM contacts WHERE id = ?", (req.contact_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "Contact not found")
        cursor = await db.execute(
            """INSERT INTO generated_emails (user_id, contact_id, subject, body)
               VALUES (?, ?, ?, ?)""",
            (user["id"], req.contact_id, req.subject, req.body),
        )
        await db.commit()
        return {"id": int(cursor.lastrowid), "contact_id": req.contact_id, "subject": req.subject, "body": req.body}
    finally:
        await db.close()


@router.patch("/generated/{draft_id}")
async def update_generated_email_draft(
    draft_id: int,
    req: DraftUpdateRequest,
    user: dict = Depends(get_current_user),
):
    """Update a Studio draft only when it belongs to the current member."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """UPDATE generated_emails SET subject = ?, body = ?
               WHERE id = ? AND user_id = ?""",
            (req.subject, req.body, draft_id, user["id"]),
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Draft not found")
        return {"ok": True, "id": draft_id}
    finally:
        await db.close()


@router.delete("/generated/{draft_id}")
async def delete_generated_email_draft(
    draft_id: int,
    user: dict = Depends(get_current_user),
):
    """Delete a Studio draft only when it belongs to the current member."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM generated_emails WHERE id = ? AND user_id = ?",
            (draft_id, user["id"]),
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Draft not found")
        return {"ok": True, "id": draft_id}
    finally:
        await db.close()


@router.get("/generated")
async def list_generated_emails(
    contact_id: Optional[int] = None,
    sort: str = "created_desc",
    user: dict | None = Depends(get_current_user_optional),
):
    """List generated emails for the current user. Returns [] if not authenticated."""
    if not user:
        return []
    order_map = {
        "created_desc": "ge.created_at DESC",
        "created_asc": "ge.created_at ASC",
        "contact": "c.name",
    }
    order = order_map.get(sort, "ge.created_at DESC")
    db = await get_db()
    try:
        if contact_id:
            cursor = await db.execute(
                f"""SELECT ge.*, c.name, c.email, c.company FROM generated_emails ge
                    JOIN contacts c ON ge.contact_id = c.id
                    WHERE ge.user_id = ? AND ge.contact_id = ? ORDER BY {order}""",
                (user["id"], contact_id),
            )
        else:
            cursor = await db.execute(
                f"""SELECT ge.*, c.name, c.email, c.company FROM generated_emails ge
                    JOIN contacts c ON ge.contact_id = c.id
                    WHERE ge.user_id = ? ORDER BY {order}""",
                (user["id"],),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.delete("/generated")
async def clear_generated_emails_cache(user: dict = Depends(get_current_user)):
    """Remove all of the current member's saved Studio drafts (does not delete contacts)."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM generated_emails WHERE user_id = ?", (user["id"],))
        cur = await db.execute("SELECT changes() AS n")
        row = await cur.fetchone()
        n = int(row["n"]) if row and row["n"] is not None else 0
        await db.commit()
        return {"ok": True, "deleted": n}
    finally:
        await db.close()
