"""Immediate, paged, local-window, and first-mailbox campaign drains."""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "send-path-test-secret-not-a-known-default-x"
os.environ["CAMPAIGN_SEND_DELAY_SEC"] = "0"

from app.database import get_db, init_db
from app.routers import campaigns
from app.services.contact_intelligence import Mailbox
from app.services.dispatch_service import snapshot
from app.services.register_names import derive


async def _campaign(email: str, *, company: str = "Example", source: str = "web_discovery") -> tuple[int, int, int]:
    db = await get_db()
    try:
        contact = await db.execute(
            "INSERT INTO contacts(name,email,company,contact_source) VALUES('Person',?,?,?)",
            (email, company, source),
        )
        campaign = await db.execute(
            "INSERT INTO campaigns(name,status,owner_user_id,sender_user_id) VALUES('Test','draft',1,1)"
        )
        cc = await db.execute(
            """INSERT INTO campaign_contacts(campaign_id,contact_id,email_subject,email_body,status)
               VALUES(?,?, 'Hello','Body','pending')""",
            (campaign.lastrowid, contact.lastrowid),
        )
        await db.commit()
        return int(campaign.lastrowid), int(contact.lastrowid), int(cc.lastrowid)
    finally:
        await db.close()


async def _release_without_kick(campaign_id: int) -> None:
    old = os.environ.get("APP_ENV")
    os.environ["APP_ENV"] = "beta"
    try:
        await campaigns.release_campaign(campaign_id, {"id": 1})
    finally:
        if old is None:
            os.environ.pop("APP_ENV", None)
        else:
            os.environ["APP_ENV"] = old


