"""How many people a search looks for, and how far it searches for them.

A search used to look for 120 people at any company and stop after four
rounds of eight searches, so Google and Hyatt ended at 50-150. This pins:

- an unsized search gets a goal from the company's size in the register: 300
  for the largest (top 1000 by market cap, 10,000+ staff), 200 for large ones,
  120 otherwise, and a goal the member types is kept as typed;
- a larger goal gets proportionally more rounds from a deeper pool of new
  searches, and never repeats a first-wave search;
- a 120-person search stays inside its old bound.

No network: search, verification, the model and the roster are replaced.
From backend/: python tests/test_find_target_size.py
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
os.environ["JWT_SECRET"] = "p-find-target-size-secret-not-a-default"
os.environ.pop("TAVILY_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.routers import yucgoutreach as yucg_router  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services import web_contact_discovery as W  # noqa: E402
from app.services import yucgoutreach_discovery as D  # noqa: E402
from app.services.company_entity import override_profile  # noqa: E402
from app.services.company_register import upsert_companies  # noqa: E402

AMAZON = override_profile("Amazon")
FIRST = ["Sam", "Priya", "Owen", "Lena", "Ana", "Kai", "Noor", "Eli", "Rhea", "Tom", "Ida", "Max", "Zoe",
         "Raj", "Una", "Lev", "Mia", "Ben", "Ava", "Leo", "Ivy", "Jon", "Kim", "Lou", "Ned", "Oda", "Pia",
         "Quin", "Rosa", "Seth"]
LAST = ["Ortiz", "Nair", "Hart", "Fischer", "Ruiz", "Chen", "Aziz", "Stone", "Patel", "Wood", "Berg", "Lopez",
        "Frank", "Mehta", "Diaz", "Cole", "Hale", "Moss", "Reed", "Shaw", "Vance", "Webb", "Young", "Zane",
        "Blake", "Craig", "Doyle", "Ellis", "Flynn", "Grant"]


def goals_follow_company_size() -> None:
    size = D.target_for_size
    assert size(0, None, None) == size(999, None, None) == 300
    assert size(1000, None, None) == size(2999, None, None) == 200
    assert size(3000, None, None) == size(None, None, None) == D.YUCG_DEFAULT_PROSPECTS == 120
    assert size(None, 10_000, None) == 300 and size(None, 9_999, None) == size(None, 1_000, None) == 200
    assert size(None, 999, 3) == 120
    assert size(None, None, 0) == size(None, None, 1) == 200 and size(None, None, 2) == 120
    # The biggest signal wins: a mid-cap with a 20,000-person headcount is large.
    assert size(2500, 20_000, None) == 300
    scale = D.search_scale
    assert [scale(n) for n in (1, 120, 121, 240, 241, 300, 500, 800)] == [1, 1, 2, 2, 3, 3, 5, 5]


async def unsized_searches_are_sized_from_the_register() -> None:
    await upsert_companies([
        {"source": "sec_public", "source_key": "0000000001", "tier": "us_public", "country": "US",
         "company_name": "Hyatt Hotels Corp", "prominence_rank": 640},
        {"source": "sec_public", "source_key": "0000000002", "tier": "us_public", "country": "US",
         "company_name": "Smallcap Widgets Inc", "prominence_rank": 6200},
        {"source": "sec_public", "source_key": "0001652044", "tier": "us_public", "country": "US",
         "company_name": "Alphabet Inc.", "prominence_rank": 2},
        {"source": "irs_990", "source_key": "060646973", "tier": "us_nonprofit", "country": "US",
         "company_name": "Yale University", "employees": 32_000},
        {"source": "companies_house", "source_key": "01234567", "tier": "uk", "country": "GB",
         "company_name": "Brightwell Group Limited", "metadata": {"account_category": "GROUP"}},
    ])
    assert await D.company_size_target("Hyatt Hotels", None) == 300
    assert await D.company_size_target("Yale University", None) == 300
    # A brand is sized as the company it is registered under.
    assert await D.company_size_target("Google", "google.com", legal_name="Google LLC") == 300
    assert await D.company_size_target("Brightwell Group", None) == 200
    assert await D.company_size_target("Smallcap Widgets", None) == 120
    assert await D.company_size_target("Nobody Knows Ltd", None) == 120

    kick = AsyncMock()
    with patch.object(yucg_router, "_kick_discovery_drain", kick):
        sized = await yucg_router.create_run(
            yucg_router.YucgOutreachRunCreate(company_name="Hyatt Hotels"), {"id": 1, "role": "standard"})
        typed = await yucg_router.create_run(
            yucg_router.YucgOutreachRunCreate(company_name="Yale University", max_prospects=40),
            {"id": 1, "role": "standard"})
    assert sized["max_prospects"] == 300 and typed["max_prospects"] == 40, (sized, typed)
    db = await get_db()
    try:
        stored = {r["id"]: r["max_prospects"] for r in await (await db.execute(
            "SELECT id, max_prospects FROM yucgoutreach_discovery_runs WHERE id IN (?, ?)",
            (sized["id"], typed["id"]))).fetchall()}
        # Out of the way of the runs below; nothing drains them here.
        await db.execute("DELETE FROM yucgoutreach_discovery_runs")
        await db.commit()
    finally:
        await db.close()
    assert stored == {sized["id"]: 300, typed["id"]: 40}, stored


def a_larger_goal_has_a_deeper_pool() -> None:
    first = W.first_wave_queries("Amazon", "amazon.com", "", AMAZON)
    shallow = W.round_query_pool("Amazon", "amazon.com", "", AMAZON)
    for depth in range(1, 6):
        pool = W.round_query_pool("Amazon", "amazon.com", "", AMAZON, depth=depth)
        # The rounds a goal of this depth may ask can all be filled.
        assert len(pool) >= D.DISCOVERY_MAX_ROUNDS * D.DISCOVERY_ROUND_QUERIES * depth, (depth, len(pool))
        assert pool[:len(shallow)] == shallow, depth
        assert len(pool) == len(set(pool)) and not {q for q, page in pool if page == 0} & set(first), depth
        assert all(0 <= page <= 10 for _, page in pool), depth
    # A function search stays on that function's titles however deep it goes.
    finance = W.round_query_pool("Amazon", "amazon.com", "", AMAZON, ["working"], "finance", depth=3)
    assert not any('"Head of Marketing"' in q for q, _ in finance), finance


async def _run(run_id: int, target: int) -> tuple[dict, list[tuple[str, int]]]:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, max_prospects, status, lease_token, research_json)
               VALUES (?, 1, 'Amazon', NULL, ?, 'running', ?, ?)""",
            (run_id, target, f"lease-{run_id}",
             json.dumps({"title_hints": "", "entity": AMAZON, "levels": ["working"]})),
        )
        await db.commit()
    finally:
        await db.close()
    asked: list[tuple[str, int]] = []
    names = iter(f"{f} {l}" for l in LAST for f in FIRST)

    async def search_once(query, max_results, page=0):
        # Every search finds two people nobody found before.
        asked.append((query, page))
        await asyncio.sleep(0)
        out = []
        for name in (next(names), next(names)):
            slug = name.lower().replace(" ", "-")
            out.append({"title": f"{name} - Engineering Manager at Amazon | LinkedIn",
                        "url": f"https://www.linkedin.com/in/{slug}/",
                        "content": f"{name} - Engineering Manager at Amazon."})
        return out

    async def verify(rows, **_kw):
        return [dict(r, ai_verdict="real", email_verification_status="valid") for r in rows], []

    token = D._active_lease.set(f"lease-{run_id}")
    try:
        with patch.object(D, "scrape_contacts_from_domain", AsyncMock(return_value=[])), \
             patch.object(W, "_search_once", search_once), \
             patch.object(D, "_llm_json", AsyncMock(return_value={"people": []})), \
             patch.object(D, "_company_meta", AsyncMock(return_value={})), \
             patch.object(D, "_tavily_name_seeds", AsyncMock(return_value=[])), \
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
            "SELECT status, prospects_count, research_json FROM yucgoutreach_discovery_runs WHERE id=?",
            (run_id,))).fetchone())
    finally:
        await db.close()
    return run, asked


