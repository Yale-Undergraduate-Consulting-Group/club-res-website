"""Hermetic proof of the discovery-to-dispatch safety boundaries."""
import asyncio
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["JWT_SECRET"] = "outreach-pipeline-test-secret-xxxxxxxx"
os.environ["CAMPAIGN_SEND_DELAY_SEC"] = "0"

from fastapi import HTTPException
from app.database import get_db, init_db
from app.models import CampaignContactAdd
from app.routers.campaigns import (
    add_contacts_to_campaign,
    _campaign_readiness,
    _claim_pending_rows,
    drain_campaign,
    drain_releasing_campaigns,
    list_campaigns,
    release_campaign,
    retry_failed_campaign_contacts,
)
from app.routers.yucgoutreach import YucgOutreachRunCreate, create_run
from app.routers.outreach import (
    OutreachCampaignAddContacts,
    OutreachCampaignCreate,
    add_contacts_to_outreach_campaign,
    create_outreach_campaign,
    get_outreach_campaign,
    list_outreach_campaigns,
)
from app.services.gmail_api import DeliveryNotAttemptedError
from app.services.yucgoutreach_discovery import (
    DiscoveryLeaseLost,
    _active_lease,
    _run_update,
    drain_queued_yucgoutreach_runs,
    recover_interrupted_yucgoutreach_runs,
)


async def rejected(coro, status: int) -> None:
    try:
        await coro
        raise AssertionError("Expected request to be rejected")
    except HTTPException as exc:
        assert exc.status_code == status, exc


