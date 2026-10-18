"""Cache-first roster reads, derived work emails, and the bounce→pattern loop.
From backend/: python3 tests/test_roster_email.py"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "p-roster-email-secret-not-a-known-default-xx"
os.environ["ROSTER_SEC_PAUSE_SEC"] = "0"
os.environ["ROSTER_WEB_ON_ENROLL"] = "0"
os.environ["ROSTER_EMAIL_RECHECK_DAYS"] = "30"
os.environ.pop("TAVILY_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services.company_email_cache import (  # noqa: E402
    get_domain_patterns,
    load_reconcile_context,
    record_verified_sample,
)
from app.services.contact_intelligence import ingest_contact  # noqa: E402
from app.services.contact_scraper import strict_email_name_alignment  # noqa: E402
from app.services.contact_verify_pipeline import _resolve_identity  # noqa: E402

FORM4_PEMBLE = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>PEMBLE CLIFTON A</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>true</isDirector><isOfficer>true</isOfficer>
      <officerTitle>President and Chief Executive Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
</ownershipDocument>
"""
FORM4_WANG = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>WANG CHENG-WEI</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>false</isDirector><isOfficer>true</isOfficer>
      <officerTitle>General Manager - Garmin Corp. Division</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
</ownershipDocument>
"""
TICKERS = {"0": {"cik_str": 1121788, "ticker": "GRMN", "title": "GARMIN LTD"}}


async def _http(url: str) -> bytes:
    if "company_tickers" in url:
        return json.dumps(TICKERS).encode()
    if "submissions" in url:
        return json.dumps(
            {
                "filings": {
                    "recent": {
                        "form": ["4", "4"],
                        "accessionNumber": ["0001193125-26-000001", "0001193125-26-000002"],
                        "primaryDocument": ["ownership.xml", "ownership.xml"],
                    }
                }
            }
        ).encode()
    if "000119312526000001" in url:
        return FORM4_PEMBLE.encode()
    if "000119312526000002" in url:
        return FORM4_WANG.encode()
    raise AssertionError(url)


def async_fake_mx(ok: bool):
    async def _mx(domain: str, cache=None):
        return (ok, ["mx.example.invalid"] if ok else [])
    return _mx


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_row_email_contract() -> None:
    # Role inboxes and misaligned locals are refused even if a source claims them.
    assert RE._row_email("Ada Lovelace", "garmin.com", "support@garmin.com") is None
    assert RE._row_email("Ada Lovelace", "garmin.com", "jane.doe@garmin.com") is None
    assert RE._row_email("Ada Lovelace", "garmin.com", "ada.lovelace@garmin.com") == "ada.lovelace@garmin.com"

    async def _check() -> None:
        assert await RE.derive_roster_email("Madonna", "garmin.com") is None
        assert await RE.derive_roster_email("Ada Lovelace", "") is None

        async def _aligned(name: str, dom: str) -> str:
            return "clifton.pemble@garmin.com"

        with patch.object(RE, "_build_email", _aligned):
            assert await RE.derive_roster_email("Clifton A. Pemble", "garmin.com") == "clifton.pemble@garmin.com"

        async def _junk(name: str, dom: str) -> str:
            return "support@apple.com"

        with patch.object(RE, "_build_email", _junk):
            assert await RE.derive_roster_email("Clifton A. Pemble", "apple.com") is None

    asyncio.run(_check())


async def _core() -> None:
    R._TICKERS = None
    R._TICKERS_AT = None
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()

    # Cold path: SEC-refresh on demand + email minting in one go. Every minted
    # address must be name-aligned (the app's global first.last default proves it).
    with patch.object(R, "_http_get", side_effect=_http), patch.object(RE, "get_mx_cached", async_fake_mx(True)):
        rows = await RE.refresh_roster_on_demand("Garmin", "garmin.com")
    names = {row["name"] for row in rows}
    assert "Clifton A. Pemble" in names and "Cheng-Wei Wang" in names, rows
    assert all(row["contact_source"] == "roster_sec" for row in rows)
    assert all(row["company_domain"] == "garmin.com" for row in rows)
    pemble = next(row for row in rows if "Pemble" in row["name"])
    assert pemble["email"] == "clifton.pemble@garmin.com"
    assert all(strict_email_name_alignment(row["name"], row["email"]) for row in rows if row["email"])

    from app.services.contact_merge import merge_contacts

    nameless = [dict(row, email="") for row in rows]
    merged = merge_contacts(nameless, "Garmin", "garmin.com")
    assert {m["name"] for m in merged} >= {"Clifton A. Pemble", "Cheng-Wei Wang"}
    assert all(m.get("email") for m in merged)
    assert (await RE.roster_refresh_note("Garmin", "garmin.com")).startswith("public")

    # Warm path: cache reads never touch SEC.
    with patch.object(R, "_http_get", side_effect=AssertionError("SEC hit on warm path")):
        warm = await RE.cached_roster_contacts("Garmin", "garmin.com")
    assert {row["name"] for row in warm} == names
    with patch.object(R, "_http_get", side_effect=AssertionError("SEC hit on alias warm path")):
        alias = await RE.cached_roster_contacts("Garmin Ltd.", "garmin.com")
    assert {row["name"] for row in alias} == names

    # Identity gate contract: a bare cache row is not a prospect (no email, no
    # minting in the pipeline); an aligned row passes untouched; a role inbox
    # hijacking a real person's row is rebuilt to the learned-pattern address.
    await record_verified_sample("garmin.com", "ada.lovelace@garmin.com", "Ada Lovelace", source="domain_scrape")
    ctx = await load_reconcile_context({"garmin.com"})
    bare = dict(pemble, email="")
    assert _resolve_identity(bare, company_name="Garmin", domain="garmin.com", ctx=ctx) is None
    aligned = _resolve_identity(pemble, company_name="Garmin", domain="garmin.com", ctx=ctx)
    assert aligned is not None and aligned[1] == "clifton.pemble@garmin.com" and not aligned[3]
    hijacked = dict(pemble, email="support@garmin.com")
    rebuilt_row = _resolve_identity(hijacked, company_name="Garmin", domain="garmin.com", ctx=ctx)
    assert rebuilt_row is not None and rebuilt_row[1] == "clifton.pemble@garmin.com" and rebuilt_row[3]

    # Drain is scheduled out after the on-demand pass.
    idle = await RE.drain_roster_emails()
    assert idle["claimed"] == 0, idle

    # Maintenance sweep: an expired roster with a lost email gets re-minted
    # against the learned pattern; a mail-dead domain does NOT mint.
    roster = (await R.list_rosters(q="Garmin"))[0]
    db = await get_db()
    try:
        await db.execute(
            """UPDATE company_roster_people SET inferred_email=NULL, email_status=NULL, email_checked_at=NULL
               WHERE roster_id=?""",
            (int(roster["id"]),),
        )
        await db.execute("UPDATE company_rosters SET next_email_check_at='' WHERE id=?", (int(roster["id"]),))
        await db.commit()
    finally:
        await db.close()
    with patch.object(RE, "get_mx_cached", async_fake_mx(True)):
        sweep = await RE.drain_roster_emails()
    assert sweep["claimed"] >= 1 and sweep["touched"] >= 2, sweep
    full = await R.roster_detail(int(roster["id"]))
    by_name = {p["full_name"]: p for p in full["people"]}
    assert by_name["Clifton A. Pemble"]["email_status"] == "mx_valid"
    assert by_name["Clifton A. Pemble"]["inferred_email"] == "clifton.pemble@garmin.com"
    warm2 = await RE.cached_roster_contacts("Garmin", "garmin.com")
    pemble2 = next(row for row in warm2 if "Pemble" in row["name"])
    assert pemble2["email"] == "clifton.pemble@garmin.com"
    # Mail-dead domain: addresses cleared, people stay name-only.
    db = await get_db()
    try:
        await db.execute(
            """UPDATE company_roster_people SET inferred_email=NULL, email_status=NULL, email_checked_at=NULL
               WHERE roster_id=?""",
            (int(roster["id"]),),
        )
        await db.execute("UPDATE company_rosters SET next_email_check_at='' WHERE id=?", (int(roster["id"]),))
        await db.commit()
    finally:
        await db.close()
    with patch.object(RE, "get_mx_cached", async_fake_mx(False)):
        dead = await RE.drain_roster_emails()
    assert dead["claimed"] >= 1
    full = await R.roster_detail(int(roster["id"]))
    current = [p for p in full["people"] if p["employment"] == "current"]
    assert current and all(p["email_status"] == "invalid_domain" for p in current)
    assert all(not p["inferred_email"] for p in current)

    # Restore an aligned address, then run the bounce loop: a permanent failure
    # decays the learned pattern exactly once and tombstones the roster person,
    # who then drops out of cache reads.
    with patch.object(RE, "get_mx_cached", async_fake_mx(True)):
        await RE._ensure_emails(dict(roster))
    patterns = await get_domain_patterns("garmin.com")
    before = float(next(p for p in patterns if p["pattern_key"] == "first.last")["confidence"])
    db = await get_db()
    try:
        # ingest_contact demotes inferred origins without a prior published sample.
        await db.execute(
            """INSERT INTO email_pattern_samples(company_domain,email,pattern_key,source_url,observed_at,provenance)
               VALUES('garmin.com','ada.lovelace@garmin.com','first.last','https://www.garmin.com/about/',?,'domain_scrape')
               ON CONFLICT(company_domain,email) DO NOTHING""",
            (_now_iso(),),
        )
        snap = await ingest_contact(
            db,
            contact={
                "name": "Clifton A. Pemble",
                "email": "clifton.pemble@garmin.com",
                "company": "Garmin",
                "company_domain": "garmin.com",
                "title": "President and Chief Executive Officer",
                "email_pattern": "first.last",
            },
            actor_id=1,
            origin="inferred_from_published_pattern",
        )
        await db.commit()
        cand_id = int(snap["candidate_id"])
        await RE.apply_mailbox_proof(db, cand_id, "permanent_failure_observed")
        # record_mailbox_event then adds the check row + suppression; a second
        # failure event for the same address must not decay the pattern again.
        await db.execute(
            "INSERT INTO email_checks(candidate_id,actor_id,verifier,check_type,result,reason,source_ids_json,checked_at,cost_units) VALUES (?,1,'message_tracking','message_tracking','permanent_failure_observed','550 5.1.1','[]',?,0)",
            (cand_id, _now_iso()),
        )
        await db.execute(
            """INSERT INTO candidate_suppressions(email,state,observed_at)
               VALUES ('clifton.pemble@garmin.com','permanent_failure',?)""",
            (_now_iso(),),
        )
        await RE.apply_mailbox_proof(db, cand_id, "permanent_failure_observed")
        await db.commit()
    finally:
        await db.close()
    patterns = await get_domain_patterns("garmin.com")
    after = float(next(p for p in patterns if p["pattern_key"] == "first.last")["confidence"])
    assert after < before
    assert abs((before - 0.12) - after) < 1e-9, (before, after)  # exactly one -0.12 tick
    full = await R.roster_detail(int(roster["id"]))
    assert next(p for p in full["people"] if p["full_name"] == "Clifton A. Pemble")["email_status"] == "bounced"
    warm3 = await RE.cached_roster_contacts("Garmin", "garmin.com")
    assert "Clifton A. Pemble" not in {row["name"] for row in warm3}, "bounced person drops from cache"
    assert "Cheng-Wei Wang" in {row["name"] for row in warm3}

    # Reply up-weights the same pattern (different proof, not deduped by failure).
    db = await get_db()
    try:
        await RE.apply_mailbox_proof(db, cand_id, "human_reply_observed")
        await db.commit()
    finally:
        await db.close()
    patterns = await get_domain_patterns("garmin.com")
    up = float(next(p for p in patterns if p["pattern_key"] == "first.last")["confidence"])
    assert up > after


def test_roster_email_core() -> None:
    asyncio.run(_core())


if __name__ == "__main__":
    test_row_email_contract()
    test_roster_email_core()
    print("ok")
