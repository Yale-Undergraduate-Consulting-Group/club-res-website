"""
Attachments API - Email attachment library (intro PDFs, past workstreams, etc.)
"""
import uuid
import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.database import get_db, row_to_dict
from app.auth_deps import get_current_user, get_current_admin
from app.services.audit_service import log_audit

router = APIRouter()

# Storage folder next to backend
ATTACHMENTS_DIR = Path(__file__).parent.parent.parent / "attachments"
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".gif"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB (Gmail limit per attachment)


def _ensure_dir():
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

@router.get("")
async def list_attachments(user: dict = Depends(get_current_user)):
    """List club files plus private files uploaded by this member."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id, filename, display_name, file_size, mime_type, created_at, owner_user_id
               FROM email_attachments
               WHERE owner_user_id IS NULL OR owner_user_id = ?
               ORDER BY display_name, filename""",
            (user["id"],),
        )
        rows = await cursor.fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        await db.close()


@router.get("/onedrive")
async def list_onedrive(admin: dict = Depends(get_current_admin)):
    from app.services.graph_onedrive import configured, list_folder

    if not configured():
        return {"configured": False, "items": []}
    try:
        return {"configured": True, "items": list_folder()}
    except Exception as e:
        raise HTTPException(400, str(e)) from e


class OneDriveAttach(BaseModel):
    item_id: str

@router.post("/onedrive/attach")
async def attach_onedrive(body: OneDriveAttach, admin: dict = Depends(get_current_admin)):
    from app.services.graph_onedrive import download_item, list_folder

    try:
        allowed_ids = {str(item.get("id")) for item in list_folder() if item.get("id") and not item.get("folder")}
        if body.item_id not in allowed_ids:
            raise HTTPException(403, "The selected file is outside the configured club folder")
        content, filename, mime_type = download_item(body.item_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large. Max {MAX_FILE_SIZE // (1024 * 1024)} MB.")
    _ensure_dir()
    storage_name = f"{uuid.uuid4().hex}{ext}"
    storage_path = ATTACHMENTS_DIR / storage_name
    storage_path.write_bytes(content)
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO email_attachments (filename, display_name, storage_path, file_size, mime_type)
               VALUES (?, ?, ?, ?, ?)""",
            (filename, filename, str(storage_path), len(content), mime_type),
        )
        await db.commit()
        row_id = cursor.lastrowid
        cursor = await db.execute(
            "SELECT id, filename, display_name, file_size, mime_type, created_at FROM email_attachments WHERE id = ?",
            (row_id,),
        )
        row = await cursor.fetchone()
        await log_audit(admin["id"], "attachment_onedrive", "attachment", str(row_id), filename)
        return row_to_dict(row)
    except Exception:
        if storage_path.exists():
            storage_path.unlink()
        raise
    finally:
        await db.close()

@router.post("")
async def upload_attachment(
    file: UploadFile = File(...),
    display_name: str | None = Form(None),
    user: dict = Depends(get_current_user),
):
    """Upload a private file for this member's drafts."""
    _ensure_dir()
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large. Max {MAX_FILE_SIZE // (1024*1024)} MB.")
    storage_name = f"{uuid.uuid4().hex}{ext}"
    storage_path = ATTACHMENTS_DIR / storage_name
    storage_path.write_bytes(content)
    mime_type = mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO email_attachments
               (filename, display_name, storage_path, file_size, mime_type, owner_user_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file.filename or "unnamed", display_name or file.filename or "Unnamed",
             str(storage_path), len(content), mime_type, user["id"]),
        )
        await db.commit()
        row_id = cursor.lastrowid
        cursor = await db.execute("SELECT id, filename, display_name, file_size, mime_type, created_at FROM email_attachments WHERE id = ?", (row_id,))
        row = await cursor.fetchone()
        await log_audit(user["id"], "attachment_upload", "attachment", str(row_id), file.filename or "unnamed")
        return row_to_dict(row)
    except Exception:
        if storage_path.exists():
            storage_path.unlink()
        raise
    finally:
        await db.close()


@router.delete("/{attachment_id}")
async def delete_attachment(attachment_id: int, user: dict = Depends(get_current_user)):
    """Remove a private file, or any file when acting as an administrator."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT storage_path, owner_user_id FROM email_attachments WHERE id = ?",
            (attachment_id,),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Attachment not found")
        if row["owner_user_id"] not in (None, user["id"]) and user.get("role") != "admin":
            raise HTTPException(403, "You do not own this attachment")
        if row["owner_user_id"] is None and user.get("role") != "admin":
            raise HTTPException(403, "Only administrators can delete club attachments")
        references = await (await db.execute(
            "SELECT id, attachment_ids_json FROM campaigns WHERE attachment_ids_json IS NOT NULL"
        )).fetchall()
        if any(attachment_id in json.loads(item["attachment_ids_json"] or "[]") for item in references):
            raise HTTPException(409, "Remove this file from its campaign before deleting it")
        storage_path = Path(row["storage_path"])
        await db.execute("DELETE FROM email_attachments WHERE id = ?", (attachment_id,))
        await db.commit()
        if storage_path.exists():
            storage_path.unlink()
        await log_audit(user["id"], "attachment_delete", "attachment", str(attachment_id), "Deleted")
        return {"ok": True}
    finally:
        await db.close()


async def get_attachment_data_for_send(
    attachment_ids: list[int], user_id: int
) -> list[tuple[bytes, str, str]]:
    """Read only club files or files owned by the sending member."""
    if not attachment_ids:
        return []
    db = await get_db()
    try:
        placeholders = ",".join("?" * len(attachment_ids))
        cursor = await db.execute(
            f"""SELECT id, storage_path, filename, mime_type FROM email_attachments
                WHERE id IN ({placeholders})
                  AND (owner_user_id IS NULL OR owner_user_id = ?)""",
            [*attachment_ids, user_id],
        )
        rows = await cursor.fetchall()
        if len(rows) != len(set(attachment_ids)):
            raise HTTPException(404, "One or more attachments are unavailable")
        result = []
        for r in rows:
            path = Path(r["storage_path"])
            if not path.exists():
                raise HTTPException(404, f"Attachment file is missing: {r['filename']}")
            result.append((path.read_bytes(), r["filename"] or "attachment", r["mime_type"] or "application/octet-stream"))
        return result
    finally:
        await db.close()


@router.get("/{attachment_id}/download")
async def download_attachment(attachment_id: int, user: dict = Depends(get_current_user)):
    """Download a club file or one owned by this member."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT storage_path, filename, mime_type FROM email_attachments
               WHERE id = ? AND (owner_user_id IS NULL OR owner_user_id = ?)""",
            (attachment_id, user["id"]),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Attachment not found")
        path = Path(row["storage_path"])
        if not path.exists():
            raise HTTPException(404, "File not found on disk")
        return FileResponse(
            path,
            filename=row["filename"] or "attachment",
            media_type=row["mime_type"] or "application/octet-stream",
        )
    finally:
        await db.close()
