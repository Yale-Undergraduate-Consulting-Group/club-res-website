"""Find people at the company itself, until the goal is met.

Searching Amazon, Alphabet or Apple used to keep anyone whose page used the
word: an Amazon reseller's partner manager, the owner of Alphabet Soup Co., a
teller at Apple Bank for Savings. And a run stopped after one wave of searches
however many people it had been asked for. This pins:

- the headline's own employer decides, and group units (AWS) are kept, tagged;
- a run below its goal searches again with new queries until it is met;
- a run whose sources dry up says so (shortfall "exhausted");
- shared mailboxes (info@, sales@, careers@) are never saved.

No network: search, verification, the model and the roster are replaced.
From backend/: python tests/test_find_goal.py
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
os.environ["JWT_SECRET"] = "p-find-goal-secret-not-a-known-default-xxxx"
os.environ.pop("TAVILY_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import contact_scraper as CS  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services import web_contact_discovery as W  # noqa: E402
from app.services import yucgoutreach_discovery as D  # noqa: E402
from app.services.company_entity import override_profile, sanitize_entity  # noqa: E402

AMAZON = override_profile("Amazon")


def profile(name: str, headline: str, content: str = "") -> dict:
    slug = name.lower().replace(" ", "-")
    return {"title": f"{name} - {headline} | LinkedIn",
            "url": f"https://www.linkedin.com/in/{slug}/",
            "content": content or f"{name} - {headline}."}


def found(results: list[dict], company: str, domain: str, entity: dict | None,
          skips: dict | None = None) -> dict[str, dict]:
    return {p["name"]: p for p in W._extract_people_from_results(
        results, company, domain, entity=entity, skips=skips)}


def the_headline_employer_decides() -> None:
    skips: dict = {}
    got = found([
        profile("Pat Lee", "Partner manager at Acme (Amazon reseller)"),
        profile("Sam Ortiz", "SDE II at Amazon"),
        profile("Dana Kim", "Solutions Architect at Amazon Web Services (AWS)"),
        profile("Rita Gomez", "Seller Success Lead - Amazon Seller Agency"),
    ], "Amazon", "amazon.com", AMAZON, skips)
    assert "Pat Lee" not in got, got
    assert "Rita Gomez" not in got, got
    assert got["Sam Ortiz"]["group_unit"] is None, got
    assert got["Dana Kim"]["group_unit"] == "AWS", got
    assert set(skips["other_employer"].values()) == {"Pat Lee", "Rita Gomez"}, skips

    # Typing the holding searches and accepts its operating brand.
    alphabet = override_profile("Alphabet")
    got = found([
        profile("Mary Jones", "Owner - Alphabet Soup Co."),
        profile("Lee Chen", "Software Engineer at Google"),
        profile("Ana Ruiz", "Product Manager at YouTube"),
    ], "Alphabet", "google.com", alphabet)
    assert set(got) == {"Lee Chen", "Ana Ruiz"}, got
    assert got["Ana Ruiz"]["group_unit"] == "YouTube" and got["Lee Chen"]["group_unit"] is None, got
    queries = W.first_wave_queries("Alphabet", "google.com", "", alphabet)
    assert any(q.startswith('"Google" ') for q in queries), queries
    assert '"Alphabet" site:linkedin.com/in' in queries, queries

    apple = override_profile("Apple")
    got = found([
        profile("Tom Baker", "Branch Manager - Apple Bank for Savings"),
        profile("Joan Park", "iOS Engineer at Apple"),
        # No employer anywhere, just the word: not enough for an everyday-word brand.
        {"title": "Kim Wu | LinkedIn", "url": "https://www.linkedin.com/in/kim-wu/",
         "content": "Apple fan, photographer, runner."},
    ], "Apple", "apple.com", apple)
    assert set(got) == {"Joan Park"}, got

    # An acronym firm leads its divisions; an everyday-word brand never does.
    for employer, target in (("IBM Consulting", "IBM"), ("KPMG Advisory", "KPMG"), ("EY-Parthenon", "EY")):
        assert W.employer_is_target(employer, [target], [], None)[0], employer
    assert not W.employer_is_target("Amazon Reseller Co", ["Amazon"], [], None)[0]

    # Every profile query names the company in quotes and carries the
    # exclusions, and no query runs long.
    queries = W.first_wave_queries("Amazon", "amazon.com", "", AMAZON)
    for q in queries:
        if "site:linkedin.com/in" in q:
            assert '"Amazon' in q, q   # the brand, or the quoted legal name
        assert '-"amazon development centre india"' in q and len(q) <= 300, q
    pool = W.round_query_pool("Amazon", "amazon.com", "", AMAZON)
    assert pool and not {q for q, page in pool if page == 0} & set(queries), pool

    # The units survive the browser round trip.
    assert sanitize_entity(AMAZON)["units"][0] == {
        "name": "AWS", "country": "US", "domain": None, "aliases": ["Amazon Web Services"]}


async def shared_mailboxes_are_never_people() -> None:
    from app.services.email_verification import assess_address

    for email in ("info@amazon.com", "sales@amazon.com", "careers@amazon.com", "sales.emea@amazon.com"):
        assert not CS.is_valid_person_contact({"name": "Jane Doe", "email": email}, company_name="Amazon"), email
        got = await assess_address(email, actor_id=1)
        assert got["reason"].startswith("Role or generic mailbox"), (email, got)


# --- the goal-driven run -------------------------------------------------

FIRST_NAMES = ["Sam Ortiz", "Priya Nair", "Owen Hart", "Lena Fischer"]
MORE_NAMES = ["Marco Bellini", "Hana Sato", "Diego Ramos", "Ingrid Olsen", "Tariq Aziz", "Nora Quinn",
              "Felix Brandt", "Maya Levin", "Omar Haddad", "Clara Moss", "Ravi Menon", "Sofia Lindqvist"]
SHARED_INBOXES = [
    {"name": "Info Desk", "email": "info@amazon.com", "title": "Information", "contact_source": "domain_scrape"},
    {"name": "Sales Team", "email": "sales@amazon.com", "title": "Sales", "contact_source": "domain_scrape"},
    {"name": "Careers Amazon", "email": "careers@amazon.com", "title": "Jobs", "contact_source": "domain_scrape"},
]


async def _insert_run(run_id: int, target: int, origin: str = "member") -> None:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, max_prospects, status, lease_token, research_json, origin)
               VALUES (?, 1, 'Amazon', NULL, ?, 'running', ?, ?, ?)""",
            (run_id, target, f"lease-{run_id}", json.dumps({"title_hints": "", "entity": AMAZON}), origin),
        )
        await db.commit()
    finally:
        await db.close()


