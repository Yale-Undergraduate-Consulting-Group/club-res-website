"""Which Barclays: the entity and country gate, and the speed fixes beside it.

The run this guards against (Barclays, target UK): 13.6s, 3 saved, none
correct. "Barclays Global Service Centre Private Limited" profiles passed
because they say "Barclays", addresses were built on barclays.bank.in, a
broker listing's "West London" became a person, SEC Form 4 owners such as
"Plc Barclays" were stored as people, and a spent Tavily plan read as
"Large-company sites rarely publish person emails".
No network: every search, crawl, SEC, MX and model call is replaced.
From backend/: python3 tests/test_entity_gate.py"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "p-entity-gate-secret-not-a-known-default-xx"
os.environ["ROSTER_SEC_PAUSE_SEC"] = "0"
os.environ.pop("TAVILY_API_KEY", None)

import httpx  # noqa: E402

from app.database import get_db, init_db  # noqa: E402
from app.services import contact_scraper as CS  # noqa: E402
from app.services import llm  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services import web_contact_discovery as W  # noqa: E402
from app.services import yucgoutreach_discovery as D  # noqa: E402

GSC = "Barclays Global Service Centre Private Limited"
BARCLAYS = {
    "legal_name": "Barclays PLC", "display_name": "Barclays", "brand_words": ["barclays"],
    "hq_country": "GB", "hq_city": "London", "mail_domain": "barclays.com", "mail_domain_evidence": 12,
    "alt_mail_domains": [{"domain": "barclays.co.uk", "country": "GB"},
                         {"domain": "barclays.bank.in", "country": "IN"}],
    "exclude": [
        {"name": GSC, "country": "IN", "domains": [], "kind": "captive"},
        {"name": "Barclays Bank India", "country": "IN", "domains": ["barclays.bank.in"], "kind": "subsidiary"},
    ],
    "target_country": "GB", "source": "test",
}

# Eight search results, as the web stage receives them: (label, result, email).
RESULTS = {
    "gsc_india": {"title": f"Priya Sharma - AVP - {GSC} | LinkedIn",
                  "url": "https://in.linkedin.com/in/priya-sharma-1a2b3c",
                  "content": "Pune, Maharashtra, India · AVP at Barclays Global Service Centre."},
    "in_profile_barclays": {"title": "Rahul Verma - Vice President - Barclays | LinkedIn",
                            "url": "https://in.linkedin.com/in/rahul-verma",
                            "content": "Vice President at Barclays."},
    "uk_director": {"title": "James Carter - Director, Barclays · London | LinkedIn",
                    "url": "https://uk.linkedin.com/in/james-carter",
                    "content": "Director, Barclays · London, England, United Kingdom."},
    "no_location": {"title": "Emma Wilson - Managing Director - Barclays | LinkedIn",
                    "url": "https://www.linkedin.com/in/emma-wilson",
                    "content": "Managing Director at Barclays."},
    "pune_snippet": {"title": "Arjun Patel - Assistant Vice President - Barclays | LinkedIn",
                     "url": "https://www.linkedin.com/in/arjun-patel",
                     "content": "Pune, Maharashtra, India · Assistant Vice President at Barclays."},
    "broker_row": {"title": "West London | Christian Rudbeck - Barclays",
                   "url": "https://www.somebroker.co.uk/branches/west-london",
                   "content": "West London | Christian Rudbeck, Mortgage Adviser, formerly of Barclays."},
    "bank_in_email": {"title": "Neha Gupta - Head of Operations - Barclays",
                      "url": "https://example.org/directory/neha-gupta",
                      "content": "Neha Gupta, Head of Operations, Barclays. Email neha.gupta@barclays.bank.in"},
    "us_new_york": {"title": "Michael Brown - Director - Barclays | LinkedIn",
                    "url": "https://www.linkedin.com/in/michael-brown",
                    "content": "New York, New York, United States · Director at Barclays."},
}
EMAILS = {"bank_in_email": "neha.gupta@barclays.bank.in"}


def gate_all(entity: dict) -> dict[str, tuple[bool, str | None]]:
    return {k: W.entity_gate(r["title"], r["content"], r["url"], EMAILS.get(k, ""), entity)
            for k, r in RESULTS.items()}


def gate_target_uk() -> None:
    got = gate_all(BARCLAYS)
    want = {
        "gsc_india": (False, "other_entity"),           # the headline names the captive
        "in_profile_barclays": (False, "other_country"),  # in.linkedin.com
        "uk_director": (True, None),
        "no_location": (True, None),                    # unknown never rejects
        "pune_snippet": (False, "other_country"),
        "broker_row": (True, None),                     # the name check drops it, not this gate
        "bank_in_email": (False, "other_domain"),
        "us_new_york": (False, "other_country"),
    }
    assert got == want, got
    # The captive's acronym alone, and the generic captive wording, when the
    # run is about the parent.
    assert W.entity_gate("Analyst, Barclays GSC", "", "", "", BARCLAYS) == (False, "other_entity")
    assert W.entity_gate("VP, Barclays Global Capability Center", "", "", "", BARCLAYS)[1] == "other_entity"
    # "Shared services" is a job at the parent unless it is a company.
    assert W.entity_gate("Head of Shared Services, Barclays", "London", "", "", BARCLAYS) == (True, None)
    # Addresses: the mail domain and a target-country alternative pass.
    assert W.entity_gate("", "", "", "a.b@barclays.co.uk", BARCLAYS) == (True, None)
    assert W.entity_gate("", "", "", "a.b@uk.barclays.com", BARCLAYS) == (True, None)
    assert W.entity_gate("", "", "", "a.b@gmail.com", BARCLAYS) == (False, "other_domain")


def gate_any_country() -> None:
    got = gate_all(dict(BARCLAYS, target_country="*"))
    # Country checks are off; a sibling entity - its headline or its mail
    # domain - is still not the company, whatever the country.
    assert got["in_profile_barclays"] == (True, None), got
    assert got["pune_snippet"] == (True, None), got
    assert got["bank_in_email"] == (False, "other_domain"), got
    assert got["us_new_york"] == (True, None), got
    assert got["gsc_india"] == (False, "other_entity"), got
    # No profile at all (a run from before the entity profile) keeps everyone.
    assert all(W.entity_gate(r["title"], r["content"], r["url"], EMAILS.get(k, ""),
                             D._entity_from_spec({"company_name": "Barclays"}))
               == (True, None) for k, r in RESULTS.items())


def gate_default_country() -> None:
    """No country chosen: skip only what is known to be wrong. Requiring the
    HQ country by default would drop Barclays' New York bankers; the India
    service centre is caught by its own country and domain."""
    got = gate_all(dict(BARCLAYS, target_country=None))
    assert got["us_new_york"] == (True, None), got
    assert got["uk_director"] == (True, None), got
    assert got["no_location"] == (True, None), got
    assert got["in_profile_barclays"] == (False, "other_country"), got
    assert got["pune_snippet"] == (False, "other_country"), got
    assert got["bank_in_email"] == (False, "other_domain"), got
    assert got["gsc_india"] == (False, "other_entity"), got


def skipped_wording() -> None:
    skipped = {"other_entity": {"count": 26}, "other_country": {"count": 4}}
    chosen = D._skipped_clause(skipped, dict(BARCLAYS, target_country="GB"))
    assert chosen == " · 30 skipped (26 other Barclays entities, 4 outside UK)", chosen
    default = D._skipped_clause(skipped, dict(BARCLAYS, target_country=None))
    assert default == " · 30 skipped (26 other Barclays entities, 4 in IN)", default


def gate_gsc_chosen() -> None:
    gsc = dict(BARCLAYS, legal_name=GSC, display_name="Barclays Global Service Centre",
               mail_domain=None, alt_mail_domains=[], target_country="IN", hq_city="Pune")
    got = gate_all(gsc)
    assert got["gsc_india"] == (True, None), got      # the chosen entity is not excluded
    assert got["in_profile_barclays"] == (True, None), got
    assert got["pune_snippet"] == (True, None), got
    assert got["uk_director"] == (False, "other_country"), got
    assert got["bank_in_email"] == (False, "other_domain"), got   # still an excluded entity's domain


def names_and_verdicts() -> None:
    for junk in ("West London", "Plc Barclays", "Central London Team", "Fargo Municipal Capital Strategies Llc Wells",
                 "Matrix Holdings LLC", "North Region", "Harrow Branch"):
        assert not CS.looks_like_person_name(junk), junk
    for person in ("Christian Rudbeck", "Kanye West", "Jack London", "James Carter"):
        assert CS.looks_like_person_name(person), person
    # Registers list five-word names; ingestion only refuses entities and places.
    assert not CS.names_entity_or_place("De Moraes Pedro Luiz Bodin")
    assert CS.names_entity_or_place("Plc Barclays") and CS.names_entity_or_place("West London")
    # "suspicious" is saved, but not as verified.
    assert D._is_verified({"email_verification_status": "valid", "ai_verdict": "suspicious"}) == 0
    assert D._is_verified({"email_verification_status": "valid", "ai_verdict": "real"}) == 1
    # SEC: primaryDocument is the XML; no guessed names first.
    assert R._xml_candidates("xslF345X05/wk-form4_1700000000.xml") == ["wk-form4_1700000000.xml"]
    assert R._xml_candidates("form4.htm") == ["ownership.xml", "primary_doc.xml"]


async def search_errors_and_shared_queries() -> None:
    def tavily(request: httpx.Request) -> httpx.Response:
        return httpx.Response(432, text='{"detail":"This request exceeds your plan\'s set usage limit."}')

    real_client = httpx.AsyncClient
    with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-test"}), \
         patch("app.services.web_fetch.web_search_configured", lambda: False), \
         patch.object(W.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(tavily), **kw)):
        run, token = W.begin_search_run()
        try:
            assert await W._tavily_parallel(["a", "b"]) == []
        finally:
            W.end_search_run(token)
    summary = W.search_errors_summary(run)
    assert summary["count"] == 2 and summary["all_failed"] and summary["limit_reached"], summary
    assert "432" in summary["first_message"], summary
    assert D._search_unavailable(summary) == "web search unavailable (limit reached)"
    assert W.search_errors_summary({"queries": 3, "failed": 0}) is None

    # Two stages asking the same query in one run share one request.
    calls: list[str] = []

    async def once(query, max_results):
        calls.append(query)
        await asyncio.sleep(0.05)
        return [{"title": "t", "url": "u", "content": ""}]

    with patch.object(W, "_search_once", once):
        run, token = W.begin_search_run()
        try:
            a, b = await asyncio.gather(W._tavily_search("q", 8), D._tavily_search("q", 8))
        finally:
            W.end_search_run(token)
        assert a == b and calls == ["q"], calls
        await W._tavily_search("q", 8)   # outside a run: no sharing
        assert calls == ["q", "q"], calls


async def crawl_stops_on_catch_all() -> None:
    home = "<html><body>Welcome to Barclays</body></html>"
    asked: list[str] = []

    def catch_all(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, text=home)

    real_client = httpx.AsyncClient
    with patch.object(CS.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(catch_all), **kw)):
        rows = await CS._scrape_contacts_from_domain_html("barclays.com", "Barclays")
    assert rows == [], rows
    # The homepage and one wave of five; the other five paths are not asked.
    assert len(asked) == 1 + CS.CRAWL_FETCH_WORKERS, asked

    # A real team page is still read, and the pages go out together.
    team = ('<html><body><div>Jane Doe - Head of Operations - jane.doe@acme-widgets.com</div>'
            '</body></html>')
    asked.clear()

    async def site(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        await asyncio.sleep(0.1)
        if request.url.path == "/team":
            return httpx.Response(200, text=team)
        if request.url.path == "/":
            return httpx.Response(200, text="<html>home</html>")
        return httpx.Response(404, text="missing")

    with patch.object(CS.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(site), **kw)):
        started = time.monotonic()
        rows = await CS._scrape_contacts_from_domain_html("acme-widgets.com", "Acme Widgets")
        elapsed = time.monotonic() - started
    assert any(r["email"] == "jane.doe@acme-widgets.com" for r in rows), rows
    assert len(asked) == 11, asked
    assert elapsed < 0.1 * 5, f"pages were fetched one by one: {elapsed:.2f}s"


def slot_wait() -> None:
    """A busy slot is waited for, up to SLOT_WAIT_SEC, then 429 as before."""
    from fastapi import HTTPException

    reply = {"output": {"message": {"content": [{"text": "ok"}]}}, "usage": {}}
    with patch("boto3.client") as factory, \
         patch("app.services.generation_policy.reserve_bedrock_invocation", return_value=1), \
         patch("app.services.generation_policy.complete_bedrock_invocation"), \
         patch.object(llm, "SLOT_WAIT_SEC", 0.3):
        factory.return_value.converse.return_value = reply
        for _ in range(llm.INFERENCE_SLOTS):
            assert llm._inference_slots.acquire(blocking=False)
        try:
            started = time.monotonic()
            try:
                llm._bedrock_text("brief", llm.default_model_id(), None)
                raise AssertionError("ran without a slot")
            except HTTPException as exc:
                assert exc.status_code == 429
            assert time.monotonic() - started >= 0.25, "gave up without waiting"
            # A slot that frees during the wait is taken.
            threading.Timer(0.1, llm._inference_slots.release).start()
            assert llm._bedrock_text("brief", llm.default_model_id(), None) == "ok"
        finally:
            for _ in range(llm.INFERENCE_SLOTS - 1):
                llm._inference_slots.release()


async def _insert_run(run_id: int, entity: dict | None) -> None:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, status, lease_token, research_json)
               VALUES (?, 1, 'Barclays', NULL, 'running', ?, ?)""",
            (run_id, f"lease-{run_id}", json.dumps({"title_hints": "", **({"entity": entity} if entity else {})})),
        )
        await db.commit()
    finally:
        await db.close()