async def a_larger_goal_searches_proportionally_further() -> None:
    first = len(W.first_wave_queries("Amazon", "amazon.com", "", AMAZON, "working"))
    for run_id, target in ((31, 120), (32, 300)):
        run, asked = await _run(run_id, target)
        research = json.loads(run["research_json"])
        scale = D.search_scale(target)
        assert run["status"] == "completed" and research["search_scale"] == scale, run
        # Both stop on the round limit, the larger one after three times the rounds.
        assert research["shortfall"]["reason"] == "round_cap", research
        assert research["rounds"] == 1 + D.DISCOVERY_MAX_ROUNDS * scale, research
        assert len(asked) == len(set(asked)) == research["searches"], (len(asked), research["searches"])
        assert len(asked) <= first + D.DISCOVERY_MAX_ROUNDS * D.DISCOVERY_ROUND_QUERIES * scale, len(asked)
        assert len(research["round_saved"]) == research["rounds"] and sum(research["round_saved"]) == run["prospects_count"]
        if target == 300:
            assert run["prospects_count"] >= 200, run


async def main() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()
    goals_follow_company_size()
    await unsized_searches_are_sized_from_the_register()
    a_larger_goal_has_a_deeper_pool()
    await a_larger_goal_searches_proportionally_further()


if __name__ == "__main__":
    asyncio.run(main())
    print("find target size: ok")