REMEMBERED = AsyncMock()


async def _run(run_id: int, target: int, first_wave: list[dict], later: list[dict],
               later_fail: bool = False, origin: str = "member") -> tuple[dict, list[dict], list]:
    """Run the whole search with the first wave's lead query answering
    ``first_wave`` and every later query answering the next two of ``later``."""
    await _insert_run(run_id, target, origin)
    REMEMBERED.reset_mock()
    lead = W.first_wave_queries("Amazon", "amazon.com", "", AMAZON)[0]
    asked: list[tuple[str, int]] = []
    later = list(later)

    async def search_once(query, max_results, page=0):
        asked.append((query, page))
        await asyncio.sleep(0)
        if (query, page) == (lead, 0):
            return list(first_wave)
        if (query, page) in {(q, 0) for q in W.first_wave_queries("Amazon", "amazon.com", "", AMAZON)}:
            return []
        if later_fail:
            W._record_search_error("provider 503", limit=False)
            return []
        out, later[:2] = later[:2], []
        return out

    async def verify(rows, **_kw):
        return [dict(r, ai_verdict="real", email_verification_status="valid") for r in rows], []

    token = D._active_lease.set(f"lease-{run_id}")
    try:
        with patch.object(D, "scrape_contacts_from_domain", AsyncMock(return_value=SHARED_INBOXES)), \
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

    db = await get_db()
    try:
        run = dict(await (await db.execute(
            "SELECT status, progress_message, prospects_count, research_json FROM yucgoutreach_discovery_runs WHERE id=?",
            (run_id,))).fetchone())
        saved = [dict(r) for r in await (await db.execute(
            "SELECT first_name, last_name, email, evidence_json FROM yucgoutreach_prospects WHERE run_id=?",
            (run_id,))).fetchall()]
    finally:
        await db.close()
    return run, saved, asked


