"""Durable outbound intent. A claimed send is never automatically retried.

Gmail has no idempotent send key: a timeout or process death can follow acceptance.
Such work stays claimed/ambiguous for reconciliation, avoiding duplicate delivery.
"""
import os

from fastapi import HTTPException
from app.database import is_postgres


async def begin_write(db):
    if is_postgres():
        raise HTTPException(503, "Outbound dispatch requires a verified transactional database adapter")
    await db.execute("BEGIN IMMEDIATE")


async def snapshot(db, key, cc_id, sender, recipient, subject, body, delay_days=0):
    """Freeze exactly what will be sent. The sign-off is not part of the
    snapshot: it is a current fact about the sender, rendered at send time."""
    cursor = await db.execute(
        """INSERT INTO outreach_dispatches
        (dispatch_key,campaign_contact_id,sender_user_id,recipient,subject,body,delay_days)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(dispatch_key) DO NOTHING""",
        (key, cc_id, sender, recipient, subject, body, delay_days),
    )
    return cursor.rowcount == 1


async def snapshot_many(db, rows) -> set[str]:
    """Freeze a whole release in one statement.

    Releasing used to insert one row per recipient inside the write
    transaction, which holds the shared SQLite file for the length of the
    loop. Returns the keys that were newly frozen; keys already present are
    left exactly as they were claimed.
    """
    rows = list(rows)
    if not rows:
        return set()
    keys = [row[0] for row in rows]
    existing = {
        r["dispatch_key"]
        for chunk in (keys[i:i + 400] for i in range(0, len(keys), 400))
        for r in await (await db.execute(
            f"SELECT dispatch_key FROM outreach_dispatches WHERE dispatch_key IN ({','.join('?' * len(chunk))})",
            chunk,
        )).fetchall()
    }
    fresh = [row for row in rows if row[0] not in existing]
    if fresh:
        await db.executemany(
            """INSERT INTO outreach_dispatches
            (dispatch_key,campaign_contact_id,sender_user_id,recipient,subject,body,delay_days)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(dispatch_key) DO NOTHING""",
            fresh,
        )
    return {row[0] for row in fresh}


async def claim(db, key, sender):
    """Caller holds write transaction; conditional update is durable before send."""
    daily_limit = max(1, int(os.getenv("CAMPAIGN_DAILY_SEND_LIMIT", "100") or 100))
    used = await (await db.execute(
        """SELECT COUNT(*) AS n FROM outreach_dispatches
           WHERE sender_user_id=? AND claimed_at IS NOT NULL
             AND date(claimed_at)=date('now')""",
        (sender,),
    )).fetchone()
    if int(used["n"] or 0) >= daily_limit:
        return None
    cursor = await db.execute(
        """UPDATE outreach_dispatches SET state='claimed', claimed_at=CURRENT_TIMESTAMP
        WHERE dispatch_key=? AND sender_user_id=? AND state='ready' RETURNING *""",
        (key, sender),
    )
    return await cursor.fetchone()


async def finish(db, key, meta=None, error=None, *, safe_to_retry=False):
    await db.execute(
        """UPDATE outreach_dispatches SET state=?,
        completed_at=CASE WHEN ? THEN NULL ELSE CURRENT_TIMESTAMP END,
        gmail_message_id=?,last_error=?,claimed_at=CASE WHEN ? THEN NULL ELSE claimed_at END
        WHERE dispatch_key=? AND state='claimed'""",
        ("ready" if safe_to_retry else "ambiguous" if error else "sent", bool(safe_to_retry), (meta or {}).get("message_id"),
         str(error)[:1000] if error else None, bool(safe_to_retry), key),
    )
