"""Campaign state, account draft, and contact pagination checks. From backend/: python3 tests/test_campaign_states.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "campaign-state-test-secret-not-a-known-default"
os.environ["CAMPAIGN_SEND_DELAY_SEC"] = "0"

from app.database import get_db, init_db  # noqa: E402
from app.routers.campaigns import (  # noqa: E402
    _campaign_readiness,
    drain_campaign,
    release_campaign,
    resume_campaign,
    retry_failed_campaign_contacts,
)
from app.routers.contacts import list_contacts  # noqa: E402
from app.routers.emails import (  # noqa: E402
    DraftSaveRequest,
    DraftUpdateRequest,
    delete_generated_email_draft,
    list_generated_emails,
    save_generated_email_draft,
    update_generated_email_draft,
)

USER = {"id": 1, "email": "sender@yale.edu", "role": "standard"}


async def _run() -> None:
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role) VALUES (1, 'sender@yale.edu', 'standard')")
        for index in range(4):
            await db.execute(
                "INSERT INTO contacts (name, email) VALUES (?, ?)",
                (f"Person {index}", f"person{index}@example.com"),
            )
        campaign_cursor = await db.execute("INSERT INTO campaigns (name, status, owner_user_id, sender_user_id) VALUES ('State check', 'draft', 1, 1)")
        campaign_id = int(campaign_cursor.lastrowid)
        await db.execute(
            """INSERT INTO campaign_contacts
               (campaign_id, contact_id, email_subject, email_body, status)
               VALUES (?, 1, 'Subject', '', 'pending')""",
            (campaign_id,),
        )
        await db.commit()
    finally:
        await db.close()

    db = await get_db()
    try:
        readiness = await _campaign_readiness(db, campaign_id)
    finally:
        await db.close()
    assert readiness["ready"] is False
    assert readiness["issues"]
    try:
        await release_campaign(campaign_id, USER)
        raise AssertionError("incomplete campaign released")
    except HTTPException as exc:
        assert exc.status_code == 409

    db = await get_db()
    try:
        await db.execute(
            "UPDATE campaign_contacts SET email_body = 'Body' WHERE campaign_id = ?",
            (campaign_id,),
        )
        await db.commit()
    finally:
        await db.close()

    released = await release_campaign(campaign_id, USER)
    assert released["status"] == "releasing"

    import app.services.gmail_api as gmail_api

    async def fail_send(**_kwargs):
        raise RuntimeError("mailbox rejected request")

    gmail_api.send_via_gmail_api_with_tracking = fail_send
    failed = await drain_campaign(campaign_id, USER["id"], limit=5)
    assert failed["status"] == "needs_attention"
    assert failed["failed"] == 1

    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT status, last_error FROM campaign_contacts WHERE campaign_id = ?",
            (campaign_id,),
        )).fetchone()
        assert row["status"] == "failed"
        assert "mailbox rejected" in row["last_error"]
    finally:
        await db.close()

    retried = await retry_failed_campaign_contacts(campaign_id, USER)
    # An exception after invoking Gmail does not prove non-delivery. Never blindly retry.
    assert retried == {"ok": True, "retried": 0, "status": "paused"}
    try:
        await resume_campaign(campaign_id, USER)
        raise AssertionError("Ambiguous delivery resumed")
    except HTTPException as exc:
        assert exc.status_code == 409

    saved = await save_generated_email_draft(
        DraftSaveRequest(contact_id=1, subject="Draft subject", body="Draft body"),
        USER,
    )
    draft_id = saved["id"]
    await update_generated_email_draft(
        draft_id,
        DraftUpdateRequest(subject="Updated subject", body="Updated body"),
        USER,
    )
    drafts = await list_generated_emails(contact_id=1, sort="created_desc", user=USER)
    assert drafts[0]["subject"] == "Updated subject"
    await delete_generated_email_draft(draft_id, USER)
    assert await list_generated_emails(contact_id=1, sort="created_desc", user=USER) == []

    page = await list_contacts(limit=2, offset=1, user=USER)
    assert page["total"] == 4
    assert page["limit"] == 2
    assert page["offset"] == 1
    assert len(page["items"]) == 2


def test_campaign_states_drafts_and_pagination() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    try:
        test_campaign_states_drafts_and_pagination()
        print("ok")
    finally:
        Path(_tmp.name).unlink(missing_ok=True)
        Path(_tmp.name + "-wal").unlink(missing_ok=True)
        Path(_tmp.name + "-shm").unlink(missing_ok=True)
