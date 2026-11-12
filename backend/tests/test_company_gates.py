"""One reply anywhere at a company stops the club writing to it; the club caps first touches per company.

Both rules are club-wide: they must see other members' work, match a company by
domain or by name alone, leave unrelated companies untouched, and hold under
racing drains.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "company-gates-secret-xxxxxxxxxxxxxxxx")
os.environ["SEND_WINDOW_HOURS"] = "off"

from app.database import get_db, init_db
from app.routers.campaigns import _claim_pending_rows
from app.services.dispatch_service import snapshot
from app.services.follow_up_job import run_follow_up_sequences
from app.services.message_tracking import record_event
from app.services.send_gating import describe_hold


async def _base(db) -> None:
    for uid, email in ((1, "me@yale.edu"), (2, "mate@yale.edu")):
        await db.execute("INSERT INTO users(id,email,name,role,is_active) VALUES (?,?,?,'standard',1)", (uid, email, f"User {uid}"))
    await db.execute("INSERT INTO follow_up_sequences(id,name,user_id) VALUES (1,'Two-step',1)")
    await db.execute("INSERT INTO follow_up_steps(sequence_id,days_after,subject,body,step_order) VALUES (1,3,'Following up','b',0)")
    await db.execute("INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id,sequence_id) VALUES (1,'Mine','sent',1,1,1)")
    await db.execute("INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id) VALUES (2,'Theirs','sent',2,2)")
    await db.commit()


async def _person(db, n: int, company: str, domain: str | None, host: str) -> None:
    await db.execute(
        "INSERT INTO contacts(id,name,email,company,company_domain) VALUES (?,?,?,?,?)",
        (n, f"P{n}", f"p{n}@{host}", company, domain),
    )


async def _mailed(db, n: int, campaign: int, sender: int, *, replied: str | None = None) -> None:
    """A person already written to; ``replied`` is 'mark' (row) or 'event' (mailbox event only)."""
    await db.execute(
        """INSERT INTO campaign_contacts(id,campaign_id,contact_id,email_subject,email_body,status,sent_at,
           last_sequence_sent_at,sequence_step_sent,sent_by_user_id)
           VALUES (?,?,?,'s','b','sent',datetime('now','-10 days'),datetime('now','-10 days'),0,?)""",
        (n, campaign, n, sender),
    )
    email = (await (await db.execute("SELECT email FROM contacts WHERE id=?", (n,))).fetchone())["email"]
    await snapshot(db, f"initial:{n}", n, sender, email, "s", "b")
    await db.execute("UPDATE outreach_dispatches SET state='sent', claimed_at=datetime('now','-10 days') WHERE dispatch_key=?", (f"initial:{n}",))
    await snapshot(db, f"followup:{n}:0", n, sender, email, "Following up", "b", 3)
    await db.execute(
        """INSERT INTO outreach_messages(campaign_contact_id,sender_id,recipient,tracking_token,rfc_message_id,sent_at)
           VALUES (?,?,?,?,?,datetime('now','-10 days'))""",
        (n, sender, email, f"tok{n}", f"<m{n}@yale.edu>"),
    )
    if replied == "mark":
        await db.execute("UPDATE campaign_contacts SET status='replied', replied_at=datetime('now') WHERE id=?", (n,))
    await db.commit()
    if replied == "event":
        message = await (await db.execute("SELECT * FROM outreach_messages WHERE campaign_contact_id=?", (n,))).fetchone()
        stamp = (await (await db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00','now') AS t")).fetchone())["t"]
        await record_event(db, dict(message), "replied", f"r{n}", stamp, "")
        # Simulate a sync that recorded only the event, not the row.
        await db.execute("UPDATE campaign_contacts SET status='sent', replied_at=NULL WHERE id=?", (n,))
        await db.commit()


async def _follow_up_state(db, n: int) -> str:
    return (await (await db.execute("SELECT state FROM outreach_dispatches WHERE dispatch_key=?", (f"followup:{n}:0",))).fetchone())["state"]


async def _run_follow_ups() -> None:
    send = AsyncMock(return_value={"message_id": "m", "thread_id": "t"})
    with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", send), \
         patch("app.services.send_window.open_now", MagicMock(return_value=True)):
        await run_follow_up_sequences()


async def follow_ups_stop_when_a_teammate_has_a_reply() -> None:
    db = await get_db()
    await _base(db)
    # Domain match: the replied contact's company is spelled differently, only the domain agrees.
    await _person(db, 1, "Acme", "acme.com", "acme.com")
    await _person(db, 2, "Acme Corporation", "acme.com", "acme.com")
    # Name-only match: no domain on either, and the addresses are on different hosts.
    await _person(db, 3, "Initech Inc", None, "initech-mail.com")
    await _person(db, 4, "The Initech Company", None, "initech-corp.net")
    # Reply seen only as a mailbox event.
    await _person(db, 5, "Hooli", "hooli.com", "hooli.com")
    await _person(db, 6, "Hooli", "hooli.com", "hooli.com")
    # Unrelated company: nothing replied.
    await _person(db, 7, "Globex", "globex.com", "globex.com")
    await _mailed(db, 1, 1, 1)
    await _mailed(db, 2, 2, 2, replied="mark")
    await _mailed(db, 3, 1, 1)
    await _mailed(db, 4, 2, 2, replied="mark")
    await _mailed(db, 5, 1, 1)
    await _mailed(db, 6, 2, 2, replied="event")
    await _mailed(db, 7, 1, 1)
    await _run_follow_ups()
    assert await _follow_up_state(db, 1) == "ready", "a teammate's reply at the same domain stops my follow-up"
    assert await _follow_up_state(db, 3) == "ready", "a teammate's reply under the same company name stops it too"
    assert await _follow_up_state(db, 5) == "ready", "a reply known only from the mailbox event counts"
    assert await _follow_up_state(db, 7) == "sent", "an unrelated company is unaffected"
    await db.close()


async def _pending(db, n: int, campaign: int, sender: int, company: str, domain: str | None, host: str) -> None:
    await _person(db, n, company, domain, host)
    await db.execute(
        "INSERT INTO campaign_contacts(id,campaign_id,contact_id,email_subject,email_body,status) VALUES (?,?,?,'s','b','pending')",
        (n, campaign, n),
    )
    await snapshot(db, f"initial:{n}", n, sender, f"p{n}@{host}", "s", "b")
    await db.commit()


async def first_sends_are_held_after_a_reply() -> None:
    db = await get_db()
    await _base(db)
    await db.execute("UPDATE campaigns SET status='releasing' WHERE id=1")
    await _person(db, 10, "Acme", "acme.com", "acme.com")
    await _mailed(db, 10, 2, 2, replied="mark")
    await _pending(db, 11, 1, 1, "ACME Inc", None, "acme.com")
    await _pending(db, 12, 1, 1, "Globex", "globex.com", "globex.com")
    claimed, held = await _claim_pending_rows(db, 1, 10, 1)
    assert [row["id"] for row in claimed] == [12], "only the unrelated company goes"
    assert [h["reason"] for h in held] == ["company_replied"]
    assert "a teammate already has a reply from Acme" in describe_hold(held)
    status = (await (await db.execute("SELECT status FROM campaign_contacts WHERE id=11")).fetchone())["status"]
    assert status == "pending", "a held row stays queued"
    await db.close()


async def _queue(db, campaign: int, sender: int, first: int, count: int, company="Acme", domain="acme.com") -> None:
    for n in range(first, first + count):
        await _pending(db, n, campaign, sender, company, domain, domain)


async def _proven(db, hosts):
    return set(hosts)


async def _drain_ids(db, campaign: int = 1, sender: int = 1) -> tuple[list[int], list[dict]]:
    # Mailbox proof is a separate gate; here every host is already proven.
    with patch("app.services.send_gating.proven_hosts", _proven):
        claimed, held = await _claim_pending_rows(db, campaign, 10, sender)
    return [row["id"] for row in claimed], held


async def cap_allows_three_and_holds_the_fourth() -> None:
    db = await get_db()
    await _base(db)
    await db.execute("UPDATE campaigns SET status='releasing' WHERE id=1")
    # Three people already followed up at Acme must not use up the first-touch cap.
    for n in (50, 51, 52):
        await _person(db, n, "Acme", "acme.com", "acme.com")
        await _mailed(db, n, 2, 2)
        await db.execute("UPDATE outreach_dispatches SET claimed_at=NULL WHERE dispatch_key=?", (f"initial:{n}",))
        await db.execute("UPDATE outreach_dispatches SET claimed_at=datetime('now'), state='sent' WHERE dispatch_key=?", (f"followup:{n}:0",))
    await db.commit()
    await _queue(db, 1, 1, 1, 4)
    await _pending(db, 20, 1, 1, "Globex", "globex.com", "globex.com")
    ids, held = await _drain_ids(db)
    assert ids == [1, 2, 3, 20], (ids, [(h["reason"], h["row"]["id"]) for h in held])
    assert [h["row"]["id"] for h in held] == [4]
    assert held[0]["reason"] == "company_cap_reached"
    slot = (await (await db.execute("SELECT date(MIN(claimed_at),'+30 days') AS d FROM outreach_dispatches WHERE dispatch_key IN ('initial:1','initial:2','initial:3')")).fetchone())["d"]
    note = describe_hold(held)
    assert f"3 people at Acme already emailed this month; next slot {slot}" in note, note
    # Window expiry frees exactly one slot.
    await db.execute("UPDATE outreach_dispatches SET claimed_at=datetime('now','-31 days') WHERE dispatch_key='initial:1'")
    await db.commit()
    await _queue(db, 1, 1, 5, 1)
    ids, held = await _drain_ids(db)
    assert ids == [4], "the oldest rolling out of the window frees one slot, first in line"
    assert [h["row"]["id"] for h in held] == [5]
    await db.close()


async def cap_zero_disables() -> None:
    db = await get_db()
    await _base(db)
    await db.execute("UPDATE campaigns SET status='releasing' WHERE id=1")
    await _queue(db, 1, 1, 1, 6)
    with patch.dict(os.environ, {"COMPANY_SEND_CAP": "0"}):
        ids, held = await _drain_ids(db)
    assert ids == [1, 2, 3, 4, 5, 6] and not held
    await db.close()


async def racing_claims_cannot_exceed_the_cap() -> None:
    db = await get_db()
    await _base(db)
    await db.execute("UPDATE campaigns SET status='releasing'")
    await _queue(db, 1, 1, 1, 3)
    await _queue(db, 2, 2, 11, 3)
    await db.close()

    async def drain(campaign: int, sender: int) -> list[int]:
        own = await get_db()
        try:
            with patch("app.services.send_gating.proven_hosts", _proven):
                claimed, _ = await _claim_pending_rows(own, campaign, 10, sender)
            return [row["id"] for row in claimed]
        finally:
            await own.close()

    mine, theirs = await asyncio.gather(drain(1, 1), drain(2, 2))
    assert len(mine) + len(theirs) == 3, (mine, theirs)
    check = await get_db()
    claimed = (await (await check.execute(
        "SELECT COUNT(*) AS n FROM outreach_dispatches WHERE dispatch_key LIKE 'initial:%' AND claimed_at IS NOT NULL"
    )).fetchone())["n"]
    assert claimed == 3
    await check.close()


def tests() -> None:
    for name, run in [
        ("reply stops follow-ups", follow_ups_stop_when_a_teammate_has_a_reply),
        ("reply holds first sends", first_sends_are_held_after_a_reply),
        ("cap and window", cap_allows_three_and_holds_the_fourth),
        ("cap off", cap_zero_disables),
        ("racing claims", racing_claims_cannot_exceed_the_cap),
    ]:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/gates.db"

            async def go() -> None:
                await init_db()
                await run()

            asyncio.run(go())
            print(f"  {name}: ok")


if __name__ == "__main__":
    tests()
    print("company gates: ok")
