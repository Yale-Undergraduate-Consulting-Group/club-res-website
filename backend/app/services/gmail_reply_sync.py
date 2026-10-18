"""
Detect replies in Gmail threads for mass-send / follow-up campaign contacts
and update replied_at + outreach pipeline. Requires gmail.readonly scope.
"""
from __future__ import annotations

import email.utils
from datetime import datetime, timezone
from typing import Any

import httpx

from app.database import get_db
from app.services.gmail_api import get_valid_access_token


def _parseaddr_email(header_val: str) -> str:
    if not header_val:
        return ""
    _, addr = email.utils.parseaddr(header_val)
    return (addr or "").strip().lower()


def _sent_at_to_ms(sent_at: Any) -> int:
    if sent_at is None:
        return 0
    if isinstance(sent_at, (int, float)):
        v = float(sent_at)
        return int(v * 1000) if v < 1e12 else int(v)
    try:
        s = str(sent_at).replace("Z", "+00:00")
        if " " in s and "+" not in s and "T" not in s:
            dt = datetime.strptime(s.split(".")[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _headers_dict(msg: dict) -> dict[str, str]:
    headers: dict[str, str] = {}
    payload = msg.get("payload") or {}
    for h in payload.get("headers") or []:
        name = (h.get("name") or "").lower()
        if name:
            headers[name] = h.get("value") or ""
    return headers


async def _fetch_thread(access_token: str, thread_id: str) -> dict | None:
    params = [
        ("format", "metadata"),
        ("metadataHeaders", "From"),
        ("metadataHeaders", "To"),
        ("metadataHeaders", "Subject"),
        ("metadataHeaders", "Date"),
    ]
    async with httpx.AsyncClient(timeout=25.0) as client:
        r = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if r.status_code == 404:
            return None
        if r.status_code == 403:
            raise PermissionError(
                "Gmail denied thread access. Sign out and sign in again to grant inbox read (gmail.readonly)."
            )
        if r.status_code == 401:
            raise PermissionError("Gmail access expired. Sign out and sign in again.")
        if r.status_code >= 400:
            raise RuntimeError(f"Gmail thread error: {r.status_code} - {r.text}")
        return r.json()


def _thread_has_inbound_reply_from_contact(
    thread: dict,
    contact_email: str,
    owner_email: str,
    _stored_message_id: str | None,
    sent_at_ms: int,
) -> bool:
    contact_email = (contact_email or "").strip().lower()
    owner_email = (owner_email or "").strip().lower()
    if not contact_email or not thread:
        return False

    messages = list(thread.get("messages") or [])
    if not messages:
        return False

    def msg_time(m: dict) -> int:
        try:
            return int(m.get("internalDate") or 0)
        except (TypeError, ValueError):
            return 0

    messages.sort(key=msg_time)
    # Anchor to the tracked send, never the newest reminder in this thread.
    last_our_ms = max(0, sent_at_ms - 1)
    for m in messages:
        if _stored_message_id and m.get("id") == _stored_message_id:
            last_our_ms = msg_time(m)
            break

    for m in messages:
        hdr = _headers_dict(m)
        from_e = _parseaddr_email(hdr.get("from", ""))
        if from_e != contact_email:
            continue
        if msg_time(m) > last_our_ms:
            return True
    return False


_BOUNCE_FROM_MARKERS = ("mailer-daemon", "postmaster", "mail-daemon")
_BOUNCE_SUBJECT_MARKERS = (
    "undeliverable",
    "delivery status notification",
    "mail delivery failed",
    "returned mail",
    "delivery failure",
    "failure notice",
)


def thread_has_bounce(thread: dict) -> bool:
    """DSN / Gmail bounce in a thread we sent. Does not prove the inbox existed."""
    if not thread:
        return False
    for m in thread.get("messages") or []:
        hdr = _headers_dict(m)
        from_e = _parseaddr_email(hdr.get("from", ""))
        subj = (hdr.get("subject") or "").lower()
        labels = [str(x).lower() for x in (m.get("labelIds") or [])]
        if any(mark in from_e for mark in _BOUNCE_FROM_MARKERS):
            return True
        if any(mark in subj for mark in _BOUNCE_SUBJECT_MARKERS):
            return True
        if "bounce" in labels:
            return True
    return False


async def _record_pattern_outcome(db, contact_id: int, delivered: bool) -> None:
    """Let a real delivery outcome correct the format it was derived from."""
    from app.services.company_email_cache import record_send_outcome

    row = await (await db.execute(
        "SELECT email, name FROM contacts WHERE id = ?", (contact_id,)
    )).fetchone()
    if not row or not row["email"] or not row["name"]:
        return
    await record_send_outcome(
        db,
        email=row["email"],
        full_name=row["name"],
        delivered=delivered,
        source="gmail_bounce" if not delivered else "gmail_reply",
    )


async def _mark_campaign_contact_bounced(db, cc_id: int, contact_id: int) -> None:
    await db.execute(
        """UPDATE campaign_contacts SET status = 'bounced' WHERE id = ?""",
        (cc_id,),
    )
    await db.execute(
        """UPDATE contacts SET email_verification_status = 'dead'
           WHERE id = ?""",
        (contact_id,),
    )
    await _record_pattern_outcome(db, contact_id, delivered=False)


async def _mark_campaign_contact_replied(db, cc_id: int, contact_id: int) -> None:
    await db.execute(
        """UPDATE campaign_contacts SET replied_at = CURRENT_TIMESTAMP, status = 'replied' WHERE id = ?""",
        (cc_id,),
    )
    await db.execute(
        """UPDATE contacts SET pipeline_status = 'replied' WHERE id = ?
           AND (pipeline_status IS NULL OR pipeline_status NOT IN ('meeting', 'closed'))""",
        (contact_id,),
    )
    await _record_pattern_outcome(db, contact_id, delivered=True)


async def apply_contacted_auto_sort(db, sent_by_user_id: int) -> int:
    """Move contacts from cold → contacted when this user sent mail but there's no reply yet."""
    cursor = await db.execute(
        """
        UPDATE contacts SET pipeline_status = 'contacted'
        WHERE (pipeline_status IS NULL OR pipeline_status = 'cold')
          AND id IN (
            SELECT DISTINCT cc.contact_id FROM campaign_contacts cc
            WHERE cc.status = 'sent' AND cc.replied_at IS NULL AND cc.sent_by_user_id = ?
          )
        """,
        (sent_by_user_id,),
    )
    await db.commit()
    return cursor.rowcount if cursor.rowcount is not None else 0


async def sync_replies_for_user(user_id: int, *, auto_sort_contacted: bool = True) -> dict[str, Any]:
    from app.services.message_tracking import sync_sender
    return await sync_sender(user_id, auto_sort_contacted=auto_sort_contacted)


async def sync_replies_all_senders() -> dict[str, Any]:
    db = await get_db()
    try:
        rows = await (await db.execute(
            "SELECT DISTINCT sent_by_user_id FROM campaign_contacts WHERE sent_by_user_id IS NOT NULL AND sent_at IS NOT NULL"
        )).fetchall()
    finally:
        await db.close()
    results = [await sync_replies_for_user(r["sent_by_user_id"]) for r in rows]
    return {"ok": all(r.get("ok") for r in results), "users_processed": len(results),
            "marked_replied": sum(r.get("marked_replied", 0) for r in results),
            "marked_bounced": sum(r.get("marked_bounced", 0) for r in results)}