async def a_short_first_wave_searches_again_until_the_goal() -> None:
    first = [profile(n, "Software Engineer at Amazon") for n in FIRST_NAMES]
    first.append(profile("Pat Lee", "Partner manager at Acme (Amazon reseller)"))
    later = [profile("Dana Kim", "Solutions Architect at Amazon Web Services (AWS)")]
    later += [profile(n, "Senior Manager at Amazon") for n in MORE_NAMES]
    run, saved, asked = await _run(21, 10, first, later)

    research = json.loads(run["research_json"])
    assert run["status"] == "completed" and run["prospects_count"] == 10 == len(saved), (run, saved)
    assert research["rounds"] >= 2 and research["shortfall"] is None, research
    assert run["progress_message"].startswith("Done — 10 saved"), run
    emails = {r["email"] for r in saved}
    assert not emails & {"info@amazon.com", "sales@amazon.com", "careers@amazon.com"}, emails
    assert all(not CS.is_role_based_local_part(e.split("@")[0]) for e in emails), emails
    assert "pat.lee@amazon.com" not in emails, emails
    units = {r["email"]: json.loads(r["evidence_json"]).get("group_unit") for r in saved}
    assert units["dana.kim@amazon.com"] == "AWS", units
    # No query is asked twice, and the run stays inside its search bound.
    assert len(asked) == len(set(asked)) and len(asked) <= 43, asked


async def dry_sources_end_with_an_exhausted_shortfall() -> None:
    first = [profile(n, "Software Engineer at Amazon") for n in FIRST_NAMES[:3]]
    run, saved, asked = await _run(22, 10, first, [])
    research = json.loads(run["research_json"])
    assert len(saved) == 3, saved
    assert research["shortfall"] == {"target": 10, "found": 3, "reason": "exhausted"}, research
    assert run["progress_message"].endswith("3 of 10 qualified people (no more found)"), run


async def failing_search_is_not_reported_as_exhausted() -> None:
    first = [profile(n, "Software Engineer at Amazon") for n in FIRST_NAMES[:3]]
    run, saved, _ = await _run(23, 10, first, [], later_fail=True)
    research = json.loads(run["research_json"])
    assert len(saved) == 3, saved
    assert research["shortfall"]["reason"] == "search_failed", research
    assert run["progress_message"].endswith("3 of 10 qualified people (web search failing)"), run


async def everyone_found_is_kept_past_the_goal() -> None:
    first = [profile(n, "Software Engineer at Amazon") for n in FIRST_NAMES]
    run, saved, _ = await _run(24, 2, first, [])
    assert len(saved) == 4, saved
    assert json.loads(run["research_json"])["shortfall"] is None, run
    REMEMBERED.assert_awaited_once()   # a member's company joins officer tracking


async def builder_runs_never_join_officer_tracking() -> None:
    first = [profile(n, "Software Engineer at Amazon") for n in FIRST_NAMES]
    run, saved, _ = await _run(25, 2, first, [], origin="builder")
    assert run["status"] == "completed" and len(saved) == 4, (run, saved)
    REMEMBERED.assert_not_awaited()


async def main() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()
    the_headline_employer_decides()
    await shared_mailboxes_are_never_people()
    await a_short_first_wave_searches_again_until_the_goal()
    await dry_sources_end_with_an_exhausted_shortfall()
    await failing_search_is_not_reported_as_exhausted()
    await everyone_found_is_kept_past_the_goal()
    await builder_runs_never_join_officer_tracking()


if __name__ == "__main__":
    asyncio.run(main())
    print("find goal: ok")
