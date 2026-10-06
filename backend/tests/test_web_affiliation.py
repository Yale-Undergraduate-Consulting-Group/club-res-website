"""Who the open web is allowed to claim works somewhere.

A live search for chewy.com returned Brittany Pettersen - a US Congresswoman
with no connection to Chewy. A Denver Post story about a pet-care bill
mentioned the company and linked her LinkedIn profile; the slug became a name,
the company was stamped on because the search was about the company, and the
address was invented from the house pattern. Three guesses compounding into a
contact the club could have mailed.

From backend/: python tests/test_web_affiliation.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.web_contact_discovery import (  # noqa: E402
    _extract_people_from_results, discover_contacts_from_web,
)

COMPANY, DOMAIN = "Chewy", "chewy.com"

# A politician's profile linked from a story that mentions the company.
POLITICS = {
    "title": "Pet-care bill advances in Congress",
    "url": "https://www.denverpost.com/2026/03/politics/pet-care-bill",
    "content": (
        "Retailers including Chewy backed the measure. Profile: "
        "https://www.linkedin.com/in/brittany-pettersen/ — Rep. Brittany Pettersen, "
        "Congresswoman for Colorado, led the push."
    ),
}
# The person's own profile, naming the company.
PROFILE = {
    "title": "Sumit Singh - Chief Executive Officer - Chewy | LinkedIn",
    "url": "https://www.linkedin.com/in/sumitsingh/",
    "content": "Sumit Singh - Chief Executive Officer at Chewy. Greater Boston.",
}
# The company's own site announcing its own hire.
OWN_SITE = {
    "title": "Chewy names new Head of Customer Care",
    "url": "https://investor.chewy.com/news/appointment",
    "content": "Chewy today named Dana Ruiz - Head of Customer Care, effective April.",
}
# Trade press reporting that the company took someone on.
APPOINTMENT = {
    "title": "Chewy hires supply chain lead",
    "url": "https://petnews.example.com/story",
    "content": "Chewy has hired Priya Raman - Director of Supply Chain, the company confirmed.",
}
# A real person with a real job - at a different company.
COMPETITOR = {
    "title": "Amazon hires away pet-category buyer",
    "url": "https://news.example.com/amazon-hire",
    "content": "Marcus Webb - Senior Buyer at Amazon, who previously covered pet supplies.",
}
# Company and person in one roundup, with nothing tying them together.
ROUNDUP = {
    "title": "Pet retail quarterly roundup",
    "url": "https://trade.example.com/roundup",
    "content": "Chewy grew orders. Elsewhere, Tom Baxter - Director of Marketing spoke at the show.",
}

ALL = [POLITICS, PROFILE, OWN_SITE, APPOINTMENT, COMPETITOR, ROUNDUP]


def leads() -> dict[str, str]:
    return {p["name"]: p["affiliation"]
            for p in _extract_people_from_results(ALL, COMPANY, DOMAIN)}


def contacts() -> dict[str, str]:
    async def fake_search(query, max_results=8, **kwargs):
        return ALL

    with patch("app.services.web_fetch.web_search_configured", lambda: True), \
         patch("app.services.web_fetch.web_search", fake_search):
        rows = asyncio.run(discover_contacts_from_web(COMPANY, DOMAIN, max_people=20))
    return {row["name"]: row["email"] for row in rows}


def the_named_person_must_be_tied_to_the_company() -> None:
    found = leads()

    # The reported failure. A profile link inside somebody else's article is
    # evidence about that article, not about who works at the company.
    assert "Brittany Pettersen" not in found, found
    # Same shape without the politics: a real buyer, at a competitor.
    assert "Marcus Webb" not in found, found

    # And the people who genuinely belong are still found, each with the reason.
    assert found["Sumit Singh"] == "linkedin_profile", found
    assert found["Dana Ruiz"] == "company_site", found
    assert found["Priya Raman"] == "press_appointment", found
    # Named in the same roundup as the company, with nothing tying them: a lead
    # at most, and the weakest kind.
    assert found["Tom Baxter"] == "press_mention", found


def an_address_is_only_invented_where_employment_was_shown() -> None:
    written = contacts()

    assert "Brittany Pettersen" not in written, written
    assert "brittany.pettersen@chewy.com" not in written.values(), written
    assert "Marcus Webb" not in written, written
    # Proximity in one article is not a reason to guess someone's mailbox at a
    # company, so the weakest lead never becomes a contact.
    assert "Tom Baxter" not in written, written

    assert written["Sumit Singh"] == "sumit.singh@chewy.com", written
    assert written["Dana Ruiz"] == "dana.ruiz@chewy.com", written
    assert written["Priya Raman"] == "priya.raman@chewy.com", written


def a_public_office_title_is_never_a_lead() -> None:
    """Even on a page that ties them to the company, an elected official holds
    office rather than a job there."""
    result = [{
        "title": "Chewy hosts lawmakers at distribution centre",
        "url": "https://petnews.example.com/visit",
        "content": "Chewy welcomed Senator Maria Cantwell - Senator for Washington, to the site.",
    }]
    assert "Maria Cantwell" not in leads(), leads()
    assert [p["name"] for p in _extract_people_from_results(result, COMPANY, DOMAIN)] == []


def a_title_naming_another_employer_wins_over_the_search() -> None:
    """Found live in this club's own Microsoft contacts: "David Warner II -
    CEO & Founding Trainer @ Warner Digital", stored as a Microsoft person
    because a Microsoft search surfaced his profile. The title says where he
    works; the search only says what was typed."""
    results = [
        {"title": "David Warner II - CEO & Founding Trainer @ Warner Digital - LinkedIn",
         "url": "https://www.linkedin.com/in/davidwarnerii/",
         "content": "David Warner II - CEO & Founding Trainer @ Warner Digital. Microsoft MVP since 2019."},
        {"title": "Katy George - Corporate Vice President - Microsoft | LinkedIn",
         "url": "https://www.linkedin.com/in/katygeorge/",
         "content": "Katy George - Corporate Vice President at Microsoft."},
    ]
    found = {p["name"]: p["affiliation"]
             for p in _extract_people_from_results(results, "Microsoft", "microsoft.com")}
    assert "David Warner II" not in found, found
    assert found["Katy George"] == "linkedin_profile", found


def a_page_describing_itself_is_not_a_person() -> None:
    """Four live rows in this club's Microsoft contacts, each with an invented
    address: "Choose People" (a Support article's own words), "Activity Image"
    (alt text beside a profile link), "Copilot Extensibility" (a product, with
    the real person parsed as the title) and "Steve Mathias B1a579" (a real
    person wearing his LinkedIn slug suffix)."""
    results = [
        {"title": "Find people and contacts - Microsoft Support",
         "url": "https://support.microsoft.com/help/people",
         "content": "Choose People - Find people and contacts - Microsoft Support."},
        {"title": "Microsoft Build sessions", "url": "https://news.microsoft.com/build",
         "content": "Copilot Extensibility - Patrick Rodgers. https://www.linkedin.com/in/msjonguy/"},
        {"title": "Digital sovereignty", "url": "https://news.microsoft.com/sovereignty",
         "content": "Activity Image - We are building the most comprehensive platform. "
                    "https://www.linkedin.com/in/judsonalthoff/"},
        {"title": "Steve Mathias B1a579 - Customer Success Account Manager - Microsoft | LinkedIn",
         "url": "https://www.linkedin.com/in/steve-mathias-b1a579/",
         "content": "Steve Mathias B1a579 - Customer Success Account Manager at Microsoft."},
        {"title": "Katy George - Corporate Vice President - Microsoft | LinkedIn",
         "url": "https://www.linkedin.com/in/katygeorge1/",
         "content": "Katy George - Corporate Vice President at Microsoft. "
                    "Transformation Leader - Shaping the AI-powered future of work."},
    ]
    found = {p["name"] for p in _extract_people_from_results(results, "Microsoft", "microsoft.com")}
    # The real people, and only them - with the slug suffix taken off the name
    # rather than mailed as part of it.
    assert found == {"Steve Mathias", "Katy George"}, found


def the_model_reading_search_results_cannot_invent_employment() -> None:
    """The other door into the same mistake: a model is handed twenty search
    snippets and asked for "people at Chewy". It will answer with whoever the
    snippets name - and with people they do not name at all."""
    import app.services.yucgoutreach_discovery as discovery

    results = [POLITICS, PROFILE]
    model_answer = {"people": [
        {"full_name": "Brittany Pettersen", "title": "Congresswoman",
         "linkedin_url": "https://www.linkedin.com/in/brittany-pettersen/"},
        {"full_name": "Sumit Singh", "title": "Chief Executive Officer",
         "linkedin_url": "https://www.linkedin.com/in/sumitsingh/"},
        {"full_name": "Imaginary Colleague", "title": "VP Marketing", "linkedin_url": ""},
    ]}

    async def fake_search(query, max_results=8, **kwargs):
        return results

    async def fake_llm(prompt):
        return model_answer

    with patch.object(discovery, "_tavily_search", fake_search), \
         patch.object(discovery, "_llm_json", fake_llm), \
         patch("app.services.web_fetch.web_search_configured", lambda: True):
        rows = asyncio.run(discovery._tavily_name_seeds(COMPANY, DOMAIN, 10, []))

    # Named on a page that mentions the company, but as an office holder.
    assert [r["name"] for r in rows] == ["Sumit Singh"], rows
    # A name that appears in no snippet is the model's invention, not a lead.
    assert all(r["name"] != "Imaginary Colleague" for r in rows), rows
    assert rows[0]["affiliation_evidence"] == "linkedin_profile", rows


def a_page_lends_its_standing_only_to_the_person_it_is_about() -> None:
    """A profile page is about one person; a company page is about the company.
    Granting the page's standing to every name printed on it is how support
    copy and session listings became staff with invented addresses."""
    results = [
        {"title": "Katy George - Corporate Vice President - Microsoft | LinkedIn",
         "url": "https://www.linkedin.com/in/katygeorge1/",
         "content": "Katy George - Corporate Vice President at Microsoft. "
                    "Founding Trainer - Shaping tomorrow."},
        {"title": "Returns and refunds", "url": "https://support.microsoft.com/returns",
         "content": "Order Status - Track a shipment at Microsoft."},
    ]
    found = {p["name"]: p for p in _extract_people_from_results(results, "Microsoft", "microsoft.com")}

    # The other name on her profile page does not inherit her page's standing.
    assert "Founding Trainer" not in found, found
    assert found["Katy George"]["affiliation"] == "linkedin_profile", found
    # Her own link stays attached; nobody else's name may carry it.
    assert found["Katy George"]["linkedin_url"].endswith("/katygeorge1/"), found

    # Shipping copy on the company's own site parses like a person and a job.
    # A lead at most, which means no address is ever derived from it.
    assert found.get("Track a shipment", {}).get("affiliation") in (None, "press_mention"), found


def one_match_must_not_swallow_the_next_person() -> None:
    """The title ran to the end of the line, so a session listing matched as
    "Copilot Extensibility - Patrick Rodgers. John Nguyen - Principal..." and
    the real name disappeared inside the first match's title."""
    results = [{
        "title": "Microsoft Build sessions", "url": "https://news.microsoft.com/build",
        "content": "Copilot Extensibility - Patrick Rodgers. "
                   "John Nguyen - Principal Engineering Manager at Microsoft.",
    }]
    found = {p["name"]: p["title"]
             for p in _extract_people_from_results(results, "Microsoft", "microsoft.com")}
    assert "Copilot Extensibility" not in found, found
    assert found["John Nguyen"] == "Principal Engineering Manager at Microsoft", found


if __name__ == "__main__":
    the_named_person_must_be_tied_to_the_company()
    an_address_is_only_invented_where_employment_was_shown()
    a_public_office_title_is_never_a_lead()
    the_model_reading_search_results_cannot_invent_employment()
    a_title_naming_another_employer_wins_over_the_search()
    a_page_describing_itself_is_not_a_person()
    a_page_lends_its_standing_only_to_the_person_it_is_about()
    one_match_must_not_swallow_the_next_person()
    print("web affiliation: ok")