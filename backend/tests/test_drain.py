"""Drain must not double-send. From backend/: python tests/test_drain.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "p-drain-test-secret-not-a-known-default-xx"
os.environ["CAMPAIGN_SEND_DELAY_SEC"] = "0"

from app.database import get_db, init_db  # noqa: E402
from app.routers.campaigns import drain_campaign, release_campaign  # noqa: E402


async def _seed(n: int = 10) -> int:
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
    await init_db()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO users (id, email) VALUES (1, 'sender@yale.edu')"
        )
        cur = await db.execute(
            "INSERT INTO campaigns (name, status, owner_user_id, sender_user_id) VALUES ('week', 'releasing', 1, 1)"
        )
        cid = cur.lastrowid
        for i in range(n):
            email = f"p{i}@example.com"
            await db.execute(
                "INSERT INTO contacts (name, email) VALUES (?, ?)",
                (f"P{i}", email),
            )
            contact_id = i + 1
            await db.execute(
                """INSERT INTO campaign_contacts
                   (campaign_id, contact_id, email_subject, email_body, status)
                   VALUES (?, ?, 'Hi', 'Body', 'pending')""",
                (cid, contact_id),
            )
        # This test is about two ticks never sending the same row twice, not
        # about pacing. Give example.com a format with a delivery behind it so
        # the mailbox-proof gate (which would otherwise release one probe and
        # hold the other nine) does not stand in for the thing under test.
        await db.execute(
            """INSERT INTO company_email_patterns
                   (company_domain, pattern_key, pattern_template, confidence,
                    sample_count, verified_samples, sources_json)
               VALUES ('example.com','first.last','{first}.{last}',0.9,4,3,'["gmail_reply"]')"""
        )
        await db.commit()
        return int(cid)
    finally:
        await db.close()


async def _run() -> None:
    sent_cc: list[int] = []
    gate = asyncio.Event()
    started = 0

    async def fake_send(**kwargs):
        nonlocal started
        started += 1
        sent_cc.append(int(kwargs["campaign_contact_id"]))
        if started == 1:
            await gate.wait()
        return {"ok": True, "message_id": "m", "thread_id": "t"}

    import app.services.gmail_api as gmail_api

    gmail_api.send_via_gmail_api_with_tracking = fake_send  # type: ignore[method-assign]

    cid = await _seed(10)
    await release_campaign(cid, {"id": 1})
    t1 = asyncio.create_task(drain_campaign(cid, 1, limit=5))
    t2 = asyncio.create_task(drain_campaign(cid, 1, limit=5))
    await asyncio.sleep(0.05)
    gate.set()
    results = await asyncio.gather(t1, t2)
    total = sum(r["sent"] for r in results)
    assert total == 10, results
    assert len(sent_cc) == 10, sent_cc
    assert len(set(sent_cc)) == 10, sent_cc

    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT status, COUNT(*) AS n FROM campaign_contacts GROUP BY status"
        )
        by = {row["status"]: int(row["n"]) for row in await cur.fetchall()}
    finally:
        await db.close()
    assert by.get("sent") == 10, by
    assert by.get("pending", 0) == 0
    assert by.get("sending", 0) == 0


def test_two_drains_never_double_send() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_two_drains_never_double_send()
    print("ok")
    Path(_tmp.name).unlink(missing_ok=True)
    Path(_tmp.name + "-wal").unlink(missing_ok=True)
    Path(_tmp.name + "-shm").unlink(missing_ok=True)
