"""Cover images for alumni news posts (docs/product/ALUMNI-PORTAL.md).

The same path as the document register: the server reserves a row and a
private object key, the browser PUTs the bytes straight to the documents bucket
through a presigned URL (or, with no bucket, to the app's local upload link),
and the server checks the result before anything can show it. The check reads
only the first bytes of the object: a cover is a PNG, JPEG or WebP image by its
magic bytes, of the declared type, at most 5 MB. Anything else (SVG, HTML, a
renamed file) is deleted and refused.

Covers are served by the app, never by a storage URL handed to the browser, so
every response carries the verified type and ``X-Content-Type-Options: nosniff``;
the link is short-lived and the request is re-checked against the post.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time

from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from app.routers import workspace
from app.services import document_storage

log = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
TYPES = ("image/png", "image/jpeg", "image/webp")
KEY_PREFIX = "alumni/covers/"
#: A reserved or finished cover no post uses is removed at the next reservation after this.
UNATTACHED_SECONDS = 24 * 3600
#: How long a cover link (to read) and an upload link (to write) work.
LINK_SECONDS = 600
UPLOAD_SECONDS = 300
#: Signed-link purposes (document_storage.signed_token).
READ_PURPOSE = "alumni-cover"
UPLOAD_PURPOSE = "alumni-cover-upload"


def sniff(head: bytes) -> str | None:
    """The image type ``head`` (the first bytes of a file) starts with, of the three a cover may be."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def cover_url(base: str, image_id: int) -> str:
    return f"{base}/images/{image_id}?token={document_storage.signed_token(READ_PURPOSE, image_id, LINK_SECONDS)}"


def upload_url(base: str, image_id: int) -> str:
    return f"{base}/images/local-upload?token={document_storage.signed_token(UPLOAD_PURPOSE, image_id, UPLOAD_SECONDS)}"


def check_request(byte_size: int, content_type: str) -> None:
    if content_type not in TYPES:
        raise HTTPException(415, "A cover must be a PNG, JPEG or WebP image")
    if not 0 < byte_size <= MAX_BYTES:
        raise HTTPException(413, "A cover must be 5 MB or smaller")


async def stale_rows(db) -> list[dict]:
    """Covers older than a day that no post uses; the caller deletes the rows
    in its transaction and the objects after it commits (``delete_objects``)."""
    rows = await (await db.execute(
        """SELECT * FROM alumni_post_images i WHERE i.created_at < ?
           AND NOT EXISTS (SELECT 1 FROM alumni_posts p WHERE p.cover_image_id = i.id)""",
        (int(time.time()) - UNATTACHED_SECONDS,),
    )).fetchall()
    return [dict(row) for row in rows]


def _delete_sync(row: dict) -> None:
    if row["storage_bucket"] == document_storage.LOCAL:
        document_storage.local_path(row["object_key"]).unlink(missing_ok=True)
        return
    import boto3

    client = boto3.client("s3")
    version = row.get("s3_version_id")
    if not version:
        # Never completed: find what (if anything) the browser stored under the key.
        from botocore.exceptions import ClientError

        try:
            version = client.head_object(Bucket=row["storage_bucket"], Key=row["object_key"]).get("VersionId")
        except ClientError:
            return
    if version and version != "null":
        client.delete_object(Bucket=row["storage_bucket"], Key=row["object_key"], VersionId=version)


async def delete_objects(rows: list[dict]) -> None:
    """Delete the stored bytes of covers whose rows are gone. In S3 this deletes
    the named version; the bucket's lifecycle expires any other noncurrent
    version within 30 days. A failure is logged with the key so it can be
    removed by hand; the row is already gone, so nothing can link to it."""
    for row in rows:
        try:
            await asyncio.to_thread(_delete_sync, row)
        except Exception:
            log.exception("Could not delete alumni cover object %s in %s", row["object_key"], row["storage_bucket"])


def new_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(24)}"


async def presign(target: str, key: str, byte_size: int, content_type: str) -> tuple[str, dict]:
    """(url, headers) for the browser's PUT of a reserved cover in S3."""
    url = await workspace.s3_call("generate_presigned_url", ClientMethod="put_object", Params={
        "Bucket": target, "Key": key, "ContentType": content_type,
        "ContentLength": byte_size, "IfNoneMatch": "*"}, ExpiresIn=UPLOAD_SECONDS)
    return url, {"Content-Type": content_type, "If-None-Match": "*"}


def _read_head_local(path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(16)


async def verify(row: dict) -> tuple[str, str | None]:
    """(verified type or None when the bytes are not an allowed image, storage
    version). Raises 409 when the upload has not arrived or is not the
    reserved size and type. Reads 16 bytes, never the whole object."""
    if row["storage_bucket"] == document_storage.LOCAL:
        path = document_storage.local_path(row["object_key"])
        if not path.is_file() or path.stat().st_size != row["byte_size"]:
            raise HTTPException(409, "The upload is missing or not the reserved size")
        return sniff(await asyncio.to_thread(_read_head_local, path)), document_storage.LOCAL
    from botocore.exceptions import ClientError

    bucket = workspace.stored_bucket(row)
    try:
        head = await workspace.s3_call("head_object", Bucket=bucket, Key=row["object_key"])
    except ClientError as exc:
        raise HTTPException(409, "The upload has not arrived in storage") from exc
    version = head.get("VersionId")
    if head["ContentLength"] != row["byte_size"] or head.get("ContentType") != row["declared_type"] \
            or not version or version == "null":
        raise HTTPException(409, "File metadata mismatch or S3 versioning is disabled")
    part = await workspace.s3_call("get_object", Bucket=bucket, Key=row["object_key"], VersionId=version, Range="bytes=0-15")
    try:
        first = await asyncio.to_thread(part["Body"].read, 16)
    finally:
        part["Body"].close()
    return sniff(first), version


_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Disposition": "inline",
    "Cache-Control": f"private, max-age={LINK_SECONDS}",
}


async def response(row: dict):
    """The cover's bytes, streamed, with the verified type."""
    media_type = row["verified_type"]
    if row["storage_bucket"] == document_storage.LOCAL:
        path = document_storage.local_path(row["object_key"])
        if not path.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(path, media_type=media_type, headers=_HEADERS)
    stored = await workspace.s3_call("get_object", Bucket=workspace.stored_bucket(row), Key=row["object_key"],
                                     VersionId=row["s3_version_id"])
    body = stored["Body"]

    async def chunks():
        try:
            while chunk := await asyncio.to_thread(body.read, 64 * 1024):
                yield chunk
        finally:
            body.close()

    return StreamingResponse(chunks(), media_type=media_type,
                             headers={**_HEADERS, "Content-Length": str(stored["ContentLength"])})
