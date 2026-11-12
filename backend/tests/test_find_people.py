"""Find people: Yale first, a function to look for, and titles from evidence.

- A person is tied to Yale only by words that say so (a school, a class year, a
  degree), never by a lock or an appliance store, and only by the words about
  *them*, not about the next person on the page.
- Yale-tied people are stored and listed first; a listing can be narrowed to one
  function, and a search can ask for one without asking for more searches.
- A missing title is recovered from the snippet or headline already collected,
  and stays blank when the text states none.
- The Yale search lives inside the later rounds' budget.

No network: search, verification, the model and the roster are replaced.
From backend/: python tests/test_find_people.py
"""
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
os.environ["JWT_SECRET"] = "p-find-people-secret-not-a-known-default-xxxx"
os.environ.pop("TAVILY_API_KEY", None)

from fastapi import HTTPException  # noqa: E402

from app.database import get_db, init_db  # noqa: E402
from app.routers.contacts import list_contacts  # noqa: E402
from app.routers.yucgoutreach import import_run_to_contacts, list_prospects  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services import web_contact_discovery as W  # noqa: E402
from app.services import yucgoutreach_discovery as D  # noqa: E402
from app.services.company_register import FUNCTION_IDS, FUNCTIONS, classify_function, yale_evidence  # noqa: E402
from app.services.company_entity import override_profile  # noqa: E402
from app.services.role_suggestions import title_from_evidence  # noqa: E402

AMAZON = override_profile("Amazon")
ME = {"id": 1, "role": "standard"}


def profile(name: str, headline: str, content: str = "") -> dict:
    slug = name.lower().replace(" ", "-")
    return {"title": f"{name} - {headline} | LinkedIn",
            "url": f"https://www.linkedin.com/in/{slug}/",
            "content": content or f"{name} - {headline}."}


def found(results: list[dict]) -> dict[str, dict]:
    return {p["name"]: p for p in W._extract_people_from_results(results, "Amazon", "amazon.com", entity=AMAZON)}


# --- Yale -----------------------------------------------------------------

def yale_needs_the_school_not_the_word() -> None:
    for text in [
        "Education: Yale University", "BA from Yale University", "Yale '05", "Yale \u201905", "Yale SOM",
        "MBA, Yale School of Management", "graduated from Yale", "Yale College class of 2009",
        "proud Yale alum", "YALE UNIVERSITY", "J.D., Yale Law School",
    ]:
        assert yale_evidence(text), text
    for text in [
        "Yale locks and keys", "Yale Appliance & Lighting", "Yale Security Inc. regional manager",
        "attended Yale Appliance's trade show", "Yale forklift trucks", "Yale New Haven Hospital", "yale", "",
    ]:
        assert yale_evidence(text) == "", text
    assert yale_evidence(None) == ""
    # The evidence is the words around the tie, short enough to show on hover.
    long = "x " * 200 + "Education: Yale University" + " y" * 200
    got = yale_evidence(long)
    assert "Yale University" in got and len(got) < 200, got


def a_page_ties_only_the_person_it_is_about() -> None:
    own = found([profile("Sam Ortiz", "Finance Manager at Amazon",
                         "Sam Ortiz - Finance Manager at Amazon. Education: Yale University")])
    assert "Yale University" in own["Sam Ortiz"]["yale_signal"], own

    # A team page names two people; the Yale line belongs to the one it follows.
    team = {"title": "Amazon leadership", "url": "https://www.amazon.com/about/team", "content": (
        "Priya Nair - Director of Finance at Amazon. BA, Yale University. "
        "Owen Hart - Director of Operations at Amazon. Previously at Acme.")}
    people = found([team])
    assert "Yale University" in people["Priya Nair"]["yale_signal"], people
    assert people["Owen Hart"]["yale_signal"] == "", people

    # Nothing in the text, or only a lock: no signal.
    plain = found([profile("Lena Fischer", "Operations Manager at Amazon"),
                   profile("Omar Haddad", "Sales Manager at Amazon", "Omar sells Yale locks at Amazon.")])
    assert plain["Lena Fischer"]["yale_signal"] == "" and plain["Omar Haddad"]["yale_signal"] == "", plain


# --- Function -------------------------------------------------------------

