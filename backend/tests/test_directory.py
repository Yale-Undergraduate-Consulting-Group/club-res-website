"""The contact directory (app/services/contact_directory.py, /api/directory).

One row per address across contacts and prospects, only what the viewer may see,
with the club's reach state and the address proof. Filters and paging are
applied before a page is cut. From backend/: python tests/test_directory.py
"""
import asyncio
import csv
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "directory-test-secret-0123456789abcdef")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402
from app.services.company_claims import company_key  # noqa: E402
from app.services.contact_directory import reach_for  # noqa: E402

ADMIN, ANA, BEN, CLEO = 1, 2, 3, 4
FOREVER = "2999-01-01T00:00:00+00:00"


async def run(sql: str, args: tuple = ()) -> None:
    db = await get_db()
    try:
        await db.execute(sql, args)
        await db.commit()
    finally:
        await db.close()


async def seed() -> None:
    await init_db()
    for uid, name, role in [(ADMIN, "Admin", "admin"), (ANA, "Ana", "standard"), (BEN, "Ben", "standard"), (CLEO, "Cleo", "standard")]:
        await run("INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, ?, 1)", (uid, f"{name.lower()}@yale.edu", name, role))

    # id, email, name, title, company, domain, owner
    contacts = [
        (1, "ana.private@acme.com", "Ana Private", "VP", "Acme", "acme.com", ANA),
        (2, "ben.private@zeta.com", "Ben Private", "VP", "Zeta", "zeta.com", BEN),
        (3, "shared@acme.com", "Shared Person", "CEO", "Acme", "acme.com", None),
        (4, "Dup@Beta.com", "Dup Person", "", "Beta", "beta.com", None),
        (5, "bounce@acme.com", "Bounce Person", "CTO", "Acme", "acme.com", None),
        (6, "ready@clean.com", "Ready Person", "CFO", "Clean Co", "clean.com", None),
        (7, "claimed@held.com", "Claimed Person", "CFO", "Held Co", "held.com", None),
        (8, "queued@clean.com", "Queued Person", "CFO", "Clean Co", "clean.com", None),
        (9, "manual@clean.com", "Manual Person", "CFO", "Clean Co", "clean.com", None),
        (10, "formula@acme.com", "=1+1", "CFO", "Acme", "acme.com", None),
    ]
    for row in contacts:
        await run("INSERT INTO contacts (id, email, name, title, company, company_domain, owner_id) VALUES (?, ?, ?, ?, ?, ?, ?)", row)
    await run("UPDATE contacts SET pipeline_status = 'contacted' WHERE id = 9")

    # Runs: Ben's finished run is shared; his running run, Cleo's old run are private; a builder run is shared.
    for rid, uid, origin, status, count, age in [(1, BEN, "member", "completed", 3, "-1 days"), (2, BEN, "member", "running", 0, "-1 days"),
                                                  (3, CLEO, "member", "completed", 1, "-40 days"), (4, None, "builder", "completed", 1, "-1 days")]:
        await run("""INSERT INTO yucgoutreach_discovery_runs (id, user_id, company_name, status, origin, prospects_count, completed_at)
                     VALUES (?, ?, 'Co', ?, ?, ?, datetime('now', ?))""", (rid, uid, status, origin, count, age))
    for rid, first, last, email, title, company in [
        (1, "Dup", "Person", "dup@beta.com", "CFO", "Beta"), (1, "Pro", "Spect", "prospect@gamma.com", "COO", "Gamma"),
        (1, "Ver", "Ified", "verified-prospect@gamma.com", "CEO", "Gamma"), (2, "Se", "Cret", "secret@gamma.com", "CEO", "Gamma"),
        (3, "Old", "Run", "old@delta.com", "CEO", "Delta"), (4, "Bui", "Lder", "builder@eps.com", "CEO", "Eps"),
    ]:
        await run("INSERT INTO yucgoutreach_prospects (run_id, first_name, last_name, email, title, company) VALUES (?, ?, ?, ?, ?, ?)",
                  (rid, first, last, email, title, company))

    # Verdicts: four confirmed mailboxes (one a prospect only).
    for email in ("ready@clean.com", "claimed@held.com", "queued@clean.com", "verified-prospect@gamma.com"):
        await run("INSERT INTO mailbox_verdicts (email, mailbox, method, checked_at, expires_at) VALUES (?, 'provider_high_confidence', 'verifalia', ?, ?)",
                  (email, "2026-11-01T00:00:00+00:00", FOREVER))

    await run("INSERT INTO campaigns (id, name, status, owner_user_id, sender_user_id) VALUES (1, 'Ben', 'releasing', ?, ?)", (BEN, BEN))
    await run("INSERT INTO campaigns (id, name, status, owner_user_id, sender_user_id) VALUES (2, 'Ana', 'draft', ?, ?)", (ANA, ANA))
    for ccid, camp, cid, status, sent, replied, by in [
        (1, 2, 1, "pending", None, None, None), (2, 1, 3, "sent", "2026-11-03 10:00:00", None, BEN),
        (3, 1, 4, "replied", "2026-11-01 09:00:00", "2026-11-02 12:00:00", BEN), (4, 1, 5, "bounced", "2026-11-04 08:00:00", None, BEN),
        (5, 1, 8, "pending", None, None, None),
    ]:
        await run("""INSERT INTO campaign_contacts (id, campaign_id, contact_id, status, sent_at, replied_at, sent_by_user_id)
                     VALUES (?, ?, ?, ?, ?, ?, ?)""", (ccid, camp, cid, status, sent, replied, by))
    for mid, sent in [(1, "2026-11-01 10:00:00"), (2, "2026-11-03 10:00:00")]:
        await run("""INSERT INTO outreach_messages (id, campaign_contact_id, sender_id, recipient, tracking_token, rfc_message_id, sent_at)
                     VALUES (?, 2, ?, 'shared@acme.com', ?, ?, ?)""", (mid, BEN, f"t{mid}", f"<m{mid}>", sent))
    await run("INSERT INTO company_claims (company_key, company_name, member_id) VALUES (?, 'Held Co', ?)", (company_key("Held Co", "held.com"), BEN))


