"""A Find run records how long each stage took, so a slow search can be explained.

From backend/: python tests/test_discovery_timings.py
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
os.environ["JWT_SECRET"] = "p-discovery-timings-secret-not-a-known-default"
os.environ["DISCOVERY_JOBS"] = "1"
os.environ.pop("TAVILY_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import discovery_gate as gate  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services import web_contact_discovery as W  # noqa: E402
from app.services import yucgoutreach_discovery as D  # noqa: E402
from app.services.company_entity import override_profile  # noqa: E402

AMAZON = override_profile("Amazon")
NAMES = ["Sam Ortiz", "Priya Nair", "Owen Hart", "Lena Fischer"]
STAGES = {
    "sources", "website_crawl", "web_search", "company_meta", "roster", "name_seeds", "merge",
    "verify", "identity", "email_verify", "ai_review", "save", "gate_wait",
}


def profile(name: str) -> dict:
    slug = name.lower().replace(" ", "-")
    return {"title": f"{name} - Engineering Manager at Amazon | LinkedIn",
            "url": f"https://www.linkedin.com/in/{slug}",
            "content": f"{name} - Engineering Manager at Amazon."}


async def _insert_run(run_id: int) -> None:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, max_prospects, status, lease_token, research_json)
               VALUES (?, 1, 'Amazon', NULL, 3, 'running', ?, ?)""",
            (run_id, f"lease-{run_id}", json.dumps({"title_hints": "", "entity": AMAZON})),
        )
        await db.commit()
    finally:
        await db.close()


async def a_finished_run_reports_seconds_per_stage() -> None:
    await _insert_run(1)
    lead = W.first_wave_queries("Amazon", "amazon.com", "", AMAZON)[0]

    async def search_once(query, max_results, page=0):
        await asyncio.sleep(0.01)
        return [profile(n) for n in NAMES] if (query, page) == (lead, 0) else []

    async def crawl(**_kw):
        # The crawl waits behind another job for the only slot.
        async with gate.discovery_job():
            await asyncio.sleep(0.01)
        return []

    async def verify(rows, *, on_progress=None, **_kw):
        await on_progress("identity", len(rows), len(rows), "")
        await asyncio.sleep(0.02)
        await on_progress("verify", 1, 1, "")
        await asyncio.sleep(0.02)
        await on_progress("ai", 1, 1, "")
        return [dict(r, ai_verdict="real", email_verification_status="valid") for r in rows], []

    async def holder() -> None:
        async with gate.discovery_job():
            await asyncio.sleep(0.15)

    gate._sem = gate._sem_n = None
    held = asyncio.ensure_future(holder())
    await asyncio.sleep(0)
    with patch.object(D, "scrape_contacts_from_domain", crawl), \
         patch.object(W, "_search_once", search_once), \
         patch.object(D, "_llm_json", AsyncMock(return_value={"people": []})), \
         patch.object(D, "_company_meta", AsyncMock(return_value={})), \
         patch.object(D, "run_contact_verify_pipeline", verify), \
         patch("app.services.web_fetch.web_search_configured", lambda: True), \
         patch.object(RE, "cached_roster_contacts", AsyncMock(return_value=[])), \
         patch.object(RE, "refresh_roster_on_demand", AsyncMock(return_value=[])), \
         patch.object(RE, "roster_refresh_note", AsyncMock(return_value="")), \
         patch.object(R, "remember_discovery_people", AsyncMock()):
        await D._yucgoutreach_run_guard(1, "lease-1")
    await held

    db = await get_db()
    try:
        run = dict(await (await db.execute(
            "SELECT status, prospects_count, research_json FROM yucgoutreach_discovery_runs WHERE id=1"
        )).fetchone())
    finally:
        await db.close()
    research = json.loads(run["research_json"])
    assert run["status"] == "completed" and run["prospects_count"] == len(NAMES), run
    # The counts the run already recorded are still there next to the timings.
    assert research["saved"] == len(NAMES) and "domain_contacts" in research, research

    timings = research["timings"]
    assert STAGES <= set(timings), set(timings) ^ STAGES
    assert all(isinstance(v, (int, float)) and v >= 0 for v in timings.values()), timings
    assert timings["total"] >= max(timings["sources"], timings["verify"]), timings
    assert timings["identity"] >= 0.0 and timings["email_verify"] >= 0.0 and timings["ai_review"] >= 0.0
    assert timings["verify"] >= 0.0 and timings["verify"] <= timings["total"], timings
    # The run waited for the slot another job held.
    assert timings["gate_wait"] >= 0.1, timings
    assert 0 <= research["first_prospect_s"] <= timings["total"], research


async def main() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()
    await a_finished_run_reports_seconds_per_stage()


if __name__ == "__main__":
    asyncio.run(main())
    print("discovery timings: ok")