async def _run_row(run_id: int) -> dict:
    db = await get_db()
    try:
        return dict(await (await db.execute(
            "SELECT status, progress_message, research_json FROM yucgoutreach_discovery_runs WHERE id=?", (run_id,)
        )).fetchone())
    finally:
        await db.close()


async def barclays_run_end_to_end() -> None:
    await _insert_run(11, BARCLAYS)

    async def search(query, max_results=8, **_kw):
        return list(RESULTS.values())

    # What the roster held before ingestion checked names, plus a person at
    # the Indian bank's domain.
    async def roster_rows(company, domain, **_kw):
        return [
            {"name": "Plc Barclays", "email": "plc.barclays@barclays.com", "title": "10% owner",
             "company": "Barclays", "company_domain": "barclays.com", "contact_source": "roster_sec",
             "discovery_context": "club roster · sec_form4"},
            {"name": "West London", "email": "west.london@barclays.com", "title": "Christian Rudbeck",
             "company": "Barclays", "company_domain": "barclays.com", "contact_source": "roster_cache",
             "discovery_context": "club roster · discovery"},
            {"name": "Neha Gupta", "email": "neha.gupta@barclays.bank.in", "title": "Head of Operations",
             "company": "Barclays", "company_domain": "barclays.bank.in", "contact_source": "roster_cache",
             "discovery_context": "club roster · discovery"},
        ]

    async def verify(rows, **_kw):
        return [dict(r, ai_verdict="real", email_verification_status="valid") for r in rows], []

    seeds_reading = {"people": [
        {"full_name": "Priya Sharma", "title": "AVP", "linkedin_url": "https://in.linkedin.com/in/priya-sharma-1a2b3c"},
        {"full_name": "James Carter", "title": "Director", "linkedin_url": "https://uk.linkedin.com/in/james-carter"},
    ]}
    remember = AsyncMock()
    token = D._active_lease.set("lease-11")
    try:
        with patch("app.services.company_email_cache.discover_company_domain",
                   AsyncMock(side_effect=AssertionError("looked up again after the resolver"))), \
             patch.object(D, "scrape_contacts_from_domain", AsyncMock(return_value=[])), \
             patch.object(W, "_tavily_search", search), \
             patch.object(D, "_llm_json", AsyncMock(return_value=seeds_reading)), \
             patch.object(D, "_company_meta", AsyncMock(return_value={})), \
             patch.object(D, "run_contact_verify_pipeline", verify), \
             patch("app.services.web_fetch.web_search_configured", lambda: True), \
             patch.object(RE, "cached_roster_contacts", roster_rows), \
             patch.object(RE, "roster_refresh_note", AsyncMock(return_value="")), \
             patch.object(R, "remember_discovery_people", remember):
            await D.execute_yucgoutreach_run(11)
    finally:
        D._active_lease.reset(token)

    db = await get_db()
    try:
        saved = [dict(r) for r in await (await db.execute(
            "SELECT first_name, last_name, email, verified FROM yucgoutreach_prospects WHERE run_id=11"
        )).fetchall()]
    finally:
        await db.close()
    emails = {r["email"] for r in saved}
    assert emails == {"james.carter@barclays.com", "emma.wilson@barclays.com"}, saved
    assert not any(e.endswith(".bank.in") for e in emails)
    assert not any(r["first_name"] == "West" for r in saved)

    row = await _run_row(11)
    assert row["status"] == "completed", row
    skipped = json.loads(row["research_json"])["skipped"]
    counts = {k: v["count"] for k, v in skipped.items()}
    assert counts == {"other_entity": 1, "other_country": 3, "other_domain": 1, "not_a_person": 2}, skipped
    assert skipped["other_entity"]["examples"] == ["Priya Sharma"], skipped
    assert set(skipped["other_country"]["examples"]) == {"Rahul Verma", "Arjun Patel", "Michael Brown"}, skipped
    assert set(skipped["not_a_person"]["examples"]) == {"Plc Barclays", "West London"}, skipped
    assert row["progress_message"] == (
        "Done — 2 saved · 7 skipped (1 other Barclays entities, 3 outside UK, "
        "1 at other mail domains, 2 not people)"
    ), row["progress_message"]
    remembered = remember.await_args.args[2]
    assert {c["name"] for c in remembered} == {"James Carter", "Emma Wilson"}, remembered