def titles_classify_into_one_function() -> None:
    table = {
        "Director of Finance": "finance", "SVP, Chief Financial Officer": "finance", "Controller": "finance",
        "VP, Strategy & Corporate Development": "strategy", "Head of Corporate Development": "strategy",
        "Director of Operations": "operations", "Chief Operating Officer": "operations",
        "Supply Chain Manager": "operations",
        "Chief Marketing Officer": "marketing", "Brand Manager": "marketing", "Head of Communications": "marketing",
        "Head of Partnerships": "sales", "Business Development Manager": "sales", "Account Director": "sales",
        "Director of HR": "people", "Head of Talent Acquisition": "people", "Chief People Officer": "people",
        "Engineering Manager": "technology", "Product Manager": "technology", "CTO": "technology",
        "General Counsel": "legal", "Compliance Manager": "legal",
        "General Manager": "general", "Chief Executive Officer": "general", "Chief of Staff": "general",
        # Two functions in one title: the first by precedence.
        "Marketing Operations Manager": "marketing", "Product Marketing Manager": "marketing",
        "Sales Engineer": "sales", "Data Privacy Counsel": "legal",
        # No function is named: none is guessed.
        "Senior Manager": "", "Director": "", "Vice President": "", "": "",
    }
    for title, want in table.items():
        assert classify_function(title) == want, (title, classify_function(title))
    assert classify_function(None) == ""
    # Every function the picker offers can be searched for, and nothing else.
    assert {fid for fid, _ in FUNCTIONS} == FUNCTION_IDS == set(W.FUNCTION_TITLES)


def a_function_biases_the_wave_without_adding_searches() -> None:
    plain = W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, "working")
    biased = W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, "working", "finance")
    assert len(biased) == len(plain), (len(biased), len(plain))
    assert sum('"Director of Finance"' in q for q in biased) == 1 and not any('"Director of Finance"' in q for q in plain)
    # Without an entity profile nothing makes room for the targeted searches, so two generic ones give way.
    bare = W.first_wave_queries("Acme", "acme.com", "", None, "working", "finance")
    assert sum('"Director of Finance"' in q for q in bare) == 2, bare
    assert all(len(q) <= 300 and '-"amazon development centre india"' in q for q in biased), biased
    # Without an entity, with title hints, and for executives the count is the same too.
    for level in ("working", "executive"):
        for entity in (None, AMAZON):
            for hints in ("", "Head of Partnerships, Producer"):
                a = W.first_wave_queries("Acme", "acme.com", hints, entity, level)
                b = W.first_wave_queries("Acme", "acme.com", hints, entity, level, "operations")
                assert len(a) == len(b), (level, entity is None, hints)
    # Board seats have no function, and an unknown one asks for nothing extra.
    assert W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, "board", "finance") == \
        W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, "board")
    assert W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, "working", "astrology") == plain


def the_yale_search_is_a_later_round_and_costs_no_extra_budget() -> None:
    pool = W.round_query_pool("Amazon", "amazon.com", "", AMAZON, "working")
    first = W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, "working")
    query, page = pool[0]
    assert page == 0 and '"Yale University"' in query and query not in first, pool[0]
    assert sum('"Yale University"' in q for q, _ in pool) == 1
    # A function puts its titles into the later rounds in place of generic ones.
    biased = W.round_query_pool("Amazon", "amazon.com", "", AMAZON, "working", "finance")
    assert len(biased) == len(pool) and any('"Head of Finance"' in q for q, _ in biased), biased
    assert not {q for q, p in biased if p == 0} & set(W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, "working", "finance"))


# --- Titles ---------------------------------------------------------------

def a_title_is_recovered_from_evidence_and_never_invented() -> None:
    # The headline names no role; the snippet does.
    got = found([profile("Sam Ortiz", "Amazon", "Sam Ortiz. Finance Manager at Amazon · Experience: Amazon")])
    assert got["Sam Ortiz"]["title"] == "Finance Manager", got

    # A role on a team page is the person it follows, not the next person's.
    team = {"title": "Amazon people", "url": "https://www.amazon.com/about/team",
            "content": "Priya Nair is on leave. Owen Hart, Director of Operations at Amazon."}
    assert title_from_evidence("Priya Nair", ["Amazon"], text=team["content"]) == ""
    assert title_from_evidence("Owen Hart", ["Amazon"], text=team["content"]) == "Director of Operations"

    # Evidence of another employer, a past role, or the person's own name is no title.
    assert title_from_evidence("Sam Ortiz", ["Amazon"], own_page=True, text="Sam Ortiz. Analyst at Google") == ""
    assert title_from_evidence("Sam Ortiz", ["Amazon"], own_page=True, text="Formerly Analyst at Amazon") == ""
    assert title_from_evidence("Sam Ortiz", ["Amazon"], own_page=True, text="Sam Ortiz at Amazon") == ""
    assert title_from_evidence("Sam Ortiz", ["Amazon"], headline="Sam Ortiz - Amazon | LinkedIn") == ""
    assert title_from_evidence("Sam Ortiz", ["Amazon"],
                               headline="Sam Ortiz - Director of Finance - Amazon | LinkedIn") == "Director of Finance"
    # A headline about someone else says nothing about this person.
    assert title_from_evidence("Sam Ortiz", ["Amazon"],
                               headline="Ana Ruiz - Director of Finance - Amazon | LinkedIn") == ""

    # With no evidence the title is simply blank.
    got = found([profile("Lena Fischer", "Amazon", "Lena Fischer. Seattle. Amazon.")])
    # (the raw headline is kept for the save step, which cleans it to nothing)
    assert D._clean_title(got["Lena Fischer"]["title"], "Lena Fischer", "Amazon") is None, got


