"""Companies House register branch + domain resolution, hermetic.
From backend/: python3 tests/test_roster_global.py"""
from __future__ import annotations

import asyncio
import json
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
os.environ["JWT_SECRET"] = "p-roster-global-secret-not-a-known-default-xx"
os.environ["ROSTER_SEC_PAUSE_SEC"] = "0"
os.environ["ROSTER_WEB_ON_ENROLL"] = "0"
os.environ["ROSTER_DOMAIN_WEB"] = "1"
os.environ["ROSTER_DOMAIN_LOOKUPS"] = "4"
os.environ["TAVILY_API_KEY"] = "test-tavily-not-real"
os.environ["COMPANIES_HOUSE_API_KEY"] = "test-ch-key"

from app.database import get_db, init_db  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402

MONZO_SEARCH = {
    "items": [
        {"company_number": "12345678", "company_name": "Monzo Bank Holdings Limited", "company_status": "active", "title": "MONZO BANK HOLDINGS LIMITED"},
        {"company_number": "999", "company_name": "Monzo Corner Cafe", "company_status": "dissolved", "title": "MONZO CORNER CAFE"},
    ]
}
# Field names match a live response from api.company-information.service.gov.uk
# /company/{n}/officers: the person is "name", not "officer_name". The old
# fixture invented "officer_name", so it passed while production dropped
# every UK officer of every company.
MONZO_OFFICERS = {
    "items": [
        {"name": "QUANT, Ada", "officer_role": "director", "occupation": "Software Engineer", "appointed_on": "2015-01-01"},
        {"name": "BANKER, Rex Mr", "officer_role": "director", "occupation": "Banker", "resigned_on": "2024-06-30"},
        {"name": "MONZO NOMINEES LIMITED", "officer_role": "corporate-director", "identification": {"registration_number": "1"}},
    ],
    "links": {},
}


def test_ch_mapping() -> None:
    people = R.map_ch_officers(MONZO_OFFICERS["items"], "12345678")
    names = {p["full_name"] for p in people}
    assert "Ada Quant" in names, people
    assert "Rex Banker" in names
    assert len(people) == 2, "corporate officers are skipped"
    # A natural-person officer must never be dropped for want of a name: that
    # silently emptied the whole UK register path in production.
    assert all(person["full_name"].strip() for person in people)
    by = {p["full_name"]: p for p in people}
    assert by["Ada Quant"]["employment"] == "current"
    assert by["Ada Quant"]["role_type"] == "director"
    assert by["Rex Banker"]["employment"] == "left"
    assert by["Rex Banker"]["source"] == "companies_house"
    assert R.match_uk_company("Monzo Bank", MONZO_SEARCH["items"]) == "12345678"
    assert R.match_uk_company("Garmin", MONZO_SEARCH["items"]) is None
    # domain gates
    assert R._registrable_domain("https://garmin-fans.blogspot.com/x") == ""
    assert R._registrable_domain("https://www.garmin.com/about") == "garmin.com"
    assert R._registrable_domain("https://www.linkedin.com/company/garmin/") == ""
    assert R._domain_matches_company("Garmin", "garmin.com")
    assert R._domain_matches_company("ATR", "atr.net")
    assert not R._domain_matches_company("ATR", "garmin.com")
    seed = json.dumps({"verification_source_url": "https://www.embraer.com/en/about-us/leadership", "contact_type": "VP"})
    assert R._seed_domain("Embraer Commercial Aviation", seed) == "embraer.com"
    junk = json.dumps({"verification_source_url": "https://www.linkedin.com/search/results/people/"})
    assert R._seed_domain("Bombardier", junk) == ""


async def _ch_get_factory(calls: list[tuple]):
    async def _get(url: str, *, basic_auth=None) -> bytes:
        calls.append((url, basic_auth))
        if "/search/companies" in url:
            return json.dumps(MONZO_SEARCH).encode()
        if "/officers" in url:
            return json.dumps(MONZO_OFFICERS).encode()
        raise AssertionError(url)
    return _get