async def run() -> None:
    await init_db()
    db = await get_db()
    await db.execute("INSERT INTO users(id,email,name,role) VALUES(1,'sender@yale.edu','Sender','admin')")
    await db.commit()
    await db.close()

    # Release schedules the same durable drain immediately; no cron tick needed.
    os.environ["APP_ENV"] = "production"
    os.environ["SEND_WINDOW_HOURS"] = "off"
    os.environ["EXTERNAL_EMAIL_VERIFICATION_ENABLED"] = "false"
    immediate_id, _, immediate_cc = await _campaign("immediate@quick.example")
    send = AsyncMock(return_value={"message_id": "m-immediate", "thread_id": "t-immediate"})
    scheduled = []
    real_create_task = asyncio.create_task

    def capture(coro):
        task = real_create_task(coro)
        scheduled.append(task)
        return task

    with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", send), \
         patch.object(campaigns.asyncio, "create_task", side_effect=capture):
        await campaigns.release_campaign(immediate_id, {"id": 1})
        assert len(scheduled) == 1
        await scheduled[0]
    assert send.await_count == 1
    db = await get_db()
    state = await (await db.execute("SELECT status FROM campaign_contacts WHERE id=?", (immediate_cc,))).fetchone()
    await db.close()
    assert state["status"] == "sent"

    # A full held page cannot hide a sendable row later in the queue.
    scan_id, _, _ = await _campaign("held0@scan.example")
    db = await get_db()
    for i in range(1, 60):
        contact = await db.execute(
            "INSERT INTO contacts(name,email,company,contact_source) VALUES(?,?, 'Scan','web_discovery')",
            (f"Held {i}", f"held{i}@scan.example"),
        )
        await db.execute(
            """INSERT INTO campaign_contacts(campaign_id,contact_id,email_subject,email_body,status)
               VALUES(?,?, 'Hello','Body','pending')""",
            (scan_id, contact.lastrowid),
        )
    await db.commit()
    await db.close()
    await _release_without_kick(scan_id)

    async def hold_first_page(_db, _campaign, rows, _sender, probing=None):
        allowed = [row for row in rows if row["recipient_email"] == "held50@scan.example"]
        held = [{"row": row, "host": "scan.example", "reason": "test_hold"}
                for row in rows if not allowed]
        return allowed, held

    db = await get_db()
    with patch("app.services.send_gating.select_sendable", hold_first_page):
        claimed, held = await campaigns._claim_pending_rows(db, scan_id, 1, 1)
    await db.close()
    assert len(claimed) == 1 and claimed[0]["email"] == "held50@scan.example", claimed
    assert len(held) >= 50

    # California recipients wait outside their window, then send when it opens.
    window_id, _, window_cc = await _campaign("window@west.example", company="West Company")
    db = await get_db()
    entity = derive("West Company", "US")
    await db.execute(
        """INSERT INTO company_register(source,source_key,tier,country,company_name,region,entity_key)
           VALUES('test','west','us_private','US','West Company','CA',?)""",
        (entity.entity_key,),
    )
    await db.commit()
    await db.close()
    await _release_without_kick(window_id)
    os.environ["SEND_WINDOW_HOURS"] = "8-17"
    window_send = AsyncMock(return_value={"message_id": "m-window", "thread_id": "t-window"})
    with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", window_send):
        held_result = await campaigns.drain_campaign(
            window_id, 1, now=datetime(2026, 11, 7, 18, tzinfo=timezone.utc),
        )
        assert held_result["sent"] == 0 and held_result["held"] == 1, held_result
        open_result = await campaigns.drain_campaign(
            window_id, 1, now=datetime(2026, 11, 9, 17, tzinfo=timezone.utc),
        )
    assert open_result["sent"] == 1 and window_send.await_count == 1, open_result
    db = await get_db()
    state = await (await db.execute("SELECT status FROM campaign_contacts WHERE id=?", (window_cc,))).fetchone()
    await db.close()
    assert state["status"] == "sent"

    # Verifalia rejects a web-only probe before Gmail and records the deadbox.
    os.environ["SEND_WINDOW_HOURS"] = "off"
    os.environ["INBOX_VERIFY_PROVIDER"] = "verifalia"
    os.environ["EXTERNAL_EMAIL_VERIFICATION_ENABLED"] = "true"
    os.environ["VERIFALIA_API_KEY"] = "test-key"
    dead_id, dead_contact, dead_cc = await _campaign("dead@unknown-one.example")
    await _release_without_kick(dead_id)
    rejected = AsyncMock(return_value={"mailbox": Mailbox.RECIPIENT_REJECTED.value})
    gmail = AsyncMock(return_value={"message_id": "never", "thread_id": "never"})
    with patch("app.services.contact_intelligence.assess_address", rejected), \
         patch("app.services.gmail_api.send_via_gmail_api_with_tracking", gmail):
        dead_result = await campaigns.drain_campaign(dead_id, 1)
    gmail.assert_not_awaited()
    assert dead_result["failed"] == 1
    db = await get_db()
    failed = await (await db.execute(
        """SELECT cc.status,cc.last_error,c.email_verification_status
             FROM campaign_contacts cc JOIN contacts c ON c.id=cc.contact_id WHERE cc.id=?""",
        (dead_cc,),
    )).fetchone()
    await db.close()
    assert failed["status"] == "failed"
    assert failed["last_error"] == "Mailbox does not exist (Verifalia)"
    assert failed["email_verification_status"] == "dead"

    # A valid result sends; disabling Verifalia restores the prior no-check path.
    valid_id, _, _ = await _campaign("valid@unknown-two.example")
    await _release_without_kick(valid_id)
    valid = AsyncMock(return_value={"mailbox": Mailbox.PROVIDER_HIGH_CONFIDENCE.value})
    gmail = AsyncMock(return_value={"message_id": "m-valid", "thread_id": "t-valid"})
    with patch("app.services.contact_intelligence.assess_address", valid), \
         patch("app.services.gmail_api.send_via_gmail_api_with_tracking", gmail):
        valid_result = await campaigns.drain_campaign(valid_id, 1)
    assert valid_result["sent"] == 1 and valid.await_count == 1 and gmail.await_count == 1

    disabled_id, _, _ = await _campaign("disabled@unknown-three.example")
    await _release_without_kick(disabled_id)
    os.environ["EXTERNAL_EMAIL_VERIFICATION_ENABLED"] = "false"
    no_check = AsyncMock()
    gmail = AsyncMock(return_value={"message_id": "m-disabled", "thread_id": "t-disabled"})
    with patch("app.services.contact_intelligence.assess_address", no_check), \
         patch("app.services.gmail_api.send_via_gmail_api_with_tracking", gmail):
        disabled_result = await campaigns.drain_campaign(disabled_id, 1)
    assert disabled_result["sent"] == 1 and gmail.await_count == 1
    no_check.assert_not_awaited()


if __name__ == "__main__":
    asyncio.run(run())
    print("ok")
    Path(_tmp.name).unlink(missing_ok=True)
    Path(_tmp.name + "-wal").unlink(missing_ok=True)
    Path(_tmp.name + "-shm").unlink(missing_ok=True)
