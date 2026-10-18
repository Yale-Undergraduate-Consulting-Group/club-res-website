"""Which company a run is about: its domain, its brand, its SEC entry.

The run this guards against: "Meta Platforms, Inc." resolved to globaldata.com
(the first search hit), saved jeff.kim@globaldata.com as a Meta prospect, kept
that domain on the roster even after the member typed meta.com, rejected
"Engineering Manager at Meta" snippets for lacking "Platforms", and reported
the SEC roster as plain "unmatched" when SEC had answered 403.
No network: every search, MX, SEC and LLM call is replaced.
From backend/: python3 tests/test_company_identity.py"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "p-company-identity-secret-not-a-known-default-xx"
os.environ["ROSTER_SEC_PAUSE_SEC"] = "0"
os.environ.pop("SEC_USER_AGENT", None)
os.environ.pop("TAVILY_API_KEY", None)
os.environ.pop("COMPANIES_HOUSE_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import company_email_cache as CEC  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services import yucgoutreach_discovery as D  # noqa: E402
from app.services.web_contact_discovery import (  # noqa: E402
    _names_the_company,
    _title_names_another_employer,
)

META = "Meta Platforms, Inc."
TICKERS = {
    "0": {"cik_str": 1326801, "ticker": "META", "title": "Meta Platforms, Inc."},
    "1": {"cik_str": 1999111, "ticker": "MTTAF", "title": "Meta Critical Minerals Inc."},
    "2": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "3": {"cik_str": 1418121, "ticker": "APLE", "title": "Apple Hospitality REIT, Inc."},
}


def brand_matching() -> None:
    # The brand, not the legal name: people write "at Meta".
    assert _names_the_company("Engineering Manager at Meta", META, None)
    assert not _title_names_another_employer("Engineering Manager at Meta", META, None)
    # Whole words: "meta" inside "Metadata" is not the company, by name or by domain.
    assert not _names_the_company("Metadata Engineer", "Meta", None)
    assert not _names_the_company("Metadata Engineer", "Meta", "meta.com")
    assert _title_names_another_employer("Engineer at Metadata Labs", META, "meta.com")
    # Accepted over-match, on purpose (see text_names_brand): a one-word brand
    # matches any page using the word. Other gates still decide the lead.
    assert _names_the_company("Apple Bank for Savings", "Apple Inc.", None)
    # A descriptor that leads the name is the brand, not a suffix.
    assert CEC.company_brand_words("International Paper") == ["international", "paper"]
    assert CEC.company_brand_words("AT&T Inc.") == ["att"]
    assert not _names_the_company("We met at the office", "AT&T Inc.", None)


def domain_must_be_the_company() -> None:
    """A domain that merely starts with the company's first word is someone
    else's site: warner-access.com was stored as Warner Bros. Discovery's."""
    ok = R._domain_matches_company
    assert ok("Warner Bros. Discovery, Inc.", "wbd.com")
    assert ok("Warner Bros. Discovery, Inc.", "warnerbrosdiscovery.com")
    assert not ok("Warner Bros. Discovery, Inc.", "warner-access.com")
    assert ok(META, "meta.com")
    assert not ok(META, "metacritic.com")
    assert ok("Shopify Inc.", "shopify.com")
    assert ok("Garmin Ltd", "garmin-group.com")
    assert ok("ATR", "atr.net")
    assert not ok("ATR", "atrgroup.com")
    assert ok("JPMorgan Chase & Co.", "jpmorganchase.com")


def profile_id_is_not_a_surname() -> None:
    # "maggie-schumann-55937b5a" was saved as Maggie / 55937b5a.
    assert D._split_first_last("Maggie Schumann 55937b5a") == ("Maggie", "Schumann")
    assert D._split_first_last("Maggie 55937b5a") == ("Maggie", "")
    assert D._split_first_last("55937b5a") == ("", "")
    assert D._split_first_last("Jean Bartik") == ("Jean", "Bartik")
    assert D._split_first_last("David King, III") == ("David", "King")
    assert D._split_first_last("Jane Doe PhD") == ("Jane", "Doe")


def headline_becomes_a_title() -> None:
    """Titles as they came back from a live HBO search, before and after."""
    t = D._clean_title
    assert t("Matthew McGowan - Director, Content Strategy & Analysis, HBO Max", "Matthew Mcgowan", "HBO") \
        == "Director, Content Strategy & Analysis, HBO Max"
    assert t("Brennan Dillon - Marketing Ops. Campaign Management | HBO & HBO Max - Warner Bros. Discovery",
             "Brennan Dillon", "HBO") == "Marketing Ops. Campaign Management"
    assert t("Dana Lichtenstein - HBO & HBO Max", "Dana Lichtenstein", "HBO") is None
    assert t("Maggie Schumann", "Maggie Schumann 55937b5a", "HBO") is None
    assert t("Mondy Kermani - Talent Relations Coordinator, HBO & HBO Max - Warner Bros. Discovery | LinkedIn",
             "Mondy Kermani", "HBO") == "Talent Relations Coordinator, HBO & HBO Max"
    assert t("Director of Operations", "Jean Bartik", "A24") == "Director of Operations"
    assert t("", "Jean Bartik", "A24") is None
    assert t("Mike Warwick - Director of", "Mike Warwick", "Shopify") == "Director"


def sec_brand_match() -> None:
    # Two SEC names start with "meta"; the brand picks the one that IS Meta.
    assert R.match_public_company("Meta", TICKERS)["ticker"] == "META"
    assert R.match_public_company(META, TICKERS)["ticker"] == "META"
    assert R.match_public_company("Apple", TICKERS)["ticker"] == "AAPL"
    assert R.match_public_company("Meta Critical Minerals", TICKERS)["ticker"] == "MTTAF"


async def domain_resolver() -> None:
    async def mx_ok(domain, cache=None):
        return (True, ["mx"])

    # Meta is in company_entity's reviewed table, which answers before any
    # search; the search path is pinned on a company the table does not know.
    search = AsyncMock(return_value=[
        {"url": "https://www.globaldata.com/company-profile/globex-platforms-inc/"},
        {"url": "https://www.linkedin.com/company/globex"},
        {"url": "https://www.globex.com/"},
    ])
    with patch.object(CEC, "resolve_company_domain", AsyncMock(return_value="globaldata.com")), \
         patch("app.services.web_fetch.web_search_configured", lambda: True), \
         patch("app.services.web_contact_discovery._tavily_search", search), \
         patch("app.services.email_verifier.get_mx_cached", mx_ok):
        # A vendor host on record is not "known", and a vendor hit is skipped.
        assert await CEC.discover_company_domain("Globex Platforms, Inc.") == "globex.com"
        search.return_value = [{"url": "https://www.globaldata.com/company-profile/globex/"}]
        assert await CEC.discover_company_domain("Globex Platforms, Inc.") == ""
        assert await CEC.discover_company_domain(META) == "meta.com"


async def discovery_run_without_domain() -> None:
    """No verifiable domain: the run goes on with none (never the first hit),
    and its closing summary keeps the per-source counts."""
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, status, lease_token, research_json)
               VALUES (7, 1, ?, NULL, 'running', 'lease-7', ?)""",
            (META, json.dumps({"title_hints": "engineering"})),
        )
        await db.commit()
    finally:
        await db.close()

    seen_domains: list = []

    async def web(company, domain, **_kw):
        seen_domains.append(domain)
        return [{"name": "Ada Lovelace", "title": "Engineering Manager", "company": META,
                 "email": "ada.lovelace@meta.com", "contact_source": "web_discovery",
                 "affiliation_evidence": "linkedin_profile"}]

    async def roster_rows(company, domain, **_kw):
        seen_domains.append(domain)
        return []

    async def verify(rows, **_kw):
        return [dict(r, ai_verdict="real") for r in rows], []

    crawl = AsyncMock(return_value=[])
    token = D._active_lease.set("lease-7")
    try:
        with patch("app.services.company_email_cache.discover_company_domain", AsyncMock(return_value="")), \
             patch.object(D, "scrape_contacts_from_domain", crawl), \
             patch.object(D, "discover_contacts_from_web", web), \
             patch.object(D, "_company_meta", AsyncMock(return_value={})), \
             patch.object(D, "_tavily_name_seeds", AsyncMock(return_value=[])), \
             patch.object(D, "run_contact_verify_pipeline", verify), \
             patch("app.services.web_fetch.web_search_configured", lambda: True), \
             patch.object(RE, "cached_roster_contacts", roster_rows), \
             patch.object(RE, "refresh_roster_on_demand", roster_rows), \
             patch.object(RE, "roster_refresh_note", AsyncMock(return_value="")), \
             patch.object(R, "remember_discovery_people", AsyncMock()):
            await D.execute_yucgoutreach_run(7)
    finally:
        D._active_lease.reset(token)
    crawl.assert_not_called()
    assert seen_domains and all(not d for d in seen_domains), seen_domains
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT status, research_json FROM yucgoutreach_discovery_runs WHERE id=7"
        )).fetchone()
    finally:
        await db.close()
    assert row["status"] == "completed", dict(row)
    research = json.loads(row["research_json"])
    for key in ("web_contacts", "roster_contacts", "domain_contacts", "merged", "saved", "title_hints"):
        assert key in research, research
    assert research["web_contacts"] == 1, research


