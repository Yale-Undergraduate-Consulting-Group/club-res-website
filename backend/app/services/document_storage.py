"""Where document bytes live: the private documents bucket, or a local
directory when there is no bucket (local development and the beta box only).

The register's own routes still presign uploads against S3; this module is the
server-side path used by the Resources import, and the local stand-in that
lets that import (and downloads of what it stored) run without AWS.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import mimetypes
import os
import time
from pathlib import Path

#: ``workspace_document_versions.storage_bucket`` for a file in the local directory.
LOCAL = "local"
LOCAL_URL_PATH = "/api/resources/local-file"
_LOCAL_ENVIRONMENTS = {"", "local", "beta"}


def configured_bucket() -> str | None:
    return os.getenv("DOCUMENTS_BUCKET") or os.getenv("CATALOG_BUCKET") or None


def local_enabled() -> bool:
    """Local storage is only ever used with no bucket configured and a
    non-production APP_ENV; production without a bucket fails instead."""
    return not configured_bucket() and (os.getenv("APP_ENV") or "").strip().lower() in _LOCAL_ENVIRONMENTS


def local_dir() -> Path:
    return Path(os.getenv("DOCUMENTS_LOCAL_DIR") or "/tmp/yucg-documents")


def local_path(key: str) -> Path:
    root = local_dir().resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError("Storage key escapes the local directory")
    return path


def storage_target() -> str:
    """The bucket name (or LOCAL) the next stored file goes to."""
    bucket = configured_bucket()
    if bucket:
        return bucket
    if local_enabled():
        return LOCAL
    raise RuntimeError("Document storage is not configured: set DOCUMENTS_BUCKET (local storage is only for local and beta environments)")


def guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _put_sync(target: str, key: str, path: Path, content_type: str) -> str:
    if target == LOCAL:
        destination = local_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Copy, not move: the caller's temp file is theirs to remove.
        with open(path, "rb") as source, open(destination, "wb") as out:
            while chunk := source.read(1024 * 1024):
                out.write(chunk)
        return LOCAL
    import boto3

    client = boto3.client("s3")
    # Multipart for the large decks; the bucket enforces encryption by default.
    client.upload_file(str(path), target, key, ExtraArgs={"ContentType": content_type})
    head = client.head_object(Bucket=target, Key=key)
    version = head.get("VersionId")
    if not version or version == "null":
        raise RuntimeError("S3 versioning is disabled on the documents bucket")
    return version


async def store_file(target: str, key: str, path: Path, content_type: str) -> str:
    """Upload ``path`` under ``key``; returns the version id the register records."""
    return await asyncio.to_thread(_put_sync, target, key, path, content_type)


def _delete_sync(bucket: str, key: str, version_id: str | None) -> None:
    if bucket == LOCAL:
        local_path(key).unlink(missing_ok=True)
        return
    if not version_id:
        return
    import boto3

    boto3.client("s3").delete_object(Bucket=bucket, Key=key, VersionId=version_id)


async def delete_version(bucket: str, key: str, version_id: str | None) -> None:
    await asyncio.to_thread(_delete_sync, bucket, key, version_id)


# --- Local download links ----------------------------------------------------
# A local file is served by the app itself, behind the member's session and a
# short-lived signed link, so a link copied out of the browser stops working.

def _secret() -> bytes:
    from app.jwt_utils import JWT_SECRET

    return JWT_SECRET.encode()


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def local_download_url(version_id: int, expires: int = 60) -> str:
    payload = f"{int(version_id)}.{int(time.time()) + expires}"
    token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{LOCAL_URL_PATH}?token={token}.{_sign(payload)}"


def read_local_token(token: str) -> int | None:
    """The version id a valid, unexpired local link names, else None."""
    try:
        encoded, signature = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        version_id, expires = payload.split(".")
        if not hmac.compare_digest(signature, _sign(payload)) or int(expires) < time.time():
            return None
        return int(version_id)
    except (ValueError, UnicodeDecodeError):
        return None


# --- Local upload links ------------------------------------------------------
# The local stand-in for a presigned PUT: one version, short-lived, and signed
# with a purpose marker so a download link can never be used to write.

LOCAL_UPLOAD_URL_PATH = "/api/resources/local-upload"


def local_upload_url(version_id: int, expires: int = 300) -> str:
    payload = f"u.{int(version_id)}.{int(time.time()) + expires}"
    token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{LOCAL_UPLOAD_URL_PATH}?token={token}.{_sign(payload)}"


def read_local_upload_token(token: str) -> int | None:
    """The version id a valid, unexpired local upload link names, else None."""
    try:
        encoded, signature = token.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        purpose, version_id, expires = payload.split(".")
        if purpose != "u" or not hmac.compare_digest(signature, _sign(payload)) or int(expires) < time.time():
            return None
        return int(version_id)
    except (ValueError, UnicodeDecodeError):
        return None
