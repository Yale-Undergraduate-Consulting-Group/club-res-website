"""Several Find runs at once all show progress.

On prod (2026-11-17) six searches ran at once and two sat at 3% for minutes on
"Waiting for website crawl slot", a message written after the wait, while
their web searches queued behind the other runs' later rounds. This pins:

- runs started together all move: none goes PROGRESS_TICK_SEC-times-a-few
  without a new progress line while another run holds the only crawl slot or
  the club's search pace is full, and a waiting run says what it waits for;
- the shared search pace serves the run that has asked least first, so a new
  run's first searches go ahead of an older run's later rounds;
- crawl slots follow the container's memory, and one site is crawled once at a time.

No network: search, crawl pages, verification, the model and the roster are
replaced; the crawl gate, the pacer and the run's own loop are real.
From backend/: python tests/test_concurrent_discovery.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["JWT_SECRET"] = "p-concurrent-discovery-secret-not-a-default"
os.environ["DISCOVERY_JOBS"] = "1"
os.environ["DISCOVERY_PROGRESS_TICK_SEC"] = "0.05"
os.environ["MONID_SEARCHES_PER_MINUTE"] = "12"
os.environ.pop("TAVILY_API_KEY", None)

from app.database import get_db, init_db  # noqa: E402
from app.services import contact_scraper as CS  # noqa: E402
from app.services import discovery_gate as gate  # noqa: E402
from app.services import roster_email as RE  # noqa: E402
from app.services import roster_watch as R  # noqa: E402
from app.services import web_contact_discovery as W  # noqa: E402
from app.services import web_fetch as F  # noqa: E402
from app.services import yucgoutreach_discovery as D  # noqa: E402

TICK = D.PROGRESS_TICK_SEC
COMPANIES = (("Acme", "acme.com", 3.0), ("Globex", "globex.com", 0.05), ("Initech", "initech.com", 0.05))
FIRST = ["Sam", "Priya", "Owen", "Lena", "Ana", "Kai", "Noor", "Eli", "Rhea", "Tom", "Ida", "Max", "Zoe",
         "Raj", "Una", "Lev", "Mia", "Ben", "Ava", "Leo"]
LAST = ["Ortiz", "Nair", "Hart", "Fischer", "Ruiz", "Chen", "Aziz", "Stone", "Patel", "Wood", "Berg", "Lopez",
        "Frank", "Mehta", "Diaz", "Cole", "Hale", "Moss", "Reed", "Shaw"]


def people(company: str, query: str, page: int) -> list[dict]:
    """Two profiles per answered search, the same for the same question."""
    seed = sum(map(ord, f"{query}|{page}"))
    out = []
    for i in range(2):
        n = (seed * 7 + i * 131) % (len(FIRST) * len(LAST))
        name = f"{FIRST[n % len(FIRST)]} {LAST[n // len(FIRST)]}"
        slug = name.lower().replace(" ", "-")
        out.append({"title": f"{name} - Engineering Manager at {company} | LinkedIn",
                    "url": f"https://www.linkedin.com/in/{slug}-{company.lower()}/",
                    "content": f"{name} - Engineering Manager at {company}."})
    return out


async def _insert_run(run_id: int, company: str, domain: str, target: int) -> None:
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO yucgoutreach_discovery_runs
               (id, user_id, company_name, company_domain, max_prospects, status, lease_token, research_json)
               VALUES (?, 1, ?, ?, ?, 'running', ?, ?)""",
            (run_id, company, domain, target, f"lease-{run_id}", json.dumps({"levels": ["working"]})),
        )
        await db.commit()
    finally:
        await db.close()


