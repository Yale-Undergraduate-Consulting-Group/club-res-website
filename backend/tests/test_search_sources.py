"""Find people reads public people and organisation pages besides profiles.

The first wave also asks for org charts (theorg.com, Crunchbase person pages),
press releases quoting named executives (PR Newswire, Business Wire, GlobeNewswire),
conference speaker lists, public profile directories (RocketReach, ZoomInfo,
SignalHire - names and titles from the snippet only) and the company's own
leadership pages. Every person read from them still has to pass the employer
check and the name checks, and carries the kind of page as ``contact_source``.

No network: the search layer is replaced.
From backend/: python tests/test_search_sources.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "search-sources-test-secret-0123456789")

from app.routers.campaigns import _needs_mailbox_precheck  # noqa: E402
from app.services import web_contact_discovery as W  # noqa: E402
from app.services.contact_directory import source_label  # noqa: E402
from app.services.contact_scraper import WEB_SOURCES, confidence_for_contact_dict  # noqa: E402

COMPANY, DOMAIN = "Hansford Sensors", "hansfordsensors.com"

THEORG = {
    "title": "Jane Doe - Director of Finance - Hansford Sensors | The Org",
    "url": "https://theorg.com/org/hansford-sensors/org-chart/jane-doe",
    "content": "Jane Doe is the Director of Finance at Hansford Sensors.",
}
# Another company's chart that only mentions this one as a past employer.
THEORG_OTHER_EMPLOYER = {
    "title": "Mia Stone - Director of Finance - Beta Corp | The Org",
    "url": "https://theorg.com/org/beta/org-chart/mia-stone",
    "content": "Mia Stone is Director of Finance at Beta Corp. Formerly at Hansford Sensors.",
}
CRUNCHBASE = {
    "title": "Sam Ortiz - VP of Operations - Hansford Sensors | Crunchbase Person Profile",
    "url": "https://www.crunchbase.com/person/sam-ortiz",
    "content": "Sam Ortiz is VP of Operations at Hansford Sensors.",
}
PRESS = {
    "title": "Hansford Sensors launches Atlas",
    "url": "https://www.prnewswire.com/news-releases/hansford-sensors-launches-atlas.html",
    "content": (
        'NEW YORK, June 3 /PRNewswire/ -- Hansford Sensors today launched Atlas. "Atlas changes how teams work," '
        'said Priya Nair, Head of Product at Hansford Sensors. "Customers asked for it." '
        'Joe Smith, CTO of Beta, added: "Working with Hansford Sensors has been great."'
    ),
}
SPEAKER = {
    "title": "Speakers - Sensor Summit",
    "url": "https://summit.example.org/speakers",
    "content": "Ana Ruiz, Director of Strategy, Hansford Sensors. Ana leads strategy.",
}
DIRECTORY = {
    "title": "Lena Fischer - Director of Marketing - Hansford Sensors | RocketReach",
    "url": "https://rocketreach.co/lena-fischer-email_1234",
    "content": "Lena Fischer works at Hansford Sensors as Director of Marketing.",
}
COMPANY_PAGE = {
    "title": "Leadership - Hansford Sensors",
    "url": "https://hansfordsensors.com/about/leadership",
    "content": "Tom Hart - Chief Operating Officer. Dana Lee - Vice President of Marketing.",
}

EXPECTED = {
    "Jane Doe": "web_theorg",
    "Sam Ortiz": "web_crunchbase",
    "Priya Nair": "web_press",
    "Ana Ruiz": "web_speaker",
    "Lena Fischer": "web_directory",
    "Tom Hart": "web_company_page",
}


def answers(query: str) -> list[dict]:
    if "site:theorg.com" in query:
        return [THEORG, THEORG_OTHER_EMPLOYER]
    if "site:crunchbase.com" in query:
        return [CRUNCHBASE]
    if "site:prnewswire.com" in query:
        return [PRESS]
    if "site:rocketreach.co" in query:
        return [DIRECTORY]
    if f"site:{DOMAIN}" in query and "leadership team" in query:
        return [COMPANY_PAGE]
    if W._SPEAKER_TERMS in query:
        return [SPEAKER]
    return []


def read_the_wave() -> tuple[dict[str, dict], dict, list[str]]:
    asked: list[str] = []
    skips: dict = {}

    async def search_once(query, max_results=8, page=0):
        asked.append(query)
        return answers(query)

    async def run() -> list[dict]:
        run_state, token = W.begin_search_run()
        try:
            return await W.discover_contacts_from_web(
                COMPANY, DOMAIN, max_people=40, skips=skips, levels=["working", "executive"])
        finally:
            W.end_search_run(token)

    with patch.object(W, "_search_once", search_once), \
         patch("app.services.web_fetch.web_search_configured", lambda: True):
        rows = asyncio.run(run())
    return {r["name"]: r for r in rows}, skips, asked


def every_public_source_yields_candidates_through_the_employer_check() -> None:
    people, skips, _ = read_the_wave()
    got = {name: row["contact_source"] for name, row in people.items()}
    for name, source in EXPECTED.items():
        assert got.get(name) == source, (name, got)
        assert people[name]["email"].endswith("@" + DOMAIN), people[name]
    # Dana is on the same company page as Tom: same source, same standing.
    assert got.get("Dana Lee") == "web_company_page", got
    # The employer check still decides: a chart naming the company as a past
    # employer, and a quote from someone at another company, are not people here.
    assert "Mia Stone" not in people and "Joe Smith" not in people, got
    assert "Mia Stone" in skips["other_employer"].values(), skips


def the_wave_asks_each_kind_of_public_page() -> None:
    queries = W.first_wave_queries(COMPANY, DOMAIN, "", None, ["working", "executive"])
    joined = "\n".join(queries)
    for site in ("theorg.com", "crunchbase.com", "prnewswire.com", "businesswire.com", "globenewswire.com",
                 "rocketreach.co", "zoominfo.com", "signalhire.com"):
        assert f"site:{site}" in joined, site
    assert any(q.startswith(f"site:{DOMAIN} ") and "leadership team" in q for q in queries), queries
    assert any(W._SPEAKER_TERMS in q for q in queries), queries
    # Every query is asked once, and the wave stays inside its documented bound.
    assert len(queries) == len(set(queries)) and len(queries) <= 25, len(queries)


def the_next_page_of_each_public_search_is_a_later_round() -> None:
    pool = W.round_query_pool(COMPANY, DOMAIN, "", None, ["working", "executive"])
    second = {q for q, page in pool if page == 1}
    for site in ("site:theorg.com", "site:crunchbase.com", "site:prnewswire.com", "site:rocketreach.co"):
        assert any(site in q for q in second), site
    first = set(W.first_wave_queries(COMPANY, DOMAIN, "", None, ["working", "executive"]))
    assert not {q for q, page in pool if page == 0} & first


def each_kind_of_page_is_trusted_as_the_web_is() -> None:
    assert {"web_discovery", *EXPECTED.values()} <= WEB_SOURCES
    # A profile on the open web stays plain web discovery, however it was found.
    assert W.web_source("https://www.linkedin.com/in/sam-ortiz", W._SPEAKER_TERMS, DOMAIN) == "web_discovery"
    row = {"email": "sam.ortiz@hansfordsensors.com", "name": "Sam Ortiz", "title": "VP of Operations",
           "linkedin_url": "https://www.linkedin.com/in/sam-ortiz", "company": COMPANY}
    plain = confidence_for_contact_dict(dict(row, contact_source="web_discovery"), company_name=COMPANY, domain=DOMAIN)
    assert {confidence_for_contact_dict(dict(row, contact_source=s), company_name=COMPANY, domain=DOMAIN)
            for s in WEB_SOURCES} == {plain}
    assert all(_needs_mailbox_precheck({"contact_source": s}) for s in WEB_SOURCES)
    assert source_label("web_press") == "Press release" and source_label("web_theorg") == "The Org"


def test_search_sources() -> None:
    every_public_source_yields_candidates_through_the_employer_check()
    the_wave_asks_each_kind_of_public_page()
    the_next_page_of_each_public_search_is_a_later_round()
    each_kind_of_page_is_trusted_as_the_web_is()


if __name__ == "__main__":
    test_search_sources()
    print("search sources: ok")