async def run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/pipeline.db"
        await init_db()
        db = await get_db()
        await db.execute(
            "INSERT INTO users(id,email,role,is_active) VALUES "
            "(1,'one@yale.edu','standard',1),(2,'two@yale.edu','standard',1)"
        )
        await db.execute(
            "INSERT INTO contacts(id,name,email,owner_id) VALUES "
            "(1,'Alice','alice@example.org',NULL),(2,'Private','private@example.org',2)"
        )
        await db.execute(
            "INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id) "
            "VALUES (1,'Safe batch','draft',1,1)"
        )
        await db.commit()
        await db.close()

        payload = CampaignContactAdd(
            contact_ids=[1, 1],
            email_subjects={"1": "Hello Alice"},
            email_bodies={"1": "A specific message"},
        )
        first = await add_contacts_to_campaign(1, payload, {"id": 1, "role": "standard"})
        second = await add_contacts_to_campaign(1, payload, {"id": 1, "role": "standard"})
        assert first["added"] == 1 and second["added"] == 0
        await rejected(
            add_contacts_to_campaign(
                1,
                CampaignContactAdd(contact_ids=[2]),
                {"id": 1, "role": "standard"},
            ),
            404,
        )
        mine = await create_outreach_campaign(
            OutreachCampaignCreate(name="My target list"), {"id": 1, "role": "standard"}
        )
        other = await create_outreach_campaign(
            OutreachCampaignCreate(name="Other target list"), {"id": 2, "role": "standard"}
        )
        visible = await list_outreach_campaigns({"id": 1, "role": "standard"})
        assert [row["id"] for row in visible] == [mine["id"]]
        send_campaigns = await list_campaigns({"id": 1, "role": "standard"})
        assert [row["id"] for row in send_campaigns] == [1]
        await rejected(
            get_outreach_campaign(other["id"], {"id": 1, "role": "standard"}), 404
        )
        await rejected(
            add_contacts_to_outreach_campaign(
                mine["id"],
                OutreachCampaignAddContacts(contact_ids=[2]),
                {"id": 1, "role": "standard"},
            ),
            404,
        )
        await rejected(
            create_outreach_campaign(
                OutreachCampaignCreate(name="Club list", type="community"),
                {"id": 1, "role": "standard"},
            ),
            403,
        )
        club = await create_outreach_campaign(
            OutreachCampaignCreate(name="Club list", type="community"),
            {"id": 1, "role": "admin"},
        )
        await add_contacts_to_outreach_campaign(
            club["id"],
            OutreachCampaignAddContacts(contact_ids=[2]),
            {"id": 1, "role": "admin"},
        )
        club_for_member = await get_outreach_campaign(
            club["id"], {"id": 1, "role": "standard"}
        )
        assert club_for_member["contacts"] == []

        await release_campaign(1, {"id": 1})

        async def accepted_after_bounce(**_kwargs):
            other = await get_db()
            await other.execute("UPDATE campaign_contacts SET status='bounced' WHERE campaign_id=1")
            await other.commit()
            await other.close()
            return {"message_id": "gmail-1", "thread_id": "thread-1"}

        with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", accepted_after_bounce):
            sent = await drain_campaign(1, 1)
        assert sent["sent"] == 1
        db = await get_db()
        row = await (await db.execute(
            "SELECT status,sent_at FROM campaign_contacts WHERE campaign_id=1"
        )).fetchone()
        assert row["status"] == "bounced" and row["sent_at"]

        await db.execute(
            "INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id) VALUES (2,'Reconnect','draft',1,1)"
        )
        await db.execute(
            "INSERT INTO campaign_contacts(id,campaign_id,contact_id,status,email_subject,email_body) "
            "VALUES (2,2,1,'pending','Reconnect','Body')"
        )
        await db.commit()
        await db.close()
        await release_campaign(2, {"id": 1})
        async def rejected_before_acceptance(**kwargs):
            local = await get_db()
            await local.execute(
                """INSERT INTO outreach_messages(
                    campaign_contact_id,sender_id,recipient,tracking_token,rfc_message_id,dispatch_key
                ) VALUES (?,?,?,?,?,?)""",
                (kwargs["campaign_contact_id"], kwargs["user_id"], kwargs["to_email"],
                 "rejected-token", "<rejected@example.org>", kwargs["dispatch_key"]),
            )
            await local.commit()
            await local.close()
            raise DeliveryNotAttemptedError("Gmail rejected the request")

        disconnected = AsyncMock(side_effect=rejected_before_acceptance)
        with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", disconnected):
            await drain_campaign(2, 1)
        db = await get_db()
        state = await (await db.execute(
            "SELECT state FROM outreach_dispatches WHERE dispatch_key='initial:2'"
        )).fetchone()
        assert state["state"] == "ready"
        stale = await (await db.execute(
            "SELECT COUNT(*) AS n FROM outreach_messages WHERE dispatch_key='initial:2'"
        )).fetchone()
        assert stale["n"] == 0
        await db.close()
        retry = await retry_failed_campaign_contacts(2, {"id": 1})
        assert retry["retried"] == 1 and retry["status"] == "paused"

        await release_campaign(2, {"id": 1})
        accepted = AsyncMock(return_value={"message_id": "gmail-2", "thread_id": "thread-2"})
        with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", accepted):
            await drain_campaign(2, 1)
        assert accepted.await_count == 1

        db = await get_db()
        await db.execute(
            "INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id) VALUES "
            "(3,'Inactive','draft',2,2),(4,'Active','draft',1,1)"
        )
        await db.execute(
            "INSERT INTO campaign_contacts(id,campaign_id,contact_id,status,email_subject,email_body) VALUES "
            "(3,3,1,'pending','Inactive','Body'),(4,4,1,'pending','Active','Body')"
        )
        await db.commit()
        await db.close()
        await release_campaign(3, {"id": 2})
        await release_campaign(4, {"id": 1})
        db = await get_db()
        await db.execute("UPDATE users SET is_active=0 WHERE id=2")
        await db.commit()
        await db.close()
        with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", accepted):
            batch = await drain_releasing_campaigns()
        assert batch["campaigns"] == 2 and batch["ok"] is False
        assert any(item.get("campaign_id") == 3 and not item["ok"] for item in batch["results"])
        assert any(item.get("status") == "sent" for item in batch["results"])
        db = await get_db()
        inactive_state = await (await db.execute(
            "SELECT status FROM campaigns WHERE id=3"
        )).fetchone()
        await db.close()
        assert inactive_state["status"] == "needs_attention"

        db = await get_db()
        await db.execute("INSERT INTO users(id,email,role,is_active) VALUES (3,'three@yale.edu','standard',1)")
        await db.execute(
            "INSERT INTO contacts(id,name,email) VALUES "
            "(3,'Third','third@example.org'),(4,'Fourth','fourth@example.org')"
        )
        await db.execute(
            "INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id) "
            "VALUES (5,'Daily cap','draft',3,3)"
        )
        await db.execute(
            "INSERT INTO campaign_contacts(id,campaign_id,contact_id,status,email_subject,email_body) VALUES "
            "(5,5,3,'pending','Third','Body'),(6,5,4,'pending','Fourth','Body')"
        )
        await db.commit()
        await db.close()
        await release_campaign(5, {"id": 3})
        os.environ["CAMPAIGN_DAILY_SEND_LIMIT"] = "1"
        capped_sender = AsyncMock(return_value={"message_id": "gmail-cap", "thread_id": "thread-cap"})
        with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", capped_sender):
            capped = await drain_campaign(5, 3, limit=5)
            capped_again = await drain_campaign(5, 3, limit=5)
        os.environ.pop("CAMPAIGN_DAILY_SEND_LIMIT", None)
        assert capped["sent"] == 1 and capped["pending_left"] == 1
        assert capped_again["sent"] == 0 and capped_sender.await_count == 1

        # A member watching the page should not wait for the next scheduler
        # tick, so creating a run kicks the same drain immediately. The kick is
        # held here so the contention below is still two workers racing for one
        # queued run rather than one worker and an already-claimed run.
        import app.routers.yucgoutreach as yucg_router

        kick = AsyncMock()
        with patch.object(yucg_router, "_kick_discovery_drain", kick):
            created = await create_run(
                YucgOutreachRunCreate(company_name="Example", max_prospects=3),
                {"id": 1, "role": "standard"},
            )
            await asyncio.sleep(0)
            await rejected(
                create_run(
                    YucgOutreachRunCreate(company_name="Duplicate", max_prospects=3),
                    {"id": 1, "role": "standard"},
                ),
                409,
            )
        assert kick.await_count == 1, "a new run waits for the scheduler tick"

        guard = AsyncMock()
        with patch("app.services.yucgoutreach_discovery._yucgoutreach_run_guard", guard):
            claims = await asyncio.gather(
                drain_queued_yucgoutreach_runs(), drain_queued_yucgoutreach_runs()
            )
        assert sum(item["claimed"] for item in claims) == 1
        assert guard.await_count == 1
        assert await recover_interrupted_yucgoutreach_runs() == 0
        token_context = _active_lease.set("stale-worker-token")
        try:
            try:
                await _run_update(created["id"], progress_message="stale overwrite")
                raise AssertionError("Expected a stale discovery worker to lose its lease")
            except DiscoveryLeaseLost:
                pass
        finally:
            _active_lease.reset(token_context)
        db = await get_db()
        await db.execute(
            "UPDATE yucgoutreach_discovery_runs SET lease_expires_at=datetime('now','-1 minute') WHERE id=?",
            (created["id"],),
        )
        await db.commit()
        await db.close()
        assert await recover_interrupted_yucgoutreach_runs() == 1
        db = await get_db()
        recovered = await (await db.execute(
            "SELECT status FROM yucgoutreach_discovery_runs WHERE id=?", (created["id"],)
        )).fetchone()
        await db.close()
        assert recovered["status"] == "queued"

        db = await get_db()
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs(user_id,company_name,status)
               VALUES (2,'Suspended search','queued')"""
        )
        suspended_id = (await (await db.execute("SELECT last_insert_rowid() AS id")).fetchone())["id"]
        await db.commit()
        await db.close()
        with patch("app.services.yucgoutreach_discovery._yucgoutreach_run_guard", AsyncMock()):
            await drain_queued_yucgoutreach_runs()
        db = await get_db()
        suspended = await (await db.execute(
            "SELECT status FROM yucgoutreach_discovery_runs WHERE id=?", (suspended_id,)
        )).fetchone()
        await db.close()
        assert suspended["status"] == "failed"

    # Existing installations may already contain duplicate pending rows. The
    # migration preserves history, prevents new duplicates, and release blocks
    # until an owner explicitly reconciles the old rows.
    with tempfile.TemporaryDirectory() as legacy_tmp:
        legacy_path = Path(legacy_tmp) / "legacy.db"
        with sqlite3.connect(legacy_path) as legacy:
            legacy.executescript(
                """
                CREATE TABLE contacts(
                    id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE NOT NULL,
                    title TEXT, company TEXT, company_domain TEXT, linkedin_url TEXT,
                    confidence TEXT, department TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE campaigns(
                    id INTEGER PRIMARY KEY, name TEXT, status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE campaign_contacts(
                    id INTEGER PRIMARY KEY, campaign_id INTEGER, contact_id INTEGER,
                    email_subject TEXT, email_body TEXT, status TEXT DEFAULT 'pending',
                    sent_at TIMESTAMP, opened_at TIMESTAMP, replied_at TIMESTAMP, last_error TEXT
                );
                INSERT INTO contacts(id,email) VALUES(1,'duplicate@example.org');
                INSERT INTO campaigns(id,name,status) VALUES(1,'Legacy duplicates','draft');
                INSERT INTO campaign_contacts(id,campaign_id,contact_id,email_subject,email_body)
                    VALUES(1,1,1,'First','Body'),(2,1,1,'Second','Body');
                """
            )
        os.environ["DATABASE_URL"] = "sqlite:///" + str(legacy_path)
        await init_db()
        legacy_db = await get_db()
        readiness = await _campaign_readiness(legacy_db, 1)
        assert not readiness["ready"] and any("duplicate" in issue.lower() for issue in readiness["issues"])
        await legacy_db.execute("INSERT INTO users(id,email,is_active) VALUES(1,'legacy@yale.edu',1)")
        await legacy_db.execute(
            "UPDATE campaigns SET owner_user_id=1,sender_user_id=1,status='releasing' WHERE id=1"
        )
        await legacy_db.commit()
        assert await _claim_pending_rows(legacy_db, 1, 5, 1) == ([], [])
        quarantined = await (await legacy_db.execute(
            "SELECT status FROM campaigns WHERE id=1"
        )).fetchone()
        assert quarantined["status"] == "needs_attention"
        try:
            await legacy_db.execute(
                "INSERT INTO campaign_contacts(campaign_id,contact_id,email_subject,email_body) VALUES(1,1,'Third','Body')"
            )
            raise AssertionError("Expected the migration trigger to prevent another duplicate")
        except sqlite3.IntegrityError:
            pass
        finally:
            await legacy_db.close()


def test_outreach_pipeline() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    test_outreach_pipeline()
    print("outreach pipeline: ok")
