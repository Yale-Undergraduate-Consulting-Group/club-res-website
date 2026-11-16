"""Managing a resource's files from its page: upload, replace, versions, restore, delete.

Who may manage: an admin on any resource, and on a past project a member of its
team (``resources.managed_ids``). A resource the member cannot see answers 404, a
visible one they may not manage 403, and a file that is not this resource's, or is
admin-only and the member is not an admin, 404. Admin-only files are never
indexed: no text of theirs reaches search or the AI.

Bytes go straight to the private documents bucket through a presigned PUT, like
the document register; with no bucket (local development) the app takes the PUT
itself. A page upload is charged to the uploader's storage quota; the import's
server-side versions are not (``resources.IMPORT_KEY_PREFIX``).
"""
from __future__ import annotations

import asyncio
import os
import secrets
import tempfile
import time
from pathlib import Path

from fastapi import HTTPException

from app.database import get_db
from app.routers import workspace
from app.services import document_storage, resource_ask, resource_text, resources
from app.services.transaction_audit import audit


async def _access(db, user: dict, resource_id: int, document_id: int | None = None) -> tuple[dict, dict | None]:
    """(resource, file row or None when ``document_id`` is None), or the 404/403 above."""
    resource = await resources.get_visible(db, user, resource_id)
    if not resource:
        raise HTTPException(404, "Not found")
    if resource_id not in await resources.managed_ids(db, user, [resource_id]):
        raise HTTPException(403, "You may not change this resource's files")
    if document_id is None:
        return resource, None
    file = await (await db.execute(
        """SELECT f.document_id, f.admin_only, d.current_version FROM resource_files f
           JOIN workspace_documents d ON d.id = f.document_id
           WHERE f.resource_id = ? AND f.document_id = ?""", (resource_id, document_id),
    )).fetchone()
    if not file or (file["admin_only"] and not resources.is_admin(user)):
        raise HTTPException(404, "Not found")
    return resource, dict(file)


async def _item(db, user: dict, resource_id: int) -> dict:
    row = await resources.get_visible(db, user, resource_id)
    return (await resources.present(db, user, [row]))[0]


async def _version(db, document_id: int, version_id: int, *, uploader: int | None = None):
    row = await (await db.execute(
        "SELECT * FROM workspace_document_versions WHERE id = ? AND document_id = ?", (version_id, document_id))).fetchone()
    if not row or (uploader is not None and row["uploaded_by"] != uploader):
        raise HTTPException(404, "Upload not found")
    return dict(row)


async def _drop_empty_file(db, document_id: int) -> None:
    """Remove a file that never got a version (and its rows)."""
    left = await (await db.execute(
        "SELECT 1 FROM workspace_document_versions WHERE document_id = ?", (document_id,))).fetchone()
    if not left:
        await db.execute("DELETE FROM resource_files WHERE document_id = ?", (document_id,))
        await db.execute("DELETE FROM workspace_documents WHERE id = ?", (document_id,))


# --- Upload ------------------------------------------------------------------

