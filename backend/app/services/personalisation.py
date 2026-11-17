"""The one line a member confirms for each company before the Intro goes.

The club's template leaves a gap after "I'd love to work with {company} on a
project in the new year." for one sentence about that company. The model
suggests it, one call per company and from that company's material only, so a
batch of unrelated companies gets unrelated sentences; the member reads, edits
and confirms each one. A member who writes the line themselves costs nothing.

What the model may see is what every member may see and the club may say out
loud: the register's public notes on the company, and past projects whose
client may be named (disclosure ``public``, or ``name_only`` - for those only
the client, industry and semester, never the deliverable). A ``private``
project is excluded by the query itself, so no caller can widen it. Revenue,
fees and client contacts live in other tables and are never read here.
"""
from __future__ import annotations

import re
from typing import Any

#: Past projects whose client the club may name to anyone.
DISCLOSED = ("public", "name_only")
#: Related projects shown to the model; more is noise in one sentence.
MAX_PROJECTS = 3
LINE_MAX_CHARS = 300

_WORD = re.compile(r"[a-z][a-z&-]{3,}")
# Words every company description shares; matching on them pairs anything with anything.
_COMMON = frozenset({
    "company", "companies", "services", "service", "group", "industry", "industries", "business", "other",
    "with", "from", "that", "this", "their", "they", "have", "into", "about", "growth", "market", "markets",
})

SYSTEM = """You write one sentence for a cold email from the Yale Undergraduate Consulting Group
(YUCG), a team of Yale undergraduates who do consulting projects for companies. The sentence
follows "I'd love to work with <company> on a project in the new year." and says why this
company in particular: something specific about it, or a related project the club has done.

Rules:
- One sentence, at most 35 words, in the first person plural or singular, warm and plain.
- Use only the facts given below. If they are thin, keep the sentence modest and general to
  the company's sector; never invent numbers, news, products, clients or results.
- A past project may be mentioned by its client and industry only as given; do not describe
  its findings, fees, or anyone's contact details.
- No greeting, no sign-off, no quotation marks, no merge fields.
Reply with the sentence only."""

UNAVAILABLE = "AI suggestions are unavailable right now; write this line yourself or leave it empty."


def _words(*texts: str | None) -> set[str]:
    return {w for t in texts for w in _WORD.findall((t or "").lower()) if w not in _COMMON}


async def disclosed_projects(db) -> list[dict[str, Any]]:
    """Past projects whose client may be named. Private ones never leave this query."""
    marks = ",".join("?" * len(DISCLOSED))
    rows = await (await db.execute(
        f"""SELECT id, title, client, industry, areas, description, semester, disclosure
            FROM resources
            WHERE kind = 'past_project' AND disclosure IN ({marks})
              AND client IS NOT NULL AND TRIM(client) <> ''
            ORDER BY id DESC""",
        DISCLOSED,
    )).fetchall()
    return [dict(r) for r in rows]


def related_projects(company: str, notes: str, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The disclosed projects nearest this company: its own past project first,
    then those sharing the most industry words with what the club knows of it."""
    key = company.strip().lower()
    known = _words(notes)
    scored = []
    for project in projects:
        if (project.get("client") or "").strip().lower() == key:
            score = 1000
        else:
            score = len(known & _words(project.get("industry"), project.get("areas")))
        if score:
            scored.append((score, project))
    scored.sort(key=lambda item: -item[0])
    return [project for _, project in scored[:MAX_PROJECTS]]


def _project_fact(project: dict[str, Any]) -> str:
    parts = [f"client {project['client'].strip()}"]
    if (project.get("industry") or "").strip():
        parts.append(f"industry {project['industry'].strip()}")
    if (project.get("semester") or "").strip():
        parts.append(project["semester"].strip())
    if project.get("disclosure") == "public":
        # The deliverable may be named only when the project is public.
        for field in ("title", "areas"):
            if (project.get(field) or "").strip():
                parts.append(f"{field} {project[field].strip()}")
        if (project.get("description") or "").strip():
            parts.append(f"summary {project['description'].strip()[:300]}")
    return "; ".join(parts)


def brief(company: str, notes: str, projects: list[dict[str, Any]], previous: str = "") -> str:
    """The prompt for one company, built from that company's material alone."""
    lines = [f"Company: {company.strip()}"]
    if notes.strip():
        lines.append(f"What the club's register says about it: {notes.strip()[:700]}")
    else:
        lines.append("The club has no notes on this company.")
    if projects:
        lines.append("Related past YUCG projects the club may mention:")
        lines.extend(f"- {_project_fact(p)}" for p in projects)
    if previous.strip():
        lines.append(f"Write a different sentence from this one: {previous.strip()[:LINE_MAX_CHARS]}")
    return "\n".join(lines)


def sources(notes: str, projects: list[dict[str, Any]]) -> list[str]:
    """What the line was written from, shown beside it so the member can judge it."""
    found = ["the club's register notes"] if notes.strip() else []
    found += [f"past project with {p['client'].strip()}" + (f" ({p['semester'].strip()})" if (p.get("semester") or "").strip() else "")
              for p in projects]
    return found


def clean_line(raw: str) -> str:
    """One sentence on one line, without the quotes models like to add."""
    text = re.sub(r"\s+", " ", (raw or "").strip().splitlines()[0] if (raw or "").strip() else "").strip()
    text = text.strip("\"'“”‘’ ")
    if len(text) > LINE_MAX_CHARS:
        text = text[:LINE_MAX_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "."
    return text


def write_line(prompt: str, user_id: int) -> str:
    """One fast-tier call, charged to the club's AI budget like every other."""
    from app.services.llm import complete_text, rank_model_id

    return clean_line(complete_text(prompt, rank_model_id(), SYSTEM, user_id=user_id,
                                    purpose="personalisation", max_tokens=160))
