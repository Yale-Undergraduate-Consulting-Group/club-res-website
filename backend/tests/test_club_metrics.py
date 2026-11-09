"""Admin → Metrics (app/services/club_metrics.py).

Each target counts the right rows over the chosen period, is judged only with
enough data, and is attributed to the right member. Admin only. Adding and
releasing a contact records when (migration outreach:1).
From backend/: python tests/test_club_metrics.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "club-metrics-test-secret-0123456789abc")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402

ADMIN, ANA, BEN = 1, 2, 3


async def run(sql: str, args: tuple = ()) -> list:
    db = await get_db()
    try:
        rows = [tuple(r) for r in await (await db.execute(sql, args)).fetchall()]
        await db.commit()
        return rows
    finally:
        await db.close()


async def seed() -> None:
    await init_db()
    for uid, email, role, token in [(ADMIN, "admin@yale.edu", "admin", None), (ANA, "ana@yale.edu", "standard", "t"),
                                    (BEN, "ben@yale.edu", "standard", None)]:
        await run("INSERT INTO users (id, email, name, role, is_active, access_token) VALUES (?, ?, ?, ?, 1, ?)",
                  (uid, email, email.split("@")[0].title(), role, token))
    # Searches: Ana finds 6 usable people (one junk, one without email not counted), Ben finds 2;
    # a builder run and a run from 40 days ago are not member searches in the period.
    for rid, uid, origin, age, usable in [(1, ANA, "member", 1, 6), (2, BEN, "member", 2, 2),
                                          (3, None, "builder", 1, 9), (4, ANA, "member", 40, 9)]:
        await run("""INSERT INTO yucgoutreach_discovery_runs (id, user_id, company_name, status, origin, created_at)
                     VALUES (?, ?, 'Co', 'completed', ?, datetime('now', ?))""", (rid, uid, origin, f"-{age} days"))
        for n in range(usable):
            await run("INSERT INTO yucgoutreach_prospects (run_id, email) VALUES (?, ?)", (rid, f"p{rid}{n}@co.com"))
    await run("INSERT INTO yucgoutreach_prospects (run_id, email, ai_verdict) VALUES (2, 'junk@co.com', 'junk')")
    await run("INSERT INTO yucgoutreach_prospects (run_id, email) VALUES (2, '')")

    await run("INSERT INTO campaigns (id, name, status, owner_user_id) VALUES (1, 'C', 'releasing', ?)", (ANA,))
    await run("INSERT INTO campaigns (id, name, status, owner_user_id, sender_user_id) VALUES (2, 'Draft', 'draft', ?, ?)", (BEN, BEN))
    await run("INSERT INTO campaigns (id, name, status, owner_user_id, sender_user_id) VALUES (3, 'New', 'draft', ?, ?)", (BEN, BEN))
    for cid in range(1, 7):
        await run("INSERT INTO contacts (id, email) VALUES (?, ?)", (cid, f"c{cid}@co.com"))
    # Contact times: on time, late, waiting past its week, waiting within its week (not yet judged),
    # and a row from before the migration (no times).
    for ccid, camp, cid, status, added, released in [
            (1, 1, 1, "sent", "-10 days", "-8 days"), (2, 1, 2, "sent", "-12 days", "-3 days"),
            (3, 2, 3, "pending", "-9 days", None), (4, 2, 4, "pending", "-1 days", None), (5, 1, 5, "sent", None, None)]:
        await run(f"""INSERT INTO campaign_contacts (id, campaign_id, contact_id, status, sent_at, added_at, released_at)
                      VALUES (?, ?, ?, ?, {"datetime('now','-2 days')" if status == 'sent' else 'NULL'},
                              {'datetime(\"now\", ?)' if added else 'NULL'}, {'datetime(\"now\", ?)' if released else 'NULL'})""",
                  (ccid, camp, cid, status, *([added] if added else []), *([released] if released else [])))
    # Drafts: Ana's two drafts, one of them sent; Ben's one draft unsent.
    for uid, camp, cid in [(ANA, 1, 1), (ANA, 2, 6), (BEN, 2, 3)]:
        await run("INSERT INTO generated_emails (user_id, contact_id, campaign_id) VALUES (?, ?, ?)", (uid, cid, camp))
    # Ana sent 3 messages (ISO and SQLite timestamps both): one bounced, one replied; one older message is out of period.
    for mid, ccid, sent_at in [(1, 1, "strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-2 days')"), (2, 2, "datetime('now','-1 days')"),
                               (3, 5, "datetime('now','-3 days')"), (4, 5, "datetime('now','-60 days')")]:
        await run(f"""INSERT INTO outreach_messages (id, campaign_contact_id, sender_id, recipient, tracking_token, rfc_message_id, sent_at)
                      VALUES (?, ?, ?, 'r@co.com', ?, ?, {sent_at})""", (mid, ccid, ANA, f"t{mid}", f"<m{mid}>"))
    await run("INSERT INTO outreach_events (message_id, kind, source_id, occurred_at) VALUES (1, 'bounced', 'dsn', '2026-01-01')")
    await run("INSERT INTO outreach_events (message_id, kind, source_id, occurred_at) VALUES (2, 'replied', 'g', '2026-01-01')")
    await run("INSERT INTO outreach_events (message_id, kind, source_id, occurred_at) VALUES (2, 'opened', 'p', '2026-01-01')")
    await run("""INSERT INTO outreach_dispatches (dispatch_key, campaign_contact_id, sender_user_id, recipient, subject, body, state, claimed_at)
                 VALUES ('initial:3', 3, ?, 'r@co.com', 's', 'b', 'ambiguous', datetime('now','-1 days'))""", (ANA,))
    await run("""INSERT INTO outreach_dispatches (dispatch_key, campaign_contact_id, sender_user_id, recipient, subject, body, state, claimed_at)
                 VALUES ('initial:4', 4, ?, 'r@co.com', 's', 'b', 'claimed', datetime('now','-1 minutes'))""", (ANA,))


def client(uid: int, role: str) -> TestClient:
    c = TestClient(main.app, raise_server_exceptions=False)
    c.cookies.set("yucg_session", create_token(uid, f"{uid}@yale.edu", None, None, role))
    return c


def tests() -> None:
    asyncio.run(seed())
    admin = client(ADMIN, "admin")
    assert client(ANA, "standard").get("/api/analytics/club-metrics").status_code == 403
    assert admin.get("/api/analytics/club-metrics?days=12").status_code == 422

    body = admin.get("/api/analytics/club-metrics?days=30").json()
    t = {row["key"]: row for row in body["targets"]}
    assert (t["ready_to_send"]["count"], t["ready_to_send"]["of"]) == (1, 3)
    assert (t["searches_usable"]["count"], t["searches_usable"]["of"]) == (1, 2), t["searches_usable"]
    assert (t["drafts_sent"]["count"], t["drafts_sent"]["of"]) == (1, 3), t["drafts_sent"]
    assert (t["released_in_week"]["count"], t["released_in_week"]["of"]) == (1, 3), t["released_in_week"]
    assert (t["bounce_rate"]["count"], t["bounce_rate"]["of"]) == (1, 3), t["bounce_rate"]
    assert (t["reply_rate"]["count"], t["reply_rate"]["of"]) == (1, 3)
    assert t["needs_reconciliation"]["count"] == 1, "a claim from a minute ago is in flight, not stuck"
    # Fewer than five of anything is not judged.
    assert all(row["met"] is None for row in body["targets"]), body["targets"]
    assert body["waiting"]["contacts"] == 2
    ana = next(m for m in body["members"] if m["user_id"] == ANA)
    assert (ana["searches"], ana["drafts"], ana["sent"], ana["replies"], ana["bounces"]) == (1, 2, 3, 1, 1), ana
    assert ana["ready_to_send"] is True
    # The period narrows: 90 days includes Ana's older search and message.
    wide = {r["key"]: r for r in admin.get("/api/analytics/club-metrics?days=90").json()["targets"]}
    assert wide["searches_usable"]["of"] == 3 and wide["bounce_rate"]["of"] == 4

    # Enough data is judged against the target.
    with patch("app.services.member_readiness.readiness", AsyncMock(return_value={
            i: {"ready_to_send": i <= 9} for i in range(1, 11)})):
        ready = next(r for r in admin.get("/api/analytics/club-metrics").json()["targets"] if r["key"] == "ready_to_send")
    assert (ready["value"], ready["met"]) == (90.0, True), ready

    # Adding a contact to a campaign records when (migration outreach:1).
    assert asyncio.run(run("SELECT app, version FROM schema_migrations WHERE app = 'outreach'")) == [("outreach", 1)]
    with patch("app.services.contact_access.require_contact_access", AsyncMock(return_value=None)):
        added = client(BEN, "standard").post("/api/campaigns/3/contacts", json={"contact_ids": [6], "email_subjects": {"6": "Hi"}, "email_bodies": {"6": "Hello"}})
    assert added.status_code == 200 and added.json()["added"] == 1, added.text
    stamp = asyncio.run(run("SELECT added_at IS NOT NULL, released_at FROM campaign_contacts WHERE campaign_id = 3 AND contact_id = 6"))
    assert stamp == [(1, None)], stamp


if __name__ == "__main__":
    tests()
    print("club metrics: ok")
