"""
Tracking API - Open tracking pixel for campaign emails.
No auth required (loaded by recipient's email client).
"""
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit
from fastapi import APIRouter
from fastapi.responses import Response
from app.database import get_db

router = APIRouter()

# 1x1 transparent GIF
TRACKING_PIXEL = bytes([
    0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00, 0x01, 0x00, 0x80, 0x00, 0x00,
    0xff, 0xff, 0xff, 0x00, 0x00, 0x00, 0x21, 0xf9, 0x04, 0x01, 0x00, 0x00, 0x00,
    0x00, 0x2c, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0x02, 0x02,
    0x44, 0x01, 0x00, 0x3b
])


@router.get("/open/{campaign_contact_id}")
async def track_open(campaign_contact_id: int):
    """Tracking pixel - records open when recipient loads images. Returns 1x1 transparent GIF."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE campaign_contacts SET opened_at = CURRENT_TIMESTAMP WHERE id = ? AND opened_at IS NULL",
            (campaign_contact_id,),
        )
        await db.commit()
    except Exception:
        pass
    finally:
        await db.close()
    return pixel_response()


def pixel_response():
    return Response(content=TRACKING_PIXEL, media_type="image/gif", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache",
    })


@router.get("/message/{token}")
async def track_message_open(token: str):
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, campaign_contact_id FROM outreach_messages WHERE tracking_token = ?", (token,)
        )).fetchone()
        if row:
            await db.execute(
                """INSERT OR IGNORE INTO outreach_events(message_id, kind, source_id, occurred_at)
                   VALUES (?, 'opened', 'pixel:first', ?)""",
                (row["id"], datetime.now(timezone.utc).isoformat()),
            )
            await db.execute(
                "UPDATE campaign_contacts SET opened_at = COALESCE(opened_at, CURRENT_TIMESTAMP) WHERE id = ?",
                (row["campaign_contact_id"],),
            )
            await db.commit()
    finally:
        await db.close()
    return pixel_response()


def get_tracking_pixel_url(campaign_contact_id: int | str) -> str:
    """Build tracking pixel URL for injection into emails."""
    base = (os.getenv("API_BASE_URL") or os.getenv("BACKEND_URL") or "http://localhost:8000").strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Tracking requires a valid public API_BASE_URL or BACKEND_URL")
    route = "open" if isinstance(campaign_contact_id, int) else "message"
    return f"{base}/api/track/{route}/{campaign_contact_id}"