async def runs_started_together_all_show_progress() -> None:
    for run_id, (company, domain, _) in enumerate(COMPANIES, start=1):
        await _insert_run(run_id, company, domain, 12)
    hold = {domain: seconds for _, domain, seconds in COMPANIES}
    F._SEARCH_PACER_WINDOW_S = 0.4
    F._search_slots.clear()
    gate._sem = gate._sem_n = None

    async def search_once(query, max_results, page=0):
        # Through the real club-wide pacer, as a Monid call would be.
        await F._await_search_slot()
        await asyncio.sleep(0.01)
        company = next(c for c, _, _ in COMPANIES if c.lower() in query.lower())
        return people(company, query, page)

    acme_crawling = asyncio.Event()

    async def crawl_pages(domain, company_name, max_pages, on_page, cancel_event):
        if domain == "acme.com":
            acme_crawling.set()
        for i in range(1, 4):
            await asyncio.sleep(hold[domain] / 3)
            if on_page:
                await on_page(i, 3, f"https://{domain}/team")
        return []

    async def verify(rows, *, on_progress=None, **_kw):
        if on_progress:
            await on_progress("identity", len(rows), len(rows), "")
        await asyncio.sleep(0.05)
        return [dict(r, ai_verdict="real", email_verification_status="valid") for r in rows], []

    # Every progress write a run makes, with when it was made.
    writes: dict[int, list[tuple[float, str]]] = {1: [], 2: [], 3: []}
    real_update = D._run_update

    async def update(run_id, **fields):
        if fields.get("progress_message") is not None and not fields.get("completed"):
            writes[run_id].append((time.monotonic(), fields["progress_message"]))
        await real_update(run_id, **fields)

    with patch.object(CS, "_scrape_contacts_from_domain_html", crawl_pages), \
         patch.object(W, "_search_once", search_once), \
         patch.object(D, "_run_update", update), \
         patch.object(D, "_llm_json", AsyncMock(return_value={"people": []})), \
         patch.object(D, "_company_meta", AsyncMock(return_value={})), \
         patch.object(D, "_tavily_name_seeds", AsyncMock(return_value=[])), \
         patch.object(D, "run_contact_verify_pipeline", verify), \
         patch("app.services.web_fetch.web_search_configured", lambda: True), \
         patch.object(RE, "cached_roster_contacts", AsyncMock(return_value=[])), \
         patch.object(RE, "refresh_roster_on_demand", AsyncMock(return_value=[])), \
         patch.object(RE, "roster_refresh_note", AsyncMock(return_value="")), \
         patch.object(R, "remember_discovery_people", AsyncMock()):
        # Acme's slow crawl takes the only slot first; the other two start then.
        first = asyncio.ensure_future(D._yucgoutreach_run_guard(1, "lease-1"))
        await asyncio.wait_for(acme_crawling.wait(), 5)
        await asyncio.gather(first, *(D._yucgoutreach_run_guard(run_id, f"lease-{run_id}") for run_id in (2, 3)))

    db = await get_db()
    try:
        runs = {r["id"]: dict(r) for r in await (await db.execute(
            "SELECT id, status, prospects_count, research_json FROM yucgoutreach_discovery_runs")).fetchall()}
    finally:
        await db.close()
    for run_id, run in runs.items():
        assert run["status"] == "completed" and run["prospects_count"] >= 12, run
    # The two later crawls waited for Acme's slot, and the runs queued for the
    # search pace, so there was something to wait through.
    assert min(json.loads(runs[i]["research_json"])["timings"]["gate_wait"] for i in (2, 3)) >= 0.5, runs
    lines = [message for made in writes.values() for _, message in made]
    assert any("website waiting for a crawl slot" in m for m in lines), lines
    assert any("queued at the club's shared search pace" in m for m in lines), lines
    for run_id, made in writes.items():
        at = [t for t, _ in made]
        gap = max(b - a for a, b in zip(at, at[1:]))
        # A tick is 0.05 s; a loaded CI box gets twenty. Before, a run behind
        # Acme's 3 s crawl wrote nothing from the moment it started until all
        # its sources were in.
        assert gap < 20 * TICK, (run_id, gap, made)
        # A run that is waiting says how long it has been waiting.
        assert any(m.endswith(" s") for _, m in made), made


async def the_pace_serves_the_run_that_has_asked_least() -> None:
    F._SEARCH_PACER_WINDOW_S = 0.15
    F._search_slots.clear()
    served: list[str] = []
    busy, fresh = [40], [0]

    async def search(name: str, account: list[int]) -> None:
        token = F.search_pace_account.set(account)
        try:
            await F._await_search_slot()
        finally:
            F.search_pace_account.reset(token)
        served.append(name)

    with patch.dict(os.environ, {"MONID_SEARCHES_PER_MINUTE": "1"}):
        await F._await_search_slot()  # the window is full
        older = [asyncio.ensure_future(search(f"round-{i}", busy)) for i in range(3)]
        await asyncio.sleep(0)
        assert F.search_pace_waiting() == 3 and F.search_pace_waiting(busy) == 3, F._pace_queue
        newer = [asyncio.ensure_future(search(f"first-{i}", fresh)) for i in range(2)]
        await asyncio.gather(*older, *newer)
    # The new run's two searches ran first, though they arrived last.
    assert served == ["first-0", "first-1", "round-0", "round-1", "round-2"], served
    assert F.search_pace_waiting() == 0


def crawl_slots_follow_memory() -> None:
    gib = 1024**3
    with patch.dict(os.environ, {"DISCOVERY_JOBS": ""}):
        for ram, slots in ((None, 1), (gib // 2, 1), (gib, 4), (int(0.75 * gib), 2), (2 * gib, 4)):
            with patch.object(gate, "_cgroup_or_host_ram_bytes", lambda ram=ram: ram):
                assert gate.discovery_job_slots() == slots, (ram, slots)
    with patch.dict(os.environ, {"DISCOVERY_JOBS": "2"}):
        assert gate.discovery_job_slots() == 2
    # One crawl's eleven capped pages and the parse tree of one (about ten
    # times its page) fit the RAM a slot is sized for.
    assert 21 * CS.CRAWL_MAX_PAGE_BYTES <= gate.CRAWL_RAM_BYTES


async def one_site_is_crawled_once_at_a_time() -> None:
    gate._sem = gate._sem_n = None
    inside: list[int] = []
    most = [0]

    async def crawl_pages(domain, company_name, max_pages, on_page, cancel_event):
        inside.append(1)
        most[0] = max(most[0], len(inside))
        await asyncio.sleep(0.05)
        inside.pop()
        return []

    with patch.dict(os.environ, {"DISCOVERY_JOBS": "4"}), \
         patch.object(CS, "_scrape_contacts_from_domain_html", crawl_pages):
        gate._sem = gate._sem_n = None
        await asyncio.gather(*(CS.scrape_contacts_from_domain("acme.com", "Acme") for _ in range(3)))
        assert most[0] == 1 and not CS._site_crawls, most
        await asyncio.gather(*(CS.scrape_contacts_from_domain(d, d) for d in ("a.com", "b.com", "c.com")))
        assert most[0] == 3, most
    gate._sem = gate._sem_n = None


async def main() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.execute("INSERT INTO users (id, email, role, is_active) VALUES (1, 'a@yale.edu', 'standard', 1)")
        await db.commit()
    finally:
        await db.close()
    await runs_started_together_all_show_progress()
    await the_pace_serves_the_run_that_has_asked_least()
    crawl_slots_follow_memory()
    await one_site_is_crawled_once_at_a_time()


if __name__ == "__main__":
    asyncio.run(main())
    print("concurrent discovery: ok")