def client(uid: int, role: str = "standard") -> TestClient:
    c = TestClient(main.app, raise_server_exceptions=False)
    c.cookies.set("yucg_session", create_token(uid, f"{uid}@yale.edu", None, None, role))
    return c


def listed(c: TestClient, **params) -> dict:
    response = c.get("/api/directory", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def emails(body: dict) -> set[str]:
    return {r["email"] for r in body["items"]}


def tests() -> None:
    asyncio.run(seed())
    ana, ben, admin = client(ANA), client(BEN), client(ADMIN, "admin")

    assert TestClient(main.app, raise_server_exceptions=False).get("/api/directory").status_code == 401
    assert TestClient(main.app, raise_server_exceptions=False).get("/api/directory/summary").status_code == 401

    # Visibility: another member's private contact, a run still running, and a
    # teammate's run older than the sharing window stay hidden.
    everyone = listed(ana, limit=200)
    assert everyone["total"] == 12 == len(everyone["items"]), everyone["total"]
    seen = emails(everyone)
    assert not seen & {"ben.private@zeta.com", "secret@gamma.com", "old@delta.com"}, seen
    assert {"ana.private@acme.com", "shared@acme.com", "prospect@gamma.com", "builder@eps.com"} <= seen
    assert "ana.private@acme.com" not in emails(listed(ben, limit=200)) and "ben.private@zeta.com" in emails(listed(ben, limit=200))
    assert "secret@gamma.com" in emails(listed(ben, limit=200)), "a member's own running run is theirs"
    # Admins see every contact (list_contacts' rule) but runs only as anyone may.
    admin_seen = emails(listed(admin, limit=200))
    assert {"ana.private@acme.com", "ben.private@zeta.com"} <= admin_seen and "secret@gamma.com" not in admin_seen

    # Dedupe: the contact and the prospect are one person, the contact row wins
    # and its blank title is filled from the prospect.
    dup = [r for r in everyone["items"] if r["email"] == "dup@beta.com"]
    assert len(dup) == 1 and dup[0]["on_file"] is True and dup[0]["name"] == "Dup Person" and dup[0]["title"] == "CFO", dup
    prospect = next(r for r in everyone["items"] if r["email"] == "prospect@gamma.com")
    assert (prospect["name"], prospect["on_file"], prospect["source"]) == ("Pro Spect", False, "Find people"), prospect

    # Reach states, with who and when and how many.
    by_email = {r["email"]: r for r in everyone["items"]}
    shared = by_email["shared@acme.com"]["reach"]
    assert (shared["state"], shared["by"], shared["by_me"], shared["sends"]) == ("contacted", "Ben", False, 2), shared
    assert shared["at"].startswith("2026-11-03"), shared
    assert listed(ben, q="shared@acme.com")["items"][0]["reach"]["by_me"] is True
    assert by_email["dup@beta.com"]["reach"]["state"] == "replied"
    assert by_email["bounce@acme.com"]["reach"]["state"] == "bounced"
    mine = by_email["ana.private@acme.com"]["reach"]
    assert (mine["state"], mine["by"], mine["by_me"]) == ("queued", "Ana", True), mine
    assert (by_email["queued@clean.com"]["reach"]["state"], by_email["queued@clean.com"]["reach"]["by"]) == ("queued", "Ben")
    assert (by_email["manual@clean.com"]["reach"]["state"], by_email["manual@clean.com"]["reach"]["by"]) == ("contacted", None)
    assert by_email["ready@clean.com"]["reach"]["state"] == "not_contacted"
    # Another member holds Held Co: flagged for Ana, not for Ben.
    assert by_email["claimed@held.com"]["reach"]["claimed_by"] == "Ben"
    assert listed(ben, q="claimed@held.com")["items"][0]["reach"]["claimed_by"] is None

    # Ready = confirmed address, nobody reached or queued, company not held by someone else.
    ready = listed(ana, ready=1)
    assert emails(ready) == {"ready@clean.com", "verified-prospect@gamma.com"} and ready["total"] == 2, emails(ready)
    assert all(r["ready"] for r in ready["items"])
    assert emails(listed(ben, ready=1)) == {"ready@clean.com", "verified-prospect@gamma.com", "claimed@held.com"}

    # Filters apply before the page is cut: totals describe the whole filtered set.
    confirmed = listed(ana, proof="mailbox_confirmed", limit=1)
    assert confirmed["total"] == 4 and len(confirmed["items"]) == 1, confirmed["total"]
    pages = [listed(ana, proof="mailbox_confirmed", limit=3, offset=o) for o in (0, 3)]
    assert [len(p["items"]) for p in pages] == [3, 1] and all(p["total"] == 4 for p in pages)
    assert len(emails(pages[0]) | emails(pages[1])) == 4
    assert emails(listed(ana, reach="queued")) == {"ana.private@acme.com", "queued@clean.com"}
    assert emails(listed(ana, reach="contacted")) == {"shared@acme.com", "manual@clean.com"}
    assert listed(ana, proof="unverified")["total"] == 12 - 4
    # A text filter and a reach filter together.
    assert emails(listed(ana, q="clean.com", reach="not_contacted")) == {"ready@clean.com"}
    assert listed(ana, limit=5, offset=10)["total"] == 12 and len(listed(ana, limit=5, offset=10)["items"]) == 2
    walked = [e for o in (0, 5, 10) for e in (r["email"] for r in listed(ana, limit=5, offset=o)["items"])]
    assert len(walked) == len(set(walked)) == 12, "pages do not overlap or skip"
    # LIKE wildcards in a search are text.
    assert listed(ana, q="%")["total"] == 0 and listed(ana, q="_")["total"] == 0
    assert listed(ana, q="acme")["total"] == 4 and listed(ana, company="clean co")["total"] == 3

    assert ana.get("/api/directory", params={"proof": "bogus"}).status_code == 422
    assert ana.get("/api/directory", params={"reach": "bogus"}).status_code == 422
    assert ana.get("/api/directory", params={"limit": 0}).status_code == 422

    summary = ana.get("/api/directory/summary").json()
    assert summary["total"] == 12 and summary["capped"] is False and summary["ready"] == 2, summary
    assert summary["proof"]["mailbox_confirmed"] == 4 and sum(summary["proof"].values()) == 12
    assert summary["reach"]["queued"] == 2 and summary["reach"]["replied"] == 1 and sum(summary["reach"].values()) == 12
    assert {"enabled", "credits_used_today", "credits_cap_today", "verified_total", "queue_remaining"} <= set(summary["backlog"])

    # Export is the current filter, and a name that is a formula is text.
    response = ana.get("/api/directory/export", params={"q": "acme"})
    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0][:4] == ["Name", "Title", "Company", "Email"] and len(rows) == 1 + 4
    assert "'=1+1" in {r[0] for r in rows[1:]}
    assert "Ben" in {r[7] for r in rows[1:]}
    assert len(list(csv.reader(io.StringIO(ana.get("/api/directory/export", params={"ready": 1}).text)))) == 1 + 2

    # More addresses than one IN list holds is answered from one pass over the ledger.
    async def big():
        db = await get_db()
        try:
            wanted = [f"nobody{i}@nowhere.com" for i in range(700)] + ["shared@acme.com", "queued@clean.com"]
            return await reach_for(db, wanted, ANA)
        finally:
            await db.close()

    wide = asyncio.run(big())
    assert (wide["shared@acme.com"]["state"], wide["shared@acme.com"]["by"], wide["shared@acme.com"]["sends"]) == ("contacted", "Ben", 2)
    assert wide["queued@clean.com"]["state"] == "queued" and wide["nobody5@nowhere.com"]["state"] == "not_contacted"


if __name__ == "__main__":
    tests()
    print("directory tests passed")
