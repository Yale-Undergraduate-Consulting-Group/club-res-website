"""Club Resources API. Every route decides access in app.services.resources;
a resource or file the member may not see answers 404, the same as a missing one."""
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.auth_deps import get_current_admin, get_current_user
from app.database import get_db
from app.routers.workspace import download_url
from app.services import document_storage, resources

router = APIRouter()

def not_found() -> HTTPException:
    return HTTPException(404, "Not found")


@router.get("")
async def list_resources(
    q: str = Query("", max_length=200), kind: str = "", semester: str = "", industry: str = "",
    limit: int = 60, offset: int = 0, user: dict = Depends(get_current_user),
):
    db = await get_db()
    try:
        rows, total, facets = await resources.search(
            db, user, q=q, kind=kind, semester=semester, industry=industry, limit=limit, offset=offset)
        return {"items": await resources.present(db, user, rows), "total": total, "facets": facets}
    finally:
        await db.close()


@router.get("/local-file")
async def local_file(token: str, user: dict = Depends(get_current_user)):
    """Serve a file from the local storage directory (no-bucket environments),
    for the short-lived link the download route issued to this session."""
    version_id = document_storage.read_local_token(token)
    if version_id is None:
        raise not_found()
    db = await get_db()
    try:
        row = await (await db.execute(
            """SELECT v.object_key, v.filename, v.storage_bucket, f.resource_id, f.document_id
               FROM workspace_document_versions v
               JOIN resource_files f ON f.document_id = v.document_id
               WHERE v.id = ? AND v.state = 'ready'""",
            (version_id,),
        )).fetchone()
        if not row or row["storage_bucket"] != document_storage.LOCAL:
            raise not_found()
        # The signed link is not authority: a copied link still has to pass the
        # resource/file access check for the member opening it.
        if not await resources.file_for_download(db, user, row["resource_id"], row["document_id"]):
            raise not_found()
        path = document_storage.local_path(row["object_key"])
        if not path.is_file():
            raise not_found()
        return FileResponse(path, filename=row["filename"], media_type="application/octet-stream", content_disposition_type="attachment")
    finally:
        await db.close()


@router.post("/team/rematch")
async def rematch_team(admin: dict = Depends(get_current_admin)):
    """Match team names to member accounts again (after new members joined)."""
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        counts = await resources.match_team(db)
        await db.commit()
        return counts
    finally:
        await db.close()


@router.get("/{resource_id}")
async def get_resource(resource_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        row = await resources.get_visible(db, user, resource_id)
        if not row:
            raise not_found()
        return (await resources.present(db, user, [row]))[0]
    finally:
        await db.close()


@router.get("/{resource_id}/files/{file_id}/download")
async def download_file(resource_id: int, file_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        found = await resources.file_for_download(db, user, resource_id, file_id)
        if not found:
            raise not_found()
        resource, file = found
        url = await download_url(db, file_id)
        # Recorded once the link is issued: the link is the download.
        await db.execute("BEGIN IMMEDIATE")
        await resources.audit_download(db, user, resource, file)
        await db.commit()
        name = await (await db.execute(
            "SELECT v.filename FROM workspace_documents d JOIN workspace_document_versions v ON v.id = d.current_version WHERE d.id = ?",
            (file_id,),
        )).fetchone()
        return {"url": url, "filename": name["filename"] if name else ""}
    finally:
        await db.close()


@router.delete("/{resource_id}")
async def delete_resource(resource_id: int, admin: dict = Depends(get_current_admin)):
    db = await get_db()
    try:
        if not await resources.delete_resource(db, admin, resource_id):
            raise not_found()
        return {"ok": True}
    finally:
        await db.close()
