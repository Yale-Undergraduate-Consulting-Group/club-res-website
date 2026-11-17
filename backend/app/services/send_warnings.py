"""Warnings, never blocks, about who one send writes to at one company.

People at one company talk to each other, and a team that finds the same cold
email in four inboxes forwards it to IT as spam; enough of that and the
member's Gmail lands in junk everywhere. The send caps stay as they are; this
only tells the member before they confirm, with the people ranked so the page
can offer to keep the most senior few:

* many people at one company (SEND_WARN_PEOPLE_PER_COMPANY, default 5);
* several on one team there, read as the same function from their titles, the
  classification behind Find's function filter (SEND_WARN_PEOPLE_PER_TEAM,
  default 3). The app records no reporting lines, so function is the team.
"""
from __future__ import annotations

import os
import re
from typing import Any

from app.services.company_register import FUNCTIONS, classify_function

#: How many a team warning suggests keeping.
KEEP = 2

_FUNCTION_LABEL = dict(FUNCTIONS)

# Most senior first. A title is ranked by the first band it names; the bands
# read the same words fit_level does, finer grained.
_SENIORITY: tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.I) for p in (
    r"\b(?:ceo|chief\s+executive|(?<!vice[\s-])(?<!vice\s)president|chair(?:man|woman|person)?|(?:co-?)?founder)\b",
    r"\b(?:c[a-z]o|chief\s+[a-z&,\s-]*officer|managing\s+(?:director|partner)|general\s+manager)\b",
    r"\b(?:[escg]vp|(?:executive|senior|corporate|group)\s+vice[\s-]president)\b",
    r"\b(?:vp|vice[\s-]president)\b",
    r"\b(?:head\s+of|director|partner)\b",
    r"\b(?:senior\s+manager|principal|lead)\b",
    r"\b(?:manager)\b",
    r"\b(?:senior|sr\.?)\b",
))


def _threshold(name: str, default: int) -> int:
    try:
        return max(2, int((os.getenv(name) or "").strip()))
    except ValueError:
        return default


def thresholds() -> tuple[int, int]:
    """(people per company, people per team) at which a send is warned about."""
    return _threshold("SEND_WARN_PEOPLE_PER_COMPANY", 5), _threshold("SEND_WARN_PEOPLE_PER_TEAM", 3)


def seniority(title: str | None) -> int:
    """Lower is more senior; a title naming no band ranks last."""
    text = title or ""
    return next((rank for rank, pattern in enumerate(_SENIORITY) if pattern.search(text)), len(_SENIORITY))


def _ranked(people: list[dict[str, Any]]) -> list[int]:
    # Stable: equal ranks keep the order the send lists them in.
    return [p["id"] for p in sorted(people, key=lambda p: seniority(p.get("title")))]


def batch_warnings(people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Warnings for the people one send writes to (dicts with id, title, company).

    Each names the company (and team), how many, the threshold that fired, and
    the people most senior first, so the page can re-count against its own
    ticks and keep the first KEEP."""
    per_company, per_team = thresholds()
    companies: dict[str, list[dict[str, Any]]] = {}
    for person in people:
        key = (person.get("company") or "").strip().lower()
        if key:
            companies.setdefault(key, []).append(person)
    warnings: list[dict[str, Any]] = []
    for group in companies.values():
        company = (group[0].get("company") or "").strip()
        if len(group) >= per_company:
            warnings.append({"kind": "company", "company": company, "count": len(group),
                             "threshold": per_company, "contact_ids": _ranked(group)})
        teams: dict[str, list[dict[str, Any]]] = {}
        for person in group:
            function = classify_function(person.get("title"))
            if function:
                teams.setdefault(function, []).append(person)
        for function, team in teams.items():
            if len(team) >= per_team:
                warnings.append({"kind": "team", "company": company, "team": _FUNCTION_LABEL[function],
                                 "count": len(team), "threshold": per_team, "keep": KEEP,
                                 "contact_ids": _ranked(team)})
    return warnings
