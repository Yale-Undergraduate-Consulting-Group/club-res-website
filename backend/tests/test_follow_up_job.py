"""Follow-up job: member cap, which campaigns and threads keep following up, out of office, send window."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "follow-up-job-secret-xxxxxxxxxxxxxxx")

from app.database import get_db, init_db
from app.services.dispatch_service import snapshot
from app.services.follow_up_job import run_follow_up_sequences
from app.services.message_tracking import record_event
from app.services.settings_service import set_member_setting


async def _seed_base(db) -> None:
    await db.execute("INSERT INTO users(id,email,name,role,is_active) VALUES (1,'me@yale.edu','Me','standard',1)")
    await db.execute("INSERT INTO follow_up_sequences(id,name,user_id) VALUES (1,'Two-step',1)")
    await db.execute("INSERT INTO follow_up_steps(sequence_id,days_after,subject,body,step_order) VALUES (1,3,'Following up','b',0)")
    await db.commit()


async def _campaign(db, campaign_id: int, status: str) -> None:
    await db.execute(
        "INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id,sequence_id) VALUES (?,?,?,1,1,1)",
        (campaign_id, f"Campaign {campaign_id}", status))
    await db.commit()


async def _due_contact(db, n: int, campaign_id: int = 1, company: str = "Acme", host: str = "acme.com") -> int:
    """A mailed contact whose step 0 follow-up was due a week ago. Returns the campaign_contact id."""
    await db.execute("INSERT INTO contacts(id,name,email,company) VALUES (?,?,?,?)", (n, f"P{n}", f"p{n}@{host}", company))
    await db.execute(
        """INSERT INTO campaign_contacts(id,campaign_id,contact_id,email_subject,email_body,status,sent_at,
           last_sequence_sent_at,sequence_step_sent,sent_by_user_id)
           VALUES (?,?,?,'s','b','sent',datetime('now','-10 days'),datetime('now','-10 days'),0,1)""",
        (n, campaign_id, n))
    await snapshot(db, f"initial:{n}", n, 1, f"p{n}@acme.com", "s", "b")
    await db.execute("UPDATE outreach_dispatches SET state='sent' WHERE dispatch_key=?", (f"initial:{n}",))
    await snapshot(db, f"followup:{n}:0", n, 1, f"p{n}@acme.com", "Following up", "b", 3)
    await db.execute(
        """INSERT INTO outreach_messages(campaign_contact_id,sender_id,recipient,tracking_token,rfc_message_id,sent_at)
           VALUES (?,1,?,?,?,datetime('now','-10 days'))""",
        (n, f"p{n}@acme.com", f"tok{n}", f"<m{n}@yale.edu>"))
    await db.commit()
    return n


async def _event(db, cc_id: int, kind: str, occurred_sql: str = "datetime('now')") -> None:
    occurred = (await (await db.execute(f"SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', {occurred_sql}) AS t")).fetchone())["t"]
    message = await (await db.execute("SELECT * FROM outreach_messages WHERE campaign_contact_id=?", (cc_id,))).fetchone()
    await record_event(db, dict(message), kind, f"{kind}-{cc_id}", occurred, "")
    await db.commit()


async def _state(db, cc_id: int) -> str:
    return (await (await db.execute(
        "SELECT state FROM outreach_dispatches WHERE dispatch_key=?", (f"followup:{cc_id}:0",))).fetchone())["state"]


async def _run(window_open: MagicMock) -> dict:
    send = AsyncMock(return_value={"message_id": "m", "thread_id": "t"})
    with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", send), \
         patch("app.services.send_window.open_now", window_open):
        result = await run_follow_up_sequences()
    result["calls"] = send.await_count
    return result


async def scenario() -> None:
    open_window = MagicMock(return_value=True)

    # Member cap: the sender's own limit, not only the club environment default, applies to follow-ups.
    db = await get_db()
    await _seed_base(db)
    await _campaign(db, 1, "sent")
    first, second = await _due_contact(db, 1), await _due_contact(db, 2)
    await set_member_setting(1, "daily_send_limit", "1")
    result = await _run(open_window)
    assert result["sent"] == 1, result
    assert {await _state(db, first), await _state(db, second)} == {"sent", "ready"}, "the second follow-up must wait for tomorrow"
    assert (await _run(open_window))["sent"] == 0, "the cap holds on the next run the same day"
    await set_member_setting(1, "daily_send_limit", None)
    assert (await _run(open_window))["sent"] == 1, "raising the limit releases the held follow-up"
    await db.close()


async def scenario_campaign_states() -> None:
    open_window = MagicMock(return_value=True)
    db = await get_db()
    await _seed_base(db)
    ids = {}
    for number, status in enumerate(["needs_attention", "releasing", "paused"], start=1):
        await _campaign(db, number, status)
        ids[status] = await _due_contact(db, number, number)
    result = await _run(open_window)
    assert result["sent"] == 2, result
    assert await _state(db, ids["needs_attention"]) == "sent", "a gate hold on unsent rows must not stop follow-ups"
    assert await _state(db, ids["releasing"]) == "sent"
    assert await _state(db, ids["paused"]) == "ready", "a paused campaign sends no follow-ups"
    await db.close()


async def scenario_stops_and_out_of_office() -> None:
    open_window = MagicMock(return_value=True)
    db = await get_db()
    await _seed_base(db)
    await _campaign(db, 1, "sent")
    # A reply stops everyone at its company, so the replied contact sits at a company of its own.
    bounced, bounced_event_only, fresh_auto, old_auto, clean = [await _due_contact(db, n) for n in (1, 2, 4, 5, 6)]
    replied = await _due_contact(db, 3, company="Globex", host="globex.com")
    await _event(db, bounced, "bounced")  # also sets campaign_contacts.status
    await db.execute(
        "INSERT INTO outreach_events(message_id,kind,source_id,occurred_at) "
        "SELECT id,'bounced','dsn',datetime('now') FROM outreach_messages WHERE campaign_contact_id=?", (bounced_event_only,))
    await _event(db, replied, "replied")
    await _event(db, fresh_auto, "auto_reply")  # out of office arrived just now
    await _event(db, old_auto, "auto_reply", "datetime('now','-9 days')")  # absence notice long past
    result = await _run(open_window)
    states = {n: await _state(db, n) for n in (bounced, bounced_event_only, replied, fresh_auto, old_auto, clean)}
    assert states == {
        bounced: "ready", bounced_event_only: "ready", replied: "ready",
        fresh_auto: "ready", old_auto: "sent", clean: "sent",
    }, states
    assert result["sent"] == 2, result
    await db.close()


async def scenario_window() -> None:
    db = await get_db()
    await _seed_base(db)
    await _campaign(db, 1, "sent")
    cc = await _due_contact(db, 1)
    closed = MagicMock(return_value=False)
    result = await _run(closed)
    assert (result["sent"], result["waiting"], result["calls"]) == (0, 1, 0), result
    assert await _state(db, cc) == "ready", "a step outside the window stays queued, untouched"
    result = await _run(MagicMock(return_value=True))
    assert (result["sent"], result["waiting"]) == (1, 0), result
    assert await _state(db, cc) == "sent"
    await db.close()


def tests() -> None:
    for name, run in [("cap", scenario), ("states", scenario_campaign_states),
                      ("stops", scenario_stops_and_out_of_office), ("window", scenario_window)]:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/followup.db"

            async def go() -> None:
                await init_db()
                await run()

            asyncio.run(go())
            print(f"  {name}: ok")


if __name__ == "__main__":
    tests()
    print("follow-up job: ok")