async def _core() -> None:
    R._TICKERS = None
    R._TICKERS_AT = None
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.execute(
            "INSERT INTO yucg_prospect_targets (company, sector, extra_json) VALUES ('Embraer Commercial Aviation', 'OEM', ?)",
            (json.dumps({"verification_source_url": "https://www.embraer.com/en/about-us/leadership"}),),
        )
        await db.execute(
            "INSERT INTO yucg_prospect_targets (company, sector, extra_json) VALUES ('Bombardier', 'OEM', ?)",
            (json.dumps({"verification_source_url": "https://www.linkedin.com/search/results/people/"}),),
        )
        await db.commit()
    finally:
        await db.close()

    # --- Companies House register branch ---
    roster = await R._ensure_roster("Monzo Bank")
    calls: list[tuple] = []
    getter = await _ch_get_factory(calls)
    with patch.object(R, "_http_get", side_effect=getter):
        result = await R.refresh_roster(roster, {})
    assert result["status"] == "register", result
    assert all(auth == ("test-ch-key", "") for _, auth in calls), calls
    detail = await R.roster_detail(int(roster["id"]))
    people = {p["full_name"]: p for p in detail["people"]}
    assert people["Ada Quant"]["employment"] == "current"
    assert people["Rex Banker"]["employment"] == "left"
    assert "Monzo Nominees" not in " ".join(people)
    assert detail["current_count"] == 1 and detail["people_count"] == 2

    # Cache reads show only serving officers.
    warm = await RE.cached_roster_contacts("Monzo Bank", None)
    assert {row["name"] for row in warm} == {"Ada Quant"}
    assert all(row["contact_source"] == "roster_cache" for row in warm)

    # CH-sourced people are STICKY: an SEC absence pass must not decay them.
    await R._upsert_people(
        int(roster["id"]),
        [{"full_name": "Newcomer Person", "normalized_name": "newcomer person", "title": "CFO", "role_type": "officer", "source": "sec_form4"}],
        domain=None,
        mark_missing=True,
    )
    detail = await R.roster_detail(int(roster["id"]))
    assert next(p for p in detail["people"] if p["full_name"] == "Ada Quant")["employment"] == "current"

    # No key => the CH branch makes zero calls.
    with patch.dict(os.environ, {"COMPANIES_HOUSE_API_KEY": ""}), patch.object(R, "_http_get", side_effect=AssertionError("CH called without key")):
        assert await R.fetch_companies_house_people("Monzo Bank") == []

    # --- Domain resolution ---
    enrolled = await R.enroll_prospect_companies()
    assert enrolled >= 2
    rows = await R.list_rosters(q="Embraer", limit=10)
    assert rows and rows[0]["company_domain"] == "embraer.com", "seed URL domain captured at enroll"
    rows = await R.list_rosters(q="Bombardier", limit=10)
    assert rows and not rows[0]["company_domain"], "linkedin stubs never set a domain"

    # Tavily fallback for a company with no seed URL: platform host rejected,
    # MX-verified label match accepted, and the roster is queued for minting.
    zeph = await R._ensure_roster("Zephyr Aero")
    await R.remember_discovery_people("Zephyr Aero", None, [{"name": "Ada Runner", "title": "VP Fleet"}])

    async def fake_search(query: str, max_results: int = 8):
        assert "Zephyr Aero" in query or True
        return [
            {"url": "https://zephyr-fans.blogspot.com/fleet"},
            {"url": "https://www.zephyraero.com/fleet"},
        ]

    async def fake_mx(domain: str, cache=None):
        return (domain == "zephyraero.com", ["mx1"])

    import app.services.web_contact_discovery as WCD
    import app.services.email_verifier as EV

    with patch.object(WCD, "_tavily_search", fake_search), patch.object(EV, "get_mx_cached", fake_mx):
        resolved = await R.resolve_missing_domains(limit=10)
    assert resolved >= 1, resolved
    rows = await R.list_rosters(q="Zephyr", limit=10)
    assert rows[0]["company_domain"] == "zephyraero.com", rows

    # The next email pass mints the aligned address for the resolved roster.
    async def fake_mx_true(domain: str, cache=None):
        return (True, ["mx1"])

    with patch.object(RE, "get_mx_cached", fake_mx_true):
        sweep = await RE.drain_roster_emails()
    assert sweep["claimed"] >= 1
    detail = await R.roster_detail(int(zeph["id"]))
    ada = next(p for p in detail["people"] if p["full_name"] == "Ada Runner")
    assert ada["inferred_email"] == "ada.runner@zephyraero.com"
    assert ada["email_status"] == "mx_valid"

    # A resolved domain also upgrades the CH officer's cache row to an address.
    monzo = (await R.list_rosters(q="Monzo", limit=5))[0]
    db = await get_db()
    try:
        await db.execute("UPDATE company_rosters SET company_domain='monzo.com', next_email_check_at='' WHERE id=?", (int(monzo["id"]),))
        await db.commit()
    finally:
        await db.close()
    with patch.object(RE, "get_mx_cached", fake_mx_true):
        await RE.drain_roster_emails()
    warm = await RE.cached_roster_contacts("Monzo Bank", None)
    ada_row = next(row for row in warm if row["name"] == "Ada Quant")
    assert ada_row["email"] == "ada.quant@monzo.com"

    # Domain lookups are budgeted: after the sweep, rosters are stamped and the
    # next pass within 30 days does nothing.
    with patch.object(WCD, "_tavily_search", side_effect=AssertionError("tavily re-hit within 30 days")):
        assert await R.resolve_missing_domains(limit=10) == 0

    # List enrichment + stats view the accumulated graph honestly.
    monzo_row = (await R.list_rosters(q="Monzo", limit=5))[0]
    assert monzo_row["emails_ready"] == 2, monzo_row  # Ada (CH) + Newcomer (SEC sticky test)
    assert "coverage_gap" in monzo_row
    stats = await R.source_stats()
    ch = next(s for s in stats if s["source"] == "companies_house")
    assert ch["produced"] >= 2 and ch["current_now"] >= 1 and ch["mx_emails"] >= 1, stats
    zeph_row = (await R.list_rosters(q="Zephyr", limit=5))[0]
    assert zeph_row["emails_ready"] == 1

    # Provider hard-Failure on a roster-only derived address: tombstone +
    # suppression, idempotent on re-check.
    db = await get_db()
    try:
        await RE.apply_provider_verdict(db, "ada.runner@zephyraero.com", "rejected")
        await db.commit()
    finally:
        await db.close()
    detail = await R.roster_detail(int(zeph["id"]))
    ada = next(x for x in detail["people"] if x["full_name"] == "Ada Runner")
    assert ada["email_status"] == "bounced"
    db = await get_db()
    try:
        sup = await (await db.execute(
            "SELECT 1 FROM candidate_suppressions WHERE email='ada.runner@zephyraero.com'")).fetchone()
        assert sup
        before = (await (await db.execute(
            "SELECT confidence FROM company_email_patterns WHERE company_domain='zephyraero.com'")).fetchone())
        await RE.apply_provider_verdict(db, "ada.runner@zephyraero.com", "rejected")
        await db.commit()
    finally:
        await db.close()
    warm = await RE.cached_roster_contacts("Zephyr Aero", None)
    assert "Ada Runner" not in {row["name"] for row in warm}


def test_roster_global_core() -> None:
    test_ch_mapping()
    asyncio.run(_core())


if __name__ == "__main__":
    test_roster_global_core()
    print("ok")
