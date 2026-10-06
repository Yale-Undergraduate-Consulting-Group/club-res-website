"""Ghost stickiness, derived-email collisions, provider verification queue.
From backend/: python3 tests/test_roster_verify.py"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "p-verify-secret-not-a-known-default-xx"
os.environ["ROSTER_SEC_PAUSE_SEC"] = "0"
os.environ["ROSTER_PROVIDER_ATTEMPTS"] = "10"
os.environ.pop("TAVILY_API_KEY", None)
os.environ.pop("COMPANIES_HOUSE_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402


def person(name: str) -> dict:
    return {
        "full_name": name,
        "normalized_name": " ".join(name.lower().split()),
        "title": "",
        "role_type": "officer",
        "source": "sec_form4",
    }


async def _set_emails(roster_id: int, mapping: dict[str, str]) -> None:
    db = await get_db()
    try:
        for name, email in mapping.items():
            await db.execute(
                "UPDATE company_roster_people SET inferred_email=?, email_status='mx_valid' WHERE roster_id=? AND full_name=?",
                (email, roster_id, name),
            )
        await db.commit()
    finally:
        await db.close()


async def _run() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()

    # ---------- 1) ghost stickiness: weekly SEC refresh must not resurrect ----------
    ghost_roster = await R._ensure_roster("Ghost Corp")
    await R._upsert_people(int(ghost_roster["id"]), [person("Cesar Nivaldo Gon")], domain=None, mark_missing=False)
    db = await get_db()
    try:
        await db.execute(
            "UPDATE company_roster_people SET employment='ghost', verdict='ghost' WHERE roster_id=?",
            (int(ghost_roster["id"]),),
        )
        await db.commit()
    finally:
        await db.close()
    await R._upsert_people(int(ghost_roster["id"]), [person("Cesar Nivaldo Gon")], domain=None, mark_missing=False)
    detail = await R.roster_detail(int(ghost_roster["id"]))
    row = detail["people"][0]
    assert row["employment"] == "ghost", "fresh filing data must not resurrect an adjudicated ghost"
    assert detail["current_count"] == 0

    # ---------- 2) collision guard on derived addresses ----------
    coll = await R._ensure_roster("Salles Co", "salco.com")
    await R._upsert_people(
        int(coll["id"]),
        [person("Salles Pedro Moreira"), person("Salles Joao Moreira")],
        domain="salco.com",
        mark_missing=False,
    )

    async def mx_true(domain: str, cache=None):
        return (True, ["mx"])

    with patch.object(RE, "get_mx_cached", mx_true):
        await RE._ensure_emails(dict(coll))
    detail = await R.roster_detail(int(coll["id"]))
    for p in detail["people"]:
        assert p["inferred_email"] is None and p["email_status"] == "collision", p
    warm = await RE.cached_roster_contacts("Salles Co", "salco.com")
    assert all(not row["email"] for row in warm), "colliding pair must stay address-free"

    # adjudicator renames one to natural order → next pass separates them
    db = await get_db()
    try:
        await db.execute(
            "UPDATE company_roster_people SET full_name='Pedro Moreira Salles', normalized_name='pedro moreira salles', email_status=NULL WHERE roster_id=? AND full_name='Salles Pedro Moreira'",
            (int(coll["id"]),),
        )
        await db.commit()
    finally:
        await db.close()
    with patch.object(RE, "get_mx_cached", mx_true):
        await RE._ensure_emails(dict(coll))
    detail = await R.roster_detail(int(coll["id"]))
    emails = [p["inferred_email"] for p in detail["people"] if p["inferred_email"]]
    assert len(emails) == len(set(emails)), emails
    assert emails, detail["people"]

    # ---------- 3) provider verification queue ----------
    vb = await R._ensure_roster("Verif Bank", "vb.com")
    await R._upsert_people(
        int(vb["id"]),
        [person("Ada One"), person("Bob Two"), person("Cara Three"), person("Dan Four"), person("Eve Five")],
        domain="vb.com",
        mark_missing=False,
    )
    await _set_emails(int(vb["id"]), {
        "Ada One": "ada.one@vb.com",
        "Bob Two": "bob.two@vb.com",
        "Cara Three": "cara.three@vb.com",
        "Dan Four": "dan.four@vb.com",
        "Eve Five": "eve.five@vb.com",
    })

    calls: list[str] = []

    async def fake_assess(email: str, **kwargs):
        calls.append(email)
        if email == "ada.one@vb.com":
            return {"mailbox": "provider_high_confidence", "provider_state": "complete"}
        if email == "bob.two@vb.com":
            db = await get_db()
            try:
                await RE.apply_provider_verdict(db, email, "rejected")
                await db.commit()
            finally:
                await db.close()
            return {"mailbox": "recipient_rejected", "provider_state": "complete"}
        if email == "cara.three@vb.com":
            return {"mailbox": "accept_all_or_risky", "provider_state": "complete"}
        return {"mailbox": "inconclusive", "provider_state": "exhausted"}

    with patch("app.services.contact_intelligence.assess_address", side_effect=fake_assess):
        result = await RE.drain_roster_verification()
    assert result["checked"] == 3 and result["confirmed"] == 1 and result["rejected"] == 1, result
    assert "eve.five@vb.com" not in calls, "exhausted provider must stop the pass (Eve never attempted)"

    detail = {p["full_name"]: p for p in (await R.roster_detail(int(vb["id"])))["people"]}
    assert detail["Ada One"]["email_status"] == "provider_valid"
    assert detail["Bob Two"]["email_status"] == "bounced", "provider rejection tombstones"
    assert detail["Cara Three"]["email_status"] == "catch_all"
    db = await get_db()
    try:
        sup = await (await db.execute(
            "SELECT 1 FROM candidate_suppressions WHERE email='bob.two@vb.com'")).fetchone()
        assert sup, "rejected address must be suppressed on send"
        await db.execute("UPDATE company_roster_people SET email_provider_checked_at=NULL WHERE full_name='Dan Four'")
        await db.commit()
    finally:
        await db.close()

    # exhausted/disabled never stamps — the row stays eligible for tomorrow
    async def disabled_assess(email: str, **kwargs):
        return {"mailbox": "inconclusive", "provider_state": "disabled"}

    with patch("app.services.contact_intelligence.assess_address", side_effect=disabled_assess):
        blocked = await RE.drain_roster_verification()
    assert blocked["checked"] == 0, blocked
    detail = {p["full_name"]: p for p in (await R.roster_detail(int(vb["id"])))["people"]}
    assert detail["Dan Four"]["email_provider_checked_at"] is None

    # second pass with everything decisive already stamped is a no-op
    with patch("app.services.contact_intelligence.assess_address", side_effect=fake_assess):
        again = await RE.drain_roster_verification()
    assert again["checked"] == 0, again


def test_roster_verify() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    test_roster_verify()
    print("ok")
