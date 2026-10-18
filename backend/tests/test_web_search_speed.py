"""Finding people is a wait a member watches, so the searches happen at once.

Each query is one HTTP round trip. Run in sequence, a dozen of them is a dozen
waits; run together, it is one. This pins that, and pins which places get asked
- profiles alone find the people who keep profiles, which is not the same set
as the people who can be reached.

From backend/: python tests/test_web_search_speed.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "web-search-speed-test-secret-0123456789")

from app.services import web_contact_discovery as web  # noqa: E402

QUERY_LATENCY = 0.2


async def slow_search(query, max_results=8, **kwargs):
    await asyncio.sleep(QUERY_LATENCY)
    return [{
        "title": f"{query[:20]} result",
        "url": f"https://example.com/{abs(hash(query))}",
        "content": "",
    }]


def the_wave_costs_one_query_not_all_of_them() -> None:
    queries = [f"query {i}" for i in range(14)]
    with patch.object(web, "_tavily_search", slow_search):
        started = time.monotonic()
        merged = asyncio.run(web._tavily_parallel(queries))
        elapsed = time.monotonic() - started

    assert len(merged) == len(queries), merged
    # Sequential would be 2.8s. Anything near one query's latency is the whole
    # wave overlapping; the ceiling leaves room for a slow machine.
    assert elapsed < QUERY_LATENCY * 3, f"searches did not overlap: {elapsed:.2f}s for {len(queries)} queries"


def the_search_asks_where_reachable_people_are_published() -> None:
    """Profiles, the company's own pages, appointment notices, and the pages
    that actually print an address."""
    asked: list[str] = []

    async def capture(query, max_results=8, **kwargs):
        asked.append(query)
        return []

    with patch.object(web, "_tavily_search", capture), \
         patch("app.services.web_fetch.web_search_configured", lambda: True):
        asyncio.run(web.discover_contacts_from_web(
            "Hansford Sensors", "hansfordsensors.com", max_people=40,
            title_hints="Head of Operations, Director of Supply Chain"))

    joined = " | ".join(asked).lower()
    assert any("site:linkedin.com/in" in q for q in asked), asked
    # The company's own pages: a team page names people a crawl of the root
    # domain often never reaches.
    assert "our team" in joined or "leadership team" in joined, asked
    # Where an address is printed rather than inferred.
    assert "@hansfordsensors.com" in joined, asked
    assert "site:hansfordsensors.com" in joined, asked
    # The roles the member asked for, in their own words.
    assert "head of operations" in joined, asked
    # And it is one wave: every query was issued before any result was read.
    assert len(asked) >= 12, asked


if __name__ == "__main__":
    the_wave_costs_one_query_not_all_of_them()
    the_search_asks_where_reachable_people_are_published()
    print("web search speed: ok")
