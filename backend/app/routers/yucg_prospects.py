"""
Club roster API — officer/director metadata from the SEC + Companies House watch.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth_deps import get_current_user
from app.services.roster_watch import list_rosters, roster_detail, source_stats

router = APIRouter()


@router.get("/rosters")
async def get_rosters(
    user: dict = Depends(get_current_user),
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
    gaps: bool = False,
):
    """Club roster: officer/director metadata from the SEC + Companies House watch."""
    return {"rosters": await list_rosters(q=q, limit=limit, only_gaps=gaps)}


@router.get("/rosters/stats")
async def get_roster_stats(user: dict = Depends(get_current_user)):
    """Per-source yield of the club roster graph (produced / current / mx / imported / replied)."""
    return {"sources": await source_stats()}


@router.get("/rosters/{roster_id}")
async def get_roster(roster_id: int, user: dict = Depends(get_current_user)):
    row = await roster_detail(roster_id)
    if not row:
        raise HTTPException(404, "Roster not found")
    return row


