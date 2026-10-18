"""Real club-project facts a member can cite in outreach - opt-in only.

The `projects` table also backs semester/team-assignment admin views, so
this router only ever surfaces rows an admin has explicitly marked
`discussable`. NDA-covered or unmarked projects must never appear here.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends

from app.auth_deps import get_current_user
from app.database import get_db, row_to_dict

router = APIRouter()

_MAX_RESULTS = 5


def _matches_company(company: str, *fields: str | None) -> bool:
    """Case-insensitive keyword overlap between the recipient's company and
    a project's citable text. Short/common words are excluded so almost
    everything doesn't "match"."""
    words = [w for w in re.split(r"[^a-z0-9]+", (company or "").lower()) if len(w) >= 3]
    if not words:
        return False
    haystack = " ".join((f or "").lower() for f in fields)
    return any(w in haystack for w in words)


@router.get("/suggest-citations")
async def suggest_citations(company: str = "", user: dict = Depends(get_current_user)):
    """Discussable projects and team experience worth citing to `company`.

    Matches by keyword overlap when there's any signal, otherwise falls
    back to most-recent first. Any member drafting outreach can call this -
    it never touches anything but rows an admin opted in.
    """
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id, client_name, description, semester, created_at
               FROM projects
               WHERE discussable = 1
               ORDER BY created_at DESC, id DESC"""
        )
        project_rows = [row_to_dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            """SELECT COALESCE(u.name, u.email) AS user_name, upa.role_in_project,
                      p.client_name, p.semester, p.description, p.created_at
               FROM user_project_assignments upa
               JOIN projects p ON p.id = upa.project_id
               JOIN users u ON u.id = upa.user_id
               WHERE p.discussable = 1
               ORDER BY p.created_at DESC, p.id DESC"""
        )
        team_rows = [row_to_dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()

    # Stable sort: rows already ordered most-recent-first from SQL, so a
    # match-first partition keeps that order as its fallback within groups.
    ranked_projects = sorted(
        project_rows, key=lambda r: not _matches_company(company, r.get("description"), r.get("client_name"))
    )[:_MAX_RESULTS]
    ranked_team = sorted(
        team_rows, key=lambda r: not _matches_company(company, r.get("description"), r.get("client_name"))
    )[:_MAX_RESULTS]

    return {
        "projects": [
            {
                "id": r["id"],
                "client_name": r.get("client_name"),
                "description": r.get("description"),
                "semester": r.get("semester"),
            }
            for r in ranked_projects
        ],
        "team_experience": [
            {
                "user_name": r.get("user_name"),
                "role_in_project": r.get("role_in_project"),
                "client_name": r.get("client_name"),
                "semester": r.get("semester"),
            }
            for r in ranked_team
        ],
    }
