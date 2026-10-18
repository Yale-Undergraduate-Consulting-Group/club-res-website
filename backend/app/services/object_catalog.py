"""S3 object catalog. App code uses stored_objects.id, never a client-supplied key."""
from __future__ import annotations

import hashlib
import os
from typing import Any

PREFIXES = (
    "prospects/current.xlsx",
    "prospects/versions/",
    "prospects/releases/",
    "packs/",
    "decks/",
    "corpus/",
    "imports/",
    "discovery/",
    "attachments/",
    "exports/",
)


def catalog_bucket() -> str:
    return (os.getenv("CATALOG_BUCKET") or "").strip()


def _client():
    import boto3

    return boto3.client("s3", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"))


def put_bytes(key: str, body: bytes, content_type: str = "application/octet-stream") -> dict[str, Any]:
    bucket = catalog_bucket()
    if not bucket:
        raise RuntimeError("CATALOG_BUCKET is not set")
    _client().put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
    return {"bucket": bucket, "key": key, "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}


def get_bytes(key: str) -> bytes:
    bucket = catalog_bucket()
    if not bucket:
        raise RuntimeError("CATALOG_BUCKET is not set")
    return _client().get_object(Bucket=bucket, Key=key)["Body"].read()

def object_metadata(key: str) -> dict[str, Any]:
    """Return stable change metadata without downloading the object body."""
    bucket = catalog_bucket()
    if not bucket:
        raise RuntimeError("CATALOG_BUCKET is not set")
    obj = _client().head_object(Bucket=bucket, Key=key)
    updated = obj.get("LastModified")
    return {
        "etag": str(obj.get("ETag") or "").strip('"'),
        "size": int(obj.get("ContentLength") or 0),
        "updated": updated.isoformat() if updated else None,
    }


def list_prefix(prefix: str, max_keys: int = 50) -> list[dict[str, Any]]:
    bucket = catalog_bucket()
    if not bucket:
        return []
    resp = _client().list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=max_keys)
    return [
        {"key": o.get("Key"), "size": o.get("Size"), "updated": str(o.get("LastModified") or "")}
        for o in resp.get("Contents") or []
    ]


def record_sync(
    kind: str,
    s3_key: str,
    sha256: str,
    byte_size: int,
    content_type: str = "",
    source: str = "app",
) -> None:
    """Best-effort catalog row. Uses a sync sqlite/psycopg path via asyncio if a loop is running."""
    import asyncio

    async def _write() -> None:
        from app.database import get_db

        db = await get_db()
        try:
            await db.execute(
                """INSERT OR REPLACE INTO stored_objects
                   (s3_key, kind, sha256, byte_size, content_type, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (s3_key, kind, sha256, byte_size, content_type or None, source),
            )
            await db.commit()
        finally:
            await db.close()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_write())
        return
    loop.create_task(_write())