async def slow_roster_does_not_hold_the_run() -> None:
    await _insert_run(12, None)
    finished = asyncio.Event()

    async def slow_refresh(company, domain, **_kw):
        await asyncio.sleep(0.6)
        finished.set()
        return []

    token = D._active_lease.set("lease-12")
    try:
        with patch("app.services.company_email_cache.discover_company_domain", AsyncMock(return_value="")), \
             patch.object(D, "ROSTER_STAGE_TIMEOUT_SEC", 0.1), \
             patch.object(D, "discover_contacts_from_web", AsyncMock(return_value=[])), \
             patch.object(D, "_tavily_name_seeds", AsyncMock(return_value=[])), \
             patch.object(D, "_company_meta", AsyncMock(return_value={})), \
             patch("app.services.web_fetch.web_search_configured", lambda: True), \
             patch.object(RE, "cached_roster_contacts", AsyncMock(return_value=[])), \
             patch.object(RE, "refresh_roster_on_demand", slow_refresh), \
             patch.object(RE, "roster_refresh_note", AsyncMock(return_value="")):
            started = time.monotonic()
            await D.execute_yucgoutreach_run(12)
            elapsed = time.monotonic() - started
            assert elapsed < 0.5, f"the run waited for the roster: {elapsed:.2f}s"
            assert (await _run_row(12))["status"] == "completed"
            # The refresh was not cancelled: it finishes for the next search.
            assert D._BACKGROUND and not finished.is_set()
            await asyncio.wait_for(finished.wait(), 2)
            await asyncio.sleep(0)
            assert not D._BACKGROUND
    finally:
        D._active_lease.reset(token)


async def main() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()
    gate_target_uk()
    gate_any_country()
    gate_default_country()
    skipped_wording()
    gate_gsc_chosen()
    names_and_verdicts()
    await search_errors_and_shared_queries()
    await crawl_stops_on_catch_all()
    slot_wait()
    await barclays_run_end_to_end()
    await slow_roster_does_not_hold_the_run()


if __name__ == "__main__":
    asyncio.run(main())
    print("entity gate: ok")
