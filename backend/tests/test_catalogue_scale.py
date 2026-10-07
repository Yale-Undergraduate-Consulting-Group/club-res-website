"""What the catalogue endpoints promise once the club has a real one.

The failure these guard against was not a wrong answer but an unusable one:
the contacts list joined the whole send ledger to the whole catalogue before
applying LIMIT, so one page took minutes, and the company picker returned
every distinct company in the database.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "catalogue-scale-secret-value-for-tests")

from fastapi import HTTPException  # noqa: E402

from app.database import get_db, init_db  # noqa: E402
from app.routers.campaigns import (  # noqa: E402
    MAX_RECIPIENTS, BuildFromTemplate, build_campaign_from_template,
)
from app.routers.contacts import companies_summary, list_contacts  # noqa: E402

ADMIN = {"id": 1, "role": "admin"}


async def seed(db) -> None:
    await db.execute("INSERT INTO users(id,email,name,role,is_active) VALUES (1,'a@yale.edu','A','admin',1)")
    await db.executemany(
        "INSERT INTO contacts (name,email,company) VALUES (?,?,?)",
        [(f"Person {i}", f"p{i}@acme.com", " Acme Corp ") for i in range(3)]
        + [(f"Other {i}", f"o{i}@beta.com", "Beta Industries") for i in range(2)],
    )
    await db.execute(
        "INSERT INTO campaigns (id,name,status,owner_user_id,sender_user_id) VALUES (1,'First touch','sent',1,1)"
    )
    await db.execute(
        "INSERT INTO campaigns (id,name,status,owner_user_id,sender_user_id) VALUES (2,'Second touch','sent',1,1)"
    )
    # Two sends to the same contact, in two campaigns: the later is reported.
    await db.execute(
        """INSERT INTO campaign_contacts (campaign_id,contact_id,email_subject,email_body,status,sent_at,sent_by_user_id)
           VALUES (1,1,'S','B','sent','2026-01-01 09:00:00',1)"""
    )
    await db.execute(
        """INSERT INTO campaign_contacts (campaign_id,contact_id,email_subject,email_body,status,sent_at,sent_by_user_id)
           VALUES (2,1,'S2','B','bounced','2026-02-02 09:00:00',1)"""
    )
    await db.commit()


async def the_list_page_still_reports_each_contact_s_latest_send() -> None:
    page = await list_contacts(limit=100, user=ADMIN)
    assert page["total"] == 5
    mailed = next(row for row in page["items"] if row["id"] == 1)
    assert mailed["last_sent_at"] == "2026-02-02 09:00:00"
    assert mailed["last_send_status"] == "bounced"
    assert mailed["last_campaign_name"] == "Second touch"
    unmailed = next(row for row in page["items"] if row["id"] == 2)
    assert unmailed["last_sent_at"] is None and unmailed["last_campaign_name"] is None


async def paging_never_repeats_or_skips_a_contact() -> None:
    first = await list_contacts(limit=2, offset=0, user=ADMIN)
    second = await list_contacts(limit=2, offset=2, user=ADMIN)
    third = await list_contacts(limit=2, offset=4, user=ADMIN)
    seen = [row["id"] for page in (first, second, third) for row in page["items"]]
    assert len(seen) == 5 and len(set(seen)) == 5


async def company_filtering_ignores_stored_whitespace_and_case() -> None:
    """Contacts arrive from crawls and spreadsheets with untrimmed names; the
    filter keys on LOWER(TRIM(company)) so a picked company finds its people."""
    page = await list_contacts(companies="acme corp", user=ADMIN)
    assert {row["id"] for row in page["items"]} == {1, 2, 3}


async def the_company_picker_is_searchable_and_bounded() -> None:
    everything = await companies_summary(user=ADMIN)
    assert [row["company"] for row in everything] == ["Acme Corp", "Beta Industries"]
    assert everything[0]["contact_count"] == 3
    assert everything[0]["mailed_count"] == 1
    assert everything[0]["bounced_count"] == 1
    assert everything[0]["campaign_count"] == 2

    searched = await companies_summary(q="beta", user=ADMIN)
    assert [row["company"] for row in searched] == ["Beta Industries"]

    one = await companies_summary(limit=1, user=ADMIN)
    assert len(one) == 1
    assert (await companies_summary(limit=1, offset=1, user=ADMIN))[0]["company"] == "Beta Industries"


async def an_oversized_selection_is_refused_rather_than_silently_trimmed() -> None:
    """The build used to keep the first 500 ids and drop the rest, so a member
    who selected more got a campaign that was not the one they reviewed."""
    payload = BuildFromTemplate(
        contact_ids=list(range(1, MAX_RECIPIENTS + 2)),
        subject="Hi {first}", body="Hello {first}.", preview_only=True,
    )
    try:
        await build_campaign_from_template(payload, ADMIN)
        raise AssertionError("an oversized selection was accepted")
    except HTTPException as exc:
        assert exc.status_code == 422
        assert str(MAX_RECIPIENTS) in str(exc.detail)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'catalogue.db'}"
        await init_db()
        db = await get_db()
        await seed(db)
        await db.close()
        await the_list_page_still_reports_each_contact_s_latest_send()
        await paging_never_repeats_or_skips_a_contact()
        await company_filtering_ignores_stored_whitespace_and_case()
        await the_company_picker_is_searchable_and_bounded()
        await an_oversized_selection_is_refused_rather_than_silently_trimmed()
        print("catalogue scale: ok")


asyncio.run(main())
