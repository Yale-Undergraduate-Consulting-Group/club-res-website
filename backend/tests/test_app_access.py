"""Per-app access (app/app_access.py, docs/PLATFORM.md step 3).

Every app is open to all active members until an admin restricts it; then
only listed members and admins may use it, on every route and in every job
that acts for a member. The platform's own routes stay open to every member.
From backend/: python tests/test_app_access.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "app-access-test-secret-0123456789abcd")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402

ADMIN, ANA, BEN, BUILDER = 1, 2, 3, 4


async def setup() -> None:
    await init_db()   # the baseline and every app's migrations, app_access included
    db = await get_db()
    try:
        await db.executemany(
            "INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, ?, 1)",
            [(ADMIN, "admin@yale.edu", "Admin", "admin"), (ANA, "ana@yale.edu", "Ana", "standard"),
             (BEN, "ben@yale.edu", "Ben", "standard"), (BUILDER, "builder@system.invalid", "Builder", "system")],
        )
        await db.commit()
    finally:
        await db.close()


def as_member(user_id: int, role: str = "standard") -> TestClient:
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set("yucg_session", create_token(user_id, f"{user_id}@yale.edu", None, None, role))
    return client


async def query(sql: str, params: tuple = ()) -> list:
    db = await get_db()
    try:
        return [tuple(r) for r in await (await db.execute(sql, params)).fetchall()]
    finally:
        await db.close()


def tests() -> None:
    asyncio.run(setup())
    admin, ana, ben = as_member(ADMIN, "admin"), as_member(ANA), as_member(BEN)

    # Open by default: every member uses Outreach and sees it on sign-in.
    assert ben.get("/api/contacts").status_code == 200
    assert "outreach" in ben.get("/api/auth/me").json()["user"]["apps"]

    # Only admins change access, only for real apps and real members.
    assert ana.put("/api/admin/apps/outreach", json={"restricted": True, "member_ids": [ANA]}).status_code == 403
    assert admin.put("/api/admin/apps/core", json={"restricted": True, "member_ids": []}).status_code == 404
    assert admin.put("/api/admin/apps/outreach", json={"restricted": True, "member_ids": [999]}).status_code == 400

    # Restrict Outreach to Ana.
    assert admin.put("/api/admin/apps/outreach", json={"restricted": True, "member_ids": [ANA]}).status_code == 200
    listed = {row["app"]: row for row in admin.get("/api/admin/apps").json()}
    assert listed["outreach"]["restricted"] and listed["outreach"]["member_ids"] == [ANA], listed
    assert asyncio.run(query("SELECT action, resource_id FROM audit_log WHERE action = 'app_access_update'")) \
        == [("app_access_update", "outreach")]

    # Every Outreach route refuses Ben - the routers with their own per-route
    # sign-in too - while Ana and admins carry on; the platform stays open.
    for path in ("/api/contacts", "/api/yucgoutreach/runs", "/api/analytics/leaderboard"):
        assert ben.get(path).status_code == 403, path
        assert ana.get(path).status_code != 403, path
        assert admin.get(path).status_code != 403, path
    assert ben.get("/api/settings").status_code == 200
    assert "outreach" not in ben.get("/api/auth/me").json()["user"]["apps"]
    assert "outreach" in ana.get("/api/auth/me").json()["user"]["apps"]
    # A request with no session keeps its own check: the tracking pixel in
    # sent mail still answers.
    assert TestClient(main.app, raise_server_exceptions=False).get("/api/track/open/unknown.png").status_code != 403

    # Jobs that act for a member skip Ben.
    from app.app_access import may_use
    from app.services import gmail_reply_sync, yucgoutreach_discovery

    async def jobs() -> None:
        db = await get_db()
        try:
            assert await may_use(db, ANA, "outreach") and not await may_use(db, BEN, "outreach")
            assert await may_use(db, BUILDER, "outreach")   # the catalog builder acts for the club
            assert await may_use(db, BEN, "core")
            await db.execute(
                "INSERT INTO yucgoutreach_discovery_runs (id, user_id, company_name, status) VALUES (1, ?, 'Acme', 'queued')",
                (BEN,),
            )
            await db.execute("INSERT INTO contacts (id, email, name) VALUES (1, 'x@acme.com', 'X')")
            await db.execute("INSERT INTO campaigns (id, name, owner_user_id, sender_user_id, status) VALUES (1, 'c', ?, ?, 'releasing')",
                             (BEN, BEN))
            await db.execute(
                "INSERT INTO campaign_contacts (campaign_id, contact_id, status, sent_at, sent_by_user_id) VALUES (1, 1, 'sent', CURRENT_TIMESTAMP, ?)",
                (BEN,),
            )
            await db.commit()
        finally:
            await db.close()
        # A queued search of a member without access is cancelled, not run.
        with patch.object(yucgoutreach_discovery, "_yucgoutreach_run_guard"):
            await yucgoutreach_discovery.drain_queued_yucgoutreach_runs()
        assert await query("SELECT status, error_message FROM yucgoutreach_discovery_runs WHERE id = 1") \
            == [("failed", "No access to Outreach")]
        # His mailbox is not read for replies.
        synced: list[int] = []

        async def record(user_id, **_kw):
            synced.append(user_id)
            return {"ok": True}

        with patch.object(gmail_reply_sync, "sync_replies_for_user", record):
            await gmail_reply_sync.sync_replies_all_senders()
        assert BEN not in synced, synced
        # His releasing campaign stops and needs attention instead of sending.
        from app.routers.campaigns import drain_releasing_campaigns

        result = await drain_releasing_campaigns()
        assert not result["ok"] and "access to Outreach" in result["results"][0]["error"], result
        assert await query("SELECT status FROM campaigns WHERE id = 1") == [("needs_attention",)]

    asyncio.run(jobs())

    # Opening it again restores Ben.
    assert admin.put("/api/admin/apps/outreach", json={"restricted": False, "member_ids": []}).status_code == 200
    assert ben.get("/api/contacts").status_code == 200


if __name__ == "__main__":
    tests()
    print("app access: ok")
