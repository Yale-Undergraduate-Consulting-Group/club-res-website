"""Microsoft Graph helpers for the club OneDrive folder. Token from GRAPH_ACCESS_TOKEN."""
from __future__ import annotations

import os
from typing import Any

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"


def configured() -> bool:
    return bool((os.getenv("GRAPH_ACCESS_TOKEN") or "").strip())


def _headers() -> dict[str, str]:
    token = (os.getenv("GRAPH_ACCESS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("GRAPH_ACCESS_TOKEN is not set")
    return {"Authorization": f"Bearer {token}"}


def _folder_path() -> str:
    raw = (os.getenv("GRAPH_DRIVE_FOLDER") or "/YUCG").strip()
    return raw if raw.startswith("/") else f"/{raw}"


def list_folder(path: str | None = None) -> list[dict[str, Any]]:
    folder = path or _folder_path()
    url = f"{GRAPH}/me/drive/root:{folder}:/children"
    with httpx.Client(timeout=25.0) as client:
        r = client.get(url, headers=_headers())
        if r.status_code >= 400:
            raise RuntimeError(f"Graph list failed: {r.status_code} {r.text[:240]}")
        items = r.json().get("value") or []
    return [
        {
            "id": i.get("id"),
            "name": i.get("name"),
            "size": i.get("size"),
            "mime": (i.get("file") or {}).get("mimeType"),
            "folder": bool(i.get("folder")),
            "web_url": i.get("webUrl"),
        }
        for i in items
    ]


def download_item(item_id: str) -> tuple[bytes, str, str]:
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        meta = client.get(f"{GRAPH}/me/drive/items/{item_id}", headers=_headers())
        if meta.status_code >= 400:
            raise RuntimeError(f"Graph item failed: {meta.status_code}")
        info = meta.json()
        name = info.get("name") or "onedrive-file"
        mime = (info.get("file") or {}).get("mimeType") or "application/octet-stream"
        body = client.get(f"{GRAPH}/me/drive/items/{item_id}/content", headers=_headers())
        if body.status_code >= 400:
            raise RuntimeError(f"Graph download failed: {body.status_code}")
        return body.content, name, mime


def upload_bytes(filename: str, data: bytes, folder: str | None = None) -> dict[str, Any]:
    dest = f"{folder or _folder_path()}/{filename}".replace("//", "/")
    url = f"{GRAPH}/me/drive/root:{dest}:/content"
    with httpx.Client(timeout=60.0) as client:
        r = client.put(url, headers=_headers(), content=data)
        if r.status_code >= 400:
            raise RuntimeError(f"Graph upload failed: {r.status_code} {r.text[:240]}")
        j = r.json()
    return {"id": j.get("id"), "name": j.get("name"), "web_url": j.get("webUrl")}


def upload_pack_if_configured(data: bytes) -> dict[str, Any] | None:
    if not configured():
        return None
    try:
        return upload_bytes("YUCG_Outreach_Pack.xlsx", data)
    except Exception:
        return None
