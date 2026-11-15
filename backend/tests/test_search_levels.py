"""Who to look for is any combination of levels, and a member may search
several companies at once.

- The run carries a set of levels (team leads, senior leaders, board, and
  people with no title shown); its first-wave searches and its stop goal cover
  every one of them, and a person with no title counts only when asked for.
- A member may have MEMBER_ACTIVE_RUNS (3) searches queued or running; the
  club-wide queue cap still applies across members, and the drain starts
  every queued search rather than one at a time.

No network: search, verification, the model and the roster are replaced.
From backend/: python tests/test_search_levels.py
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
os.environ["JWT_SECRET"] = "p-search-levels-secret-not-a-known-default-xxxx"
os.environ.pop("TAVILY_API_KEY", None)

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from app.database import get_db, init_db  # noqa: E402
from app.routers.yucgoutreach import YucgOutreachRunCreate  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services import web_contact_discovery as W  # noqa: E402
from app.services import yucgoutreach_discovery as D  # noqa: E402
from app.services.company_entity import override_profile  # noqa: E402
from app.services.yucgoutreach_import import enqueue_discovery_run  # noqa: E402

AMAZON = override_profile("Amazon")


def profile(name: str, headline: str, content: str = "") -> dict:
    slug = name.lower().replace(" ", "-")
    return {"title": f"{name} - {headline} | LinkedIn",
            "url": f"https://www.linkedin.com/in/{slug}/",
            "content": content or f"{name} - {headline}."}


LEADS = [profile("Sam Ortiz", "Operations Manager at Amazon"), profile("Priya Nair", "Finance Manager at Amazon")]
CHIEFS = [profile("Owen Hart", "Chief Financial Officer at Amazon"), profile("Lena Fischer", "Chief Operating Officer at Amazon")]
UNTITLED = profile("Kim Walters", "Amazon", "Kim Walters. Seattle. Amazon.")


# --- Which searches a set of levels asks -----------------------------------

def levels_are_normalised() -> None:
    assert W.wanted_levels(["executive", "working"]) == ("working", "executive")
    assert W.wanted_levels("board") == ("board",)
    assert W.wanted_levels(["working", "working", "nonsense"]) == ("working",)
    # Nothing valid is the page's default: team leads and senior leaders.
    assert W.wanted_levels([]) == W.wanted_levels(None) == W.wanted_levels(["nonsense"]) == ("working", "executive")
    # The API refuses an empty or unknown choice instead of guessing.
    assert YucgOutreachRunCreate(company_name="Acme").levels == ["working", "executive"]
    for bad in ([], ["everyone"]):
        try:
            YucgOutreachRunCreate(company_name="Acme", levels=bad)
        except ValidationError:
            continue
        raise AssertionError(f"levels={bad} was accepted")


def several_levels_ask_every_levels_questions() -> None:
    def ask(levels, **kw):
        return W.first_wave_queries("Amazon", "amazon.com", kw.get("hints", ""), AMAZON, levels, kw.get("function"))

    working, executive, both = ask(["working"]), ask(["executive"]), ask(["working", "executive"])
    director = '"director of" OR "head of"'
    assert any(director in q for q in both) and any("CEO OR CFO" in q for q in both), both
    assert not any("CEO OR CFO" in q for q in working) and not any(director in q for q in executive)
    # Two levels ask their three broadest searches each, not eight in all.
    assert len(both) == len(working) + 2, (len(both), len(working))
    # Board seats join in, each level still represented.
    three = ask(["working", "executive", "board"])
    assert any('"board of directors"' in q for q in three) and any(director in q for q in three), three
    # "No title shown" asks nothing of its own: alone it is the team-lead search.
    assert ask(["unknown"]) == working and ask(["working", "unknown"]) == working
    # A function still takes the place of a generic search, however many levels.
    assert len(ask(["working", "executive"], function="finance")) == len(both)
    assert sum('"Director of Finance"' in q for q in ask(["working", "executive"], function="finance")) == 1
    # Later rounds ask title variants of every chosen level.
    pool = [q for q, _ in W.round_query_pool("Amazon", "amazon.com", "", AMAZON, ["working", "executive"])]
    assert any('"director" site:linkedin.com/in' in q for q in pool) and any('"chief" site:linkedin.com/in' in q for q in pool)
    # Whatever the levels, the first wave stays inside the documented bound.
    assert max(len(ask(levels, hints="Head of Partnerships, Producer"))
               for levels in (["working"], ["working", "executive"], ["working", "executive", "board"])) <= 25


# --- The goal counts the chosen levels --------------------------------------

async def _run(run_id: int, target: int, levels: list[str], results: list[dict]) -> tuple[dict, list, list]:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, max_prospects, status, lease_token, research_json, origin)
               VALUES (?, 1, 'Amazon', NULL, ?, 'running', ?, ?, 'member')""",
            (run_id, target, f"lease-{run_id}", json.dumps({"entity": AMAZON, "levels": levels})),
        )
        await db.commit()
    finally:
        await db.close()
    lead = W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, levels)[0]
    first_wave = {(q, 0) for q in W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, levels)}
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
             patch.object(R, "remember_discovery_people", AsyncMock()):
            await D.execute_yucgoutreach_run(run_id)
    finally:
        D._active_lease.reset(token)
    db = await get_db()
    try:
        run = dict(await (await db.execute(
            "SELECT status, progress_message, prospects_count, research_json FROM yucgoutreach_discovery_runs WHERE id=?",
            (run_id,))).fetchone())
        saved = [dict(r) for r in await (await db.execute(
            "SELECT first_name, last_name, title FROM yucgoutreach_prospects WHERE run_id=?", (run_id,))).fetchall()]
    finally:
        await db.close()
    return json.loads(run["research_json"]) | {"status": run["status"], "message": run["progress_message"]}, saved, \
        [q for q in asked if q not in first_wave]