async def start_upload(user: dict, resource_id: int, payload) -> dict:
    try:
        target = document_storage.storage_target()
    except RuntimeError:
        raise HTTPException(503, "Document storage is not configured") from None
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        _, file = await _access(db, user, resource_id, payload.file_id)
        usage = await workspace.storage_usage(db, user["id"])
        if payload.byte_size > usage["available_bytes"]:
            raise HTTPException(409, {"message": "Storage quota exceeded; pending uploads also reserve space", **usage})
        stamp = int(time.time())
        if file is None:
            cursor = await db.execute(
                "INSERT INTO workspace_documents(title, owner_user_id, visibility, created_at) VALUES (?,?, 'private', ?)",
                (payload.filename, user["id"], stamp))
            document_id = cursor.lastrowid
            await db.execute(
                "INSERT INTO resource_files(resource_id, document_id, source_key, admin_only) VALUES (?,?,?, 0)",
                (resource_id, document_id, f"upload:{document_id}"))
        else:
            document_id = file["document_id"]
        key = f"{resources.UPLOAD_KEY_PREFIX}{secrets.token_urlsafe(24)}"
        cursor = await db.execute(
            """INSERT INTO workspace_document_versions(document_id, uploaded_by, object_key, filename, byte_size, content_type,
               created_at, storage_bucket) VALUES (?,?,?,?,?,?,?,?)""",
            (document_id, user["id"], key, payload.filename, payload.byte_size, payload.content_type, stamp, target))
        version_id = cursor.lastrowid
        await audit(db, user["id"], "resource_file_upload_reserve", "resource", resource_id,
                    {"document_id": document_id, "version_id": version_id, "byte_size": payload.byte_size})
        await db.commit()
    finally:
        await db.close()
    try:
        if target == document_storage.LOCAL:
            url = document_storage.local_upload_url(version_id)
            headers = {"Content-Type": payload.content_type}
        else:
            url = await workspace.s3_call("generate_presigned_url", ClientMethod="put_object", Params={
                "Bucket": target, "Key": key, "ContentType": payload.content_type,
                "ContentLength": payload.byte_size, "IfNoneMatch": "*"}, ExpiresIn=300)
            headers = {"Content-Type": payload.content_type, "If-None-Match": "*"}
    except Exception:
        # No URL reached the caller, so nothing can be written under this key.
        db = await get_db()
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("DELETE FROM workspace_document_versions WHERE id = ? AND state = 'pending'", (version_id,))
            await _drop_empty_file(db, document_id)
            await db.commit()
        finally:
            await db.close()
        raise
    db = await get_db()
    try:
        # Conservative upper bound recorded after signing, never creation time.
        await db.execute("UPDATE workspace_document_versions SET presign_expires_at = ? WHERE id = ?", (int(time.time()) + 300, version_id))
        await db.commit()
    finally:
        await db.close()
    return {"file_id": document_id, "version_id": version_id, "upload": {"url": url, "method": "PUT", "headers": headers}}


async def receive_local_upload(user: dict, token: str, request) -> dict:
    """The PUT behind a local upload link (no-bucket environments): the body is
    streamed to the reserved path, must be exactly the reserved size, and is
    refused when something is already stored there."""
    version_id = document_storage.read_local_upload_token(token)
    if version_id is None:
        raise HTTPException(404, "Not found")
    db = await get_db()
    try:
        row = await (await db.execute(
            """SELECT v.object_key, v.byte_size, f.resource_id, f.document_id
               FROM workspace_document_versions v JOIN resource_files f ON f.document_id = v.document_id
               WHERE v.id = ? AND v.state = 'pending' AND v.storage_bucket = ? AND v.uploaded_by = ?""",
            (version_id, document_storage.LOCAL, user["id"]))).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        # The signed link is not authority: the member must still manage the resource.
        await _access(db, user, row["resource_id"], row["document_id"])
    finally:
        await db.close()
    path = document_storage.local_path(row["object_key"])
    if path.exists():
        raise HTTPException(409, "This upload already has data")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > row["byte_size"]:
        raise HTTPException(413, "The file is larger than reserved")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        out = open(part, "xb")
    except FileExistsError:
        raise HTTPException(409, "This upload is already in progress") from None
    try:
        size = 0
        with out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > row["byte_size"]:
                    raise HTTPException(413, "The file is larger than reserved")
                out.write(chunk)
        if size != row["byte_size"]:
            raise HTTPException(422, "The file is not the reserved size")
        try:
            os.link(part, path)
        except FileExistsError:
            raise HTTPException(409, "This upload already has data") from None
    finally:
        part.unlink(missing_ok=True)
    return {"ok": True}


