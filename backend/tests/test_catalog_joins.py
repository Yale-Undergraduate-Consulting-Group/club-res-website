"""Last-send join + Studio draft attach. From backend/: python3 tests/test_catalog_joins.py"""
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
os.environ["JWT_SECRET"] = "p-catalog-test-secret-not-a-known-default-xx"

from app.database import get_db, init_db  # noqa: E402
from app.models import CampaignContactAdd  # noqa: E402
from app.routers.campaigns import add_contacts_to_campaign  # noqa: E402
from app.routers.contacts import companies_summary, list_contacts  # noqa: E402
from app.routers.emails import _upsert_contact_by_email  # noqa: E402


async def _seed() -> None:
    os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email) VALUES (1, 'a@yale.edu')")
        await db.execute("INSERT INTO users (id, email) VALUES (2, 'b@yale.edu')")
        await db.execute(
            "INSERT INTO contacts (id, name, email, company, company_domain) VALUES (1, 'Ada', 'ada@acme.com', 'Acme', 'acme.com')"
        )
        await db.execute(
            "INSERT INTO contacts (id, name, email, company, company_domain) VALUES (2, 'Ben', 'ben@acme.com', 'Acme', 'acme.com')"
        )
        await db.execute("INSERT INTO campaigns (id, name, status, owner_user_id, sender_user_id) VALUES (1, 'Week 1', 'draft', 1, 1)")
        await db.execute(
            """INSERT INTO campaign_contacts
               (campaign_id, contact_id, email_subject, email_body, status, sent_at, sent_by_user_id)
               VALUES (1, 1, 'Hi', 'Body', 'sent', '2026-09-01 12:00:00', 1)"""
        )
        await db.execute(
            """INSERT INTO generated_emails (user_id, contact_id, subject, body, campaign_id)
               VALUES (1, 2, 'Draft subj', 'Draft body', NULL)"""
        )
        await db.execute("INSERT INTO campaigns (id, name, status, owner_user_id, sender_user_id) VALUES (2, 'Other sender', 'sent', 2, 2)")
        await db.execute(
            """INSERT INTO campaign_contacts
               (campaign_id, contact_id, email_subject, email_body, status, sent_at, sent_by_user_id)
               VALUES (2, 1, 'Other', 'Body', 'sent', '2026-09-02 12:00:00', 2)"""
        )
        await db.commit()
    finally:
        await db.close()


async def _run() -> None:
    await _seed()
    page = await list_contacts(user={"id": 1, "role": "standard"})
    by_email = {row["email"]: row for row in page["items"]}
    assert by_email["ada@acme.com"]["last_campaign_name"] == "Week 1"
    assert by_email["ada@acme.com"]["last_send_status"] == "sent"
    assert by_email["ada@acme.com"]["last_sent_at"]
    assert by_email["ben@acme.com"]["last_campaign_name"] is None

    companies = await companies_summary(user={"id": 1, "role": "standard"})
    acme = next(c for c in companies if (c["company"] or "").lower() == "acme")
    assert acme["contact_count"] == 2
    assert acme["campaign_count"] == 1
    assert acme["last_sent_at"]

    out = await add_contacts_to_campaign(
        1, CampaignContactAdd(contact_ids=[2]), user={"id": 1, "role": "standard"}
    )
    assert out["drafts_attached"] == 1
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT email_subject, email_body FROM campaign_contacts WHERE contact_id = 2"
        )
        row = await cur.fetchone()
        assert row["email_subject"] == "Draft subj"
        assert row["email_body"] == "Draft body"
        cur = await db.execute("SELECT campaign_id FROM generated_emails WHERE contact_id = 2")
        ge = await cur.fetchone()
        assert ge["campaign_id"] == 1
        cid = await _upsert_contact_by_email(
            db, email="ada@acme.com", name="Ada Lovelace", title="CEO", company="Acme"
        )
        assert cid == 1
        cid2 = await _upsert_contact_by_email(
            db, email="new@acme.com", name="New", title="PM", company="Acme"
        )
        assert cid2 != 1
        await db.commit()
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(_run())
    print("ok")