async def the_goal_counts_every_chosen_level() -> None:
    results = LEADS + CHIEFS
    # Team leads and senior leaders: four people reach a goal of four in the
    # first wave, so no later round is asked.
    both, saved, later = await _run(1, 4, ["working", "executive"], results)
    assert both["status"] == "completed" and both["levels"] == ["working", "executive"], both
    assert both["level_found"] == 4 and both["shortfall"] is None and not later, (both, later)
    assert len(saved) == 4, saved
    # Team leads alone: only two count, the chiefs are saved but the search goes on.
    only, saved, later = await _run(2, 4, ["working"], results)
    assert len(saved) == 4 and only["level_found"] == 2, only
    assert only["shortfall"] == {"target": 4, "found": 2, "reason": "exhausted", "levels": ["working"]}, only
    assert later, "a short goal asks more searches"
    assert only["message"].endswith("2 of 4 team leads (no more found)"), only["message"]


async def people_with_no_title_count_only_when_asked_for() -> None:
    results = LEADS + [UNTITLED]
    kept, saved, _ = await _run(3, 3, ["working", "unknown"], results)
    names = {f'{r["first_name"]} {r["last_name"]}': r["title"] for r in saved}
    assert set(names) == {"Sam Ortiz", "Priya Nair", "Kim Walters"} and not names["Kim Walters"], names
    assert kept["level_found"] == 3 and kept["shortfall"] is None, kept
    # Not asked for: Kim is still saved, but the goal does not count her.
    skipped, saved, _ = await _run(4, 3, ["working"], results)
    assert len(saved) == 3 and skipped["level_found"] == 2 and skipped["shortfall"]["found"] == 2, skipped
    assert D._levels_phrase(("working", "executive", "unknown")) == \
        "team leads, senior leaders and people with no title shown"

    # Ranking follows the same set: a chief outranks nobody in a team-lead
    # search, and is ranked with them when senior leaders are asked for.
    chief = {"title": "Chief Financial Officer", "email_verification_status": "valid", "ai_verdict": "real",
             "contact_source": "web_discovery", "linkedin_url": "https://www.linkedin.com/in/x/"}
    assert D._quality_score(chief, "", ("working",)) < D._quality_score(chief, "", ("working", "executive"))


async def a_stored_run_from_before_still_reads() -> None:
    read = D._levels_from_spec
    assert read({"research_json": json.dumps({"level": "board"})}) == ("board",)
    assert read({"research_json": json.dumps({"levels": ["unknown", "working"]})}) == ("working", "unknown")
    assert read({"research_json": ""}) == read({"research_json": "{}"}) == ("working",)


# --- Several searches at once ------------------------------------------------

async def a_member_may_search_three_companies_at_once() -> None:
    db = await get_db()
    try:
        for uid in (2, 3):
            await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (?, ?, 'standard', 1)",
                             (uid, f"m{uid}@yale.edu"))
        await db.commit()

        async def queue(user_id: int, company: str) -> int:
            run_id = await enqueue_discovery_run(
                db, user_id=user_id, company_name=company, company_domain=None, title_hints=None, max_prospects=10)
            await db.commit()
            return run_id

        with patch.dict(os.environ, {"DISCOVERY_QUEUE_LIMIT": "4"}):
            for company in ("Acme", "Globex", "Initech"):
                await queue(2, company)
            try:
                await queue(2, "Umbrella")
                raise AssertionError("a fourth search of one member was accepted")
            except HTTPException as exc:
                assert exc.status_code == 409, exc
            # Another member is not held up by the first member's three...
            await queue(3, "Hooli")
            # ...until the club-wide queue is full.
            try:
                await queue(3, "Pied Piper")
                raise AssertionError("the club queue cap was ignored")
            except HTTPException as exc:
                assert exc.status_code == 429, exc
        stored = json.loads((await (await db.execute(
            "SELECT research_json FROM yucgoutreach_discovery_runs WHERE company_name='Acme'")).fetchone())["research_json"])
        assert stored == {"levels": ["working", "executive"]}, stored
    finally:
        await db.close()

    # The drain starts a member's searches side by side (each create kicks
    # one), not one after another.
    guard = AsyncMock()
    with patch.object(D, "_yucgoutreach_run_guard", guard):
        claims = await asyncio.gather(*(D.drain_queued_yucgoutreach_runs() for _ in range(4)))
    assert sum(c["claimed"] for c in claims) == 4 and guard.await_count == 4, (claims, guard.await_count)


async def main() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()
    levels_are_normalised()
    several_levels_ask_every_levels_questions()
    await the_goal_counts_every_chosen_level()
    await people_with_no_title_count_only_when_asked_for()
    await a_stored_run_from_before_still_reads()
    await a_member_may_search_three_companies_at_once()


def test_search_levels() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
    print("search levels: ok")