async def member_domain_replaces_stored() -> None:
    roster = await R._ensure_roster(META, "globaldata.com")
    rid = int(roster["id"])
    await R._upsert_people(rid, [{
        "full_name": "Jeff Kim", "normalized_name": "jeff kim", "title": "VP",
        "role_type": "officer", "source": "sec_form4",
    }], domain="globaldata.com", mark_missing=False)
    db = await get_db()
    try:
        await db.execute(
            "UPDATE company_roster_people SET inferred_email='jeff.kim@globaldata.com', email_status='bounced' WHERE roster_id=?",
            (rid,),
        )
        await db.commit()
    finally:
        await db.close()

    # A looked-up domain does not replace a stored one ...
    await RE.cached_roster_contacts(META, "other.com")
    assert (await R.roster_detail(rid))["company_domain"] == "globaldata.com"
    # ... a domain the member typed does, and the old addresses go with it.
    rows = await RE.cached_roster_contacts(META, "meta.com", pin_domain=True)
    detail = await R.roster_detail(rid)
    assert detail["company_domain"] == "meta.com", detail
    assert all(p["inferred_email"] is None for p in detail["people"]), detail["people"]
    assert rows and all("globaldata" not in (r["email"] or "") for r in rows), rows
    assert all(r["company_domain"] == "meta.com" for r in rows), rows