# --- Stored, listed, imported ---------------------------------------------

async def _run_row(run_id: int, research: dict | None = None) -> None:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, max_prospects, status, lease_token, research_json, origin,
                completed_at, prospects_count)
               VALUES (?, 1, 'Amazon', NULL, 10, 'running', ?, ?, 'member', CURRENT_TIMESTAMP, 0)""",
            (run_id, f"lease-{run_id}", json.dumps({"entity": AMAZON, **(research or {})})),
        )
        await db.commit()
    finally:
        await db.close()


REMEMBERED = AsyncMock()


async def _run(run_id: int, results: list[dict], research: dict | None = None) -> tuple[list[dict], list]:
    await _run_row(run_id, research)
    function = (research or {}).get("function")
    lead = W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, "working", function)[0]
    asked: list[tuple[str, int]] = []

    async def search_once(query, max_results, page=0):
        asked.append((query, page))
        await asyncio.sleep(0)
        return list(results) if (query, page) == (lead, 0) else []

    async def verify(rows, **_kw):
        return [dict(r, ai_verdict="real", email_verification_status="valid") for r in rows], []

    token = D._active_lease.set(f"lease-{run_id}")
    try:
        with patch.object(D, "scrape_contacts_from_domain", AsyncMock(return_value=[])), \
             patch.object(W, "_search_once", search_once), \
             patch.object(D, "_llm_json", AsyncMock(return_value={"people": []})), \
             patch.object(D, "_company_meta", AsyncMock(return_value={})), \
             patch.object(D, "run_contact_verify_pipeline", verify), \
             patch("app.services.web_fetch.web_search_configured", lambda: True), \
             patch.object(RE, "cached_roster_contacts", AsyncMock(return_value=[])), \
             patch.object(RE, "refresh_roster_on_demand", AsyncMock(return_value=[])), \
             patch.object(RE, "roster_refresh_note", AsyncMock(return_value="")), \
             patch.object(R, "remember_discovery_people", REMEMBERED):
            await D.execute_yucgoutreach_run(run_id)
    finally:
        D._active_lease.reset(token)
    return await list_prospects(run_id, ME), asked


async def a_run_stores_yale_and_recovered_titles_and_lists_yale_first() -> None:
    results = [
        profile("Sam Ortiz", "Operations Manager at Amazon"),
        profile("Priya Nair", "Finance Manager at Amazon", "Priya Nair - Finance Manager at Amazon. BA, Yale University."),
        profile("Owen Hart", "Amazon", "Owen Hart. Director of Partnerships at Amazon · Experience: Amazon"),
        profile("Lena Fischer", "Marketing Manager at Amazon", "Lena sells Yale locks at Amazon."),
        profile("Kim Walters", "Amazon", "Kim Walters. Seattle. Amazon."),
    ]
    people, asked = await _run(31, results, {"level": "working"})
    by_name = {f'{p["first_name"]} {p["last_name"]}': p for p in people}
    assert set(by_name) == {"Sam Ortiz", "Priya Nair", "Owen Hart", "Lena Fischer", "Kim Walters"}, by_name.keys()
    # No text states a title for Kim, so none is stored.
    assert not by_name["Kim Walters"]["title"] and by_name["Kim Walters"]["person_function"] == "", by_name["Kim Walters"]

    # Yale first, however the scores fall; the rest keep score order.
    assert people[0]["last_name"] == "Nair", [p["last_name"] for p in people]
    assert "Yale University" in people[0]["yale_signal"]
    assert not any(p["yale_signal"] for p in people[1:]), people

    # The recovered title is stored, classified, and counted at its level.
    assert by_name["Owen Hart"]["title"] == "Director of Partnerships", by_name["Owen Hart"]
    assert by_name["Owen Hart"]["person_function"] == "sales" and by_name["Owen Hart"]["person_level"] == "working"
    assert len(asked) == len(set(asked)) and len(asked) <= 43, asked


async def a_function_search_stays_inside_the_same_budget() -> None:
    results = [profile("Priya Nair", "Finance Manager at Amazon")]
    _, plain = await _run(32, results)
    people, biased = await _run(33, results, {"level": "working", "function": "finance"})
    assert len(people) == 1 and len(biased) <= len(plain), (len(biased), len(plain))
    db = await get_db()
    try:
        research = json.loads((await (await db.execute(
            "SELECT research_json FROM yucgoutreach_discovery_runs WHERE id=33")).fetchone())["research_json"])
    finally:
        await db.close()
    assert research["function"] == "finance", research


async def the_listing_filters_by_function() -> None:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs (id, user_id, company_name, status, max_prospects, origin)
               VALUES (41, 1, 'Amazon', 'completed', 10, 'member')""")
        for i, (title, score, yale) in enumerate([
            ("Director of Finance", 90, None), ("Finance Manager", 70, "Yale University"),
            ("Operations Manager", 95, None), ("Senior Manager", 99, None), ("Head of Marketing", 80, None),
        ]):
            await db.execute(
                """INSERT INTO yucgoutreach_prospects (run_id, first_name, last_name, email, company, title, score, yale_signal)
                   VALUES (41, 'P', ?, ?, 'Amazon', ?, ?, ?)""",
                (f"L{i}", f"p{i}@amazon.com", title, score, yale))
        await db.commit()
    finally:
        await db.close()

    everyone = await list_prospects(41, ME)
    assert [p["title"] for p in everyone] == [
        "Finance Manager", "Senior Manager", "Operations Manager", "Director of Finance", "Head of Marketing"], everyone
    finance = await list_prospects(41, ME, function="finance")
    assert [p["title"] for p in finance] == ["Finance Manager", "Director of Finance"]
    assert [p["title"] for p in await list_prospects(41, ME, function="legal")] == []
    # Filtering happens before the limit, so a match past it is not lost.
    assert [p["title"] for p in await list_prospects(41, ME, limit=1, function="finance")] == ["Finance Manager"]
    assert [p["title"] for p in await list_prospects(41, ME, limit=1)] == ["Finance Manager"]
    try:
        await list_prospects(41, ME, function="astrology")
        raise AssertionError("an unknown function must be refused")
    except HTTPException as exc:
        assert exc.status_code == 422, exc