async def _verify(row: dict) -> str:
    """Check the uploaded object; returns the storage version id to record."""
    if row["storage_bucket"] == document_storage.LOCAL:
        path = document_storage.local_path(row["object_key"])
        if not path.is_file() or path.stat().st_size != row["byte_size"]:
            raise HTTPException(409, "The upload is missing or not the reserved size")
        return document_storage.LOCAL
    from botocore.exceptions import ClientError

    try:
        head = await workspace.s3_call("head_object", Bucket=workspace.stored_bucket(row), Key=row["object_key"])
    except ClientError as exc:
        raise HTTPException(409, "The upload has not arrived in storage") from exc
    if head["ContentLength"] != row["byte_size"] or head.get("ContentType") != row["content_type"] \
            or not head.get("VersionId") or head["VersionId"] == "null":
        raise HTTPException(409, "File metadata mismatch or S3 versioning is disabled")
    return head["VersionId"]


async def _pages(row: dict, storage_version: str) -> list[str]:
    """The text of a stored version, one string per page or slide (a temporary
    copy from the bucket, or the local file in place)."""
    if row["storage_bucket"] == document_storage.LOCAL:
        return await asyncio.to_thread(resource_text.extract_pages, document_storage.local_path(row["object_key"]), row["filename"])
    with tempfile.TemporaryDirectory(prefix="yucg-resource-") as tmp:
        path = Path(tmp) / "file"
        try:
            await workspace.s3_call("download_file", Bucket=workspace.stored_bucket(row), Key=row["object_key"],
                                    Filename=str(path), ExtraArgs={"VersionId": storage_version})
        except Exception as exc:
            raise HTTPException(502, "Could not read the stored file") from exc
        return await asyncio.to_thread(resource_text.extract_pages, path, row["filename"])


async def complete_upload(user: dict, resource_id: int, document_id: int, version_id: int) -> dict:
    db = await get_db()
    try:
        _, file = await _access(db, user, resource_id, document_id)
        row = await _version(db, document_id, version_id, uploader=user["id"])
        if row["state"] == "ready":
            return await _item(db, user, resource_id)
        if row["state"] != "pending":
            raise HTTPException(409, "Upload is no longer pending")
        stored = await _verify(row)
        pages = [] if file["admin_only"] else await _pages(row, stored)
        await db.execute("BEGIN IMMEDIATE")
        _, file = await _access(db, user, resource_id, document_id)
        state = (await _version(db, document_id, version_id))["state"]
        if state != "ready":
            if state != "pending":
                raise HTTPException(409, "Upload is no longer pending")
            await db.execute("UPDATE workspace_document_versions SET state = 'ready', s3_version_id = ? WHERE id = ? AND state = 'pending'",
                             (stored, version_id))
            # Like the register: a reservation older than the current version never takes the pointer back.
            moved = await db.execute(
                "UPDATE workspace_documents SET current_version = ?, revision = revision + 1 WHERE id = ? AND current_version < ?",
                (version_id, document_id, version_id))
            if moved.rowcount:
                await resource_ask.store_text(db, resource_id, document_id, [] if file["admin_only"] else pages)
                await resources.reindex(db, resource_id)
            await audit(db, user["id"], "resource_file_upload", "resource", resource_id,
                        {"document_id": document_id, "version_id": version_id, "byte_size": row["byte_size"],
                         "replaced": bool(file["current_version"])})
        await db.commit()
        return await _item(db, user, resource_id)
    finally:
        await db.close()


async def cancel_upload(user: dict, resource_id: int, document_id: int, version_id: int) -> dict:
    """Seal a reservation the browser could not fill. In S3 the key is sealed
    with an empty object (the same step as the register's abandon, without waiting
    for the URL to expire) and the version stays as an abandoned row; locally
    nothing is left to seal, so the reservation is removed. A new file left with
    no version at all disappears with it."""
    db = await get_db()
    try:
        await _access(db, user, resource_id, document_id)
        row = await _version(db, document_id, version_id, uploader=user["id"])
        if row["state"] in ("abandoned", "failed"):
            return {"ok": True}
        if row["state"] != "pending":
            raise HTTPException(409, "Only pending uploads may be cancelled")
        local = row["storage_bucket"] == document_storage.LOCAL
        if local:
            path = document_storage.local_path(row["object_key"])
            path.unlink(missing_ok=True)
            sealed = None
        else:
            sealed = await workspace.seal_reservation(row)
        await db.execute("BEGIN IMMEDIATE")
        await _access(db, user, resource_id, document_id)
        state = (await _version(db, document_id, version_id))["state"]
        if state == "pending":
            if local:
                await db.execute("DELETE FROM workspace_document_versions WHERE id = ?", (version_id,))
                await _drop_empty_file(db, document_id)
            else:
                await db.execute("UPDATE workspace_document_versions SET state = 'abandoned', s3_version_id = ? WHERE id = ?",
                                 (sealed, version_id))
            await audit(db, user["id"], "resource_file_upload_cancel", "resource", resource_id,
                        {"document_id": document_id, "version_id": version_id, "released_bytes": row["byte_size"]})
        elif state != "abandoned":
            raise HTTPException(409, "Upload state changed; reload before cancelling")
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