async def sec_failure_is_recorded() -> None:
    async def forbidden(*_a, **_kw):
        raise RuntimeError("Client error '403 Forbidden' for url 'https://www.sec.gov/files/company_tickers.json'")

    R._TICKERS = None
    with patch.object(R, "load_tickers", forbidden), \
         patch.object(R, "_web_people", AsyncMock(return_value=[])):
        rows = await RE.refresh_roster_on_demand("Blank Widgets Inc", None)
    assert rows == []
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT source_status, last_error FROM company_rosters WHERE company_name='Blank Widgets Inc'"
        )).fetchone()
    finally:
        await db.close()
    assert row["source_status"] != "unmatched", dict(row)
    assert "403" in row["last_error"] and "SEC_USER_AGENT" in row["last_error"], dict(row)
    assert "SEC_USER_AGENT" in await RE.roster_refresh_note("Blank Widgets Inc")


async def resolve_domain_is_strict() -> None:
    """A dot does not make a domain, and a short name does not borrow another
    company's stored domain."""
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO contacts (name, email, company, company_domain) VALUES "
            "('Dana Data', 'dana@metadatasys.com', 'Metadata Systems', 'metadatasys.com'),"
            "('Wes Warner', 'wes@wbd.com', 'Warner Bros. Discovery, Inc.', 'wbd.com')"
        )
        await db.commit()
    finally:
        await db.close()
    # Not a hostname: looked up as a name, matched after dropping "Inc.".
    assert await CEC.resolve_company_domain("Warner Bros. Discovery") == "wbd.com"
    # "Meta" is not "Metadata Systems".
    assert await CEC.resolve_company_domain("Meta") == ""
    # A member-typed domain is taken as given; a name that merely looks like
    # one must accept mail.
    assert await CEC.resolve_company_domain("https://www.meta.com/about") == "meta.com"

    async def no_mx(domain, cache=None):
        return (False, [])

    with patch("app.services.email_verifier.get_mx_cached", no_mx):
        assert await CEC.resolve_company_domain("nomail.example", member_supplied=False) == ""


async def _async_cases() -> None:
    await init_db()
    await resolve_domain_is_strict()
    await domain_resolver()
    await discovery_run_without_domain()
    await member_domain_replaces_stored()
    await sec_failure_is_recorded()


def test_company_identity() -> None:
    brand_matching()
    sec_brand_match()
    domain_must_be_the_company()
    profile_id_is_not_a_surname()
    headline_becomes_a_title()
    asyncio.run(_async_cases())


if __name__ == "__main__":
    test_company_identity()
    print("company identity: ok")