async def yale_follows_a_person_onto_their_contact() -> None:
    async def assess(email: str, **_: object) -> dict:
        return {"mailbox": "inconclusive", "method": "local", "reason": "test"}

    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs (id, user_id, company_name, company_domain, status, max_prospects, origin)
               VALUES (42, 1, 'Amazon', 'amazon.com', 'completed', 10, 'member')""")
        for i, (name, yale) in enumerate([("Priya Nair", "BA, Yale University"), ("Owen Hart", None)]):
            first, last = name.split()
            await db.execute(
                """INSERT INTO yucgoutreach_prospects (id, run_id, first_name, last_name, email, company, title, score, yale_signal)
                   VALUES (?, 42, ?, ?, ?, 'Amazon', 'Finance Manager', 80, ?)""",
                (420 + i, first, last, f"{first.lower()}.{last.lower()}@amazon.com", yale))
        await db.commit()
    finally:
        await db.close()
    with patch("app.services.yucgoutreach_import.assess_address", side_effect=assess):
        result = await import_run_to_contacts(42, None, ME)
    assert result["created"] == 2, result
    page = await list_contacts(company="Amazon", user=ME, limit=50, offset=0, employee_only=False, mine_only=True,
                               companies=None, q=None, pipeline_status=None, release_id=None)
    contacts = {c["name"]: c for c in page["items"]}
    assert contacts["Priya Nair"]["yale_signal"] == "BA, Yale University", contacts
    assert not contacts["Owen Hart"]["yale_signal"]
    assert contacts["Priya Nair"]["person_function"] == "finance", contacts


async def the_migration_adds_the_column_once() -> None:
    from app.migrations import migrate  # noqa: F401  (init_db already applied it)

    db = await get_db()
    try:
        for table in ("yucgoutreach_prospects", "contacts"):
            columns = {r[1] for r in await (await db.execute(f"PRAGMA table_info({table})")).fetchall()}
            assert "yale_signal" in columns, table
        await D.add_yale_signal(db)   # running it again changes nothing
    finally:
        await db.close()


async def main() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()
    yale_needs_the_school_not_the_word()
    a_page_ties_only_the_person_it_is_about()
    titles_classify_into_one_function()
    a_function_biases_the_wave_without_adding_searches()
    the_yale_search_is_a_later_round_and_costs_no_extra_budget()
    a_title_is_recovered_from_evidence_and_never_invented()
    await the_migration_adds_the_column_once()
    await a_run_stores_yale_and_recovered_titles_and_lists_yale_first()
    await a_function_search_stays_inside_the_same_budget()
    await the_listing_filters_by_function()
    await yale_follows_a_person_onto_their_contact()


def test_find_people() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
    print("find people: ok")