# --- Versions ----------------------------------------------------------------

async def list_versions(user: dict, resource_id: int, document_id: int) -> list[dict]:
    db = await get_db()
    try:
        _, file = await _access(db, user, resource_id, document_id)
        rows = await (await db.execute(
            """SELECT v.id, v.filename, v.byte_size, v.content_type, v.created_at, u.name AS uploaded_by_name
               FROM workspace_document_versions v LEFT JOIN users u ON u.id = v.uploaded_by
               WHERE v.document_id = ? AND v.state = 'ready' ORDER BY v.id DESC""", (document_id,))).fetchall()
        return [{**dict(row), "current": row["id"] == file["current_version"]} for row in rows]
    finally:
        await db.close()


async def restore_version(user: dict, resource_id: int, document_id: int, version_id: int) -> dict:
    db = await get_db()
    try:
        _, file = await _access(db, user, resource_id, document_id)
        row = await _version(db, document_id, version_id)
        if row["state"] != "ready":
            raise HTTPException(404, "Upload not found")
        if file["current_version"] == version_id:
            return await _item(db, user, resource_id)
        pages = [] if file["admin_only"] else await _pages(row, row["s3_version_id"])
        await db.execute("BEGIN IMMEDIATE")
        _, file = await _access(db, user, resource_id, document_id)
        if (await _version(db, document_id, version_id))["state"] != "ready":
            raise HTTPException(409, "That version is no longer available")
        await db.execute("UPDATE workspace_documents SET current_version = ?, revision = revision + 1 WHERE id = ?",
                         (version_id, document_id))
        await resource_ask.store_text(db, resource_id, document_id, pages)
        await resources.reindex(db, resource_id)
        await audit(db, user["id"], "resource_file_restore", "resource", resource_id,
                    {"document_id": document_id, "version_id": version_id, "from_version_id": file["current_version"]})
        await db.commit()
        return await _item(db, user, resource_id)
    finally:
        await db.close()


async def delete_file(user: dict, resource_id: int, document_id: int) -> dict:
    """Delete every stored version of the file, then its rows (a storage failure
    leaves the rows so the delete can be retried)."""
    db = await get_db()
    try:
        await _access(db, user, resource_id, document_id)
        await resources.delete_stored_versions(db, [document_id])
        await db.execute("BEGIN IMMEDIATE")
        await _access(db, user, resource_id, document_id)
        versions = (await (await db.execute(
            "SELECT COUNT(*) AS n FROM workspace_document_versions WHERE document_id = ?", (document_id,))).fetchone())["n"]
        await resource_ask.drop_text(db, document_id)
        await db.execute("DELETE FROM resource_files WHERE document_id = ?", (document_id,))
        await db.execute("DELETE FROM workspace_document_shares WHERE document_id = ?", (document_id,))
        await db.execute("DELETE FROM workspace_document_versions WHERE document_id = ?", (document_id,))
        await db.execute("DELETE FROM workspace_documents WHERE id = ?", (document_id,))
        await resources.reindex(db, resource_id)
        await audit(db, user["id"], "resource_file_delete", "resource", resource_id,
                    {"document_id": document_id, "versions": versions})
        await db.commit()
        return await _item(db, user, resource_id)
    finally:
        await db.close()
