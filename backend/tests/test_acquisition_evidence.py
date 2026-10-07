"""Every acquisition path emits one evidence record; provider stays off in CI.
From backend/: python3 tests/test_acquisition_evidence.py"""
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
os.environ["JWT_SECRET"] = "p-acquisition-evidence-secret-not-a-known-default-xx"
# CI contract: the external provider must never be called in tests.
os.environ.pop("VERIFALIA_API_KEY", None)
os.environ["EXTERNAL_EMAIL_VERIFICATION_ENABLED"] = "false"

from app.database import get_db, init_db  # noqa: E402
from app.models import ContactCreate  # noqa: E402
from app.routers.contacts import (  # noqa: E402
    create_contact,
    get_contact,
    list_discovery_log,
)
from app.routers.yucgoutreach import import_run_to_contacts  # noqa: E402
from fastapi import HTTPException  # noqa: E402

MAILBOX_STATES = {
    "not_checked", "bad_syntax", "domain_has_no_mail_route", "mail_route_available",
    "provider_high_confidence", "provider_medium_confidence", "accept_all_or_risky",
    "recipient_rejected", "inconclusive", "previously_delivered", "human_reply_observed",
    "permanent_failure_observed",
}
IDENTITY_STATES = {"unreviewed", "plausible", "corroborated", "conflicted", "rejected"}


async def _seed() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email) VALUES (1, 'a@yale.edu')")
        await db.execute("INSERT INTO users (id, email) VALUES (2, 'b@yale.edu')")
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, status, prospects_count)
               VALUES (1, 1, 'Acme', 'acme.com', 'completed', 1)"""
        )
        await db.execute(
            """INSERT INTO yucgoutreach_prospects
               (run_id, first_name, last_name, email, company, contact_url, title,
                qualification_notes, contact_profile_url, score, ai_verdict)
               VALUES (1, 'Ada', 'Lovelace', 'ada.lovelace@acme.com', 'Acme',
                       'https://acme.com/team', 'CTO', 'Ada Lovelace (ada.lovelace@acme.com) leads engineering at Acme',
                       'https://acme.com/team', 9.0, 'good')"""
        )
        await db.commit()
    finally:
        await db.close()


async def _run() -> None:
    await _seed()
    member = {"id": 1, "role": "standard"}
    other = {"id": 2, "role": "standard"}

    # Manual create: user-supplied origin, one evidence snapshot, no provider rows.
    made = await create_contact(
        ContactCreate(name="Ada Lovelace", email="ada@acme.invalid",
                      title="CTO", company="Acme", company_domain="acme.invalid"),
        user=member,
    )
    assert made["evidence"], "create_contact must return an evidence snapshot"
    ev = made["evidence"]
    assert ev["address_origin"] == "user_supplied", ev
    assert ev["identity"] in IDENTITY_STATES, ev
    assert ev["mailbox"] in MAILBOX_STATES, ev
    assert ev["checked_at"], ev

    # Detail endpoint exposes the same evidence only to an allowed member.
    detail = await get_contact(made["id"], user=member)
    assert detail["evidence"]["address_origin"] == "user_supplied"
    try:
        await get_contact(made["id"], user={"id": 99, "role": "standard"})
        raise AssertionError("unrelated member must not read a contact they cannot see")
    except HTTPException as exc:
        # Unassigned contact is shared; a claimed one is not. Owner rules come from
        # require_contact_access and stay outside this test's scope.
        assert exc.status_code in (403, 404)

    # YUCG run import: published source becomes evidence with its URL + excerpt.
    imported = await import_run_to_contacts(1, user=member)
    assert imported["created"] == 1, imported
    yucg_ev = imported["evidence"][0]
    assert yucg_ev["address_origin"] == "published_by_company", yucg_ev
    assert any(s.get("url") == "https://acme.com/team" for s in yucg_ev["sources"]), yucg_ev
    assert any("leads engineering at Acme" in (s.get("excerpt") or "") for s in yucg_ev["sources"]), yucg_ev

    # Re-import converges on the same canonical contact instead of a duplicate row.
    again = await import_run_to_contacts(1, user=member)
    assert again["created"] == 0 and again["updated"] == 1, again
    db = await get_db()
    try:
        n = await (await db.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE email = 'ada.lovelace@acme.com'"
        )).fetchone()
        assert n["n"] == 1, "duplicate import must not create a second contact"
        verifalia = await (await db.execute(
            "SELECT COUNT(*) AS n FROM email_checks WHERE verifier = 'verifalia'"
        )).fetchone()
        assert verifalia["n"] == 0, "disabled provider must leave no check rows"
    finally:
        await db.close()

    # Discovery log ownership: another member cannot read this run's audit trail.
    try:
        await list_discovery_log("run-owned-by-1", user=other)
        raise AssertionError("must not read another member's discovery log")
    except HTTPException as exc:
        assert exc.status_code == 404, exc.status_code


def test_acquisition_paths_share_evidence_contract() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_acquisition_paths_share_evidence_contract()
    print("ok")
