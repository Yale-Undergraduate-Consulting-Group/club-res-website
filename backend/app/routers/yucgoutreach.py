"""
YUCGoutreach company discovery: SQL-backed runs, parallel enrichment, Excel export.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from hmac import compare_digest
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.auth_deps import get_current_user
from app.database import get_db, row_to_dict
from app.services.yucgoutreach_discovery import (
    YUCG_MAX_PROSPECTS,
    build_yucgoutreach_excel_bytes,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class YucgOutreachRunCreate(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=500)
    company_domain: str | None = Field(None, max_length=255)
    title_hints: str | None = Field(None, max_length=500)
    # The EntityProfile resolve-company returned (company_entity). Member
    # input: enqueue_discovery_run cuts it to the contract and sizes, and the
    # run only uses it to narrow who it keeps and which domain it tries.
    entity: dict[str, Any] | None = None
    max_prospects: int = Field(60, ge=1, le=800)  # target count of qualified people, not just a cap
    worker_concurrency: int = Field(4, ge=1, le=16)
    # Who to look for: team leads (default), senior leaders or board. The goal
    # counts people at this level; the searches and ranking follow it.
    level: Literal["working", "executive", "board"] = "working"


def _member_research_json(text: str | None) -> str | None:
    """The run's research as members see it. search_errors.first_message is
    the provider's own error text, kept in the database for maintainers; the
    page gets ``kind`` and the plain ``message`` instead."""
    if not text:
        return text
    try:
        research = json.loads(text)
    except ValueError:
        return text
    errors = research.get("search_errors") if isinstance(research, dict) else None
    if not isinstance(errors, dict) or "first_message" not in errors:
        return text
    research["search_errors"] = {k: v for k, v in errors.items() if k != "first_message"}
    return json.dumps(research)


def _run_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "company_name": row.get("company_name"),
        "company_domain": row.get("company_domain"),
        "max_prospects": row.get("max_prospects"),
        "worker_concurrency": row.get("worker_concurrency"),
        "status": row.get("status"),
        "progress_pct": row.get("progress_pct"),
        "progress_message": row.get("progress_message"),
        "prospects_count": row.get("prospects_count"),
        "research_json": _member_research_json(row.get("research_json")),
        "error_message": row.get("error_message"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "completed_at": row.get("completed_at"),
        "origin": row.get("origin") or "member",
    }


# A teammate's finished search that found people, for this long after it
# finished, by a member who is still active: readable by every member, like a
# catalog builder run. Failed, empty, older or deactivated members' runs stay
# private to their owner.
SHARED_RUN_DAYS = 30
_SHARED_MEMBER_RUN = f"""(r.origin = 'member' AND r.status = 'completed' AND r.prospects_count > 0
    AND r.completed_at >= datetime('now', '-{SHARED_RUN_DAYS} days')
    AND r.user_id IN (SELECT id FROM users WHERE is_active = 1))"""


async def _get_run_for_user(run_id: int, user_id: int, *, shared: bool = False) -> dict | None:
    """The member's own run. ``shared`` also admits catalog builder runs and
    a teammate's recent finished runs (see SHARED_RUN_DAYS), which every
    member may read and keep people from, but never delete."""
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT r.* FROM yucgoutreach_discovery_runs r WHERE r.id = ? AND "
            + (f"(r.user_id = ? OR r.origin = 'builder' OR {_SHARED_MEMBER_RUN})" if shared else "r.user_id = ?"),
            (run_id, user_id),
        )
        row = await cur.fetchone()
        return row_to_dict(row) if row else None
    finally:
        await db.close()


@router.post("/runs")
async def create_run(body: YucgOutreachRunCreate, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        from app.services.dispatch_service import begin_write
        from app.services.yucgoutreach_import import enqueue_discovery_run
        await begin_write(db)
        run_id = await enqueue_discovery_run(
            db,
            user_id=user["id"],
            company_name=body.company_name,
            company_domain=body.company_domain,
            title_hints=body.title_hints,
            entity=body.entity,
            max_prospects=body.max_prospects,
            worker_concurrency=body.worker_concurrency,
            level=body.level,
        )
        await db.commit()
    finally:
        await db.close()
    # The scheduler claims queued runs every 10 seconds, which a member is
    # watching the whole of. Kick the same drain now: it takes the lease the
    # same way, so a tick that lands mid-claim finds nothing to take.
    asyncio.create_task(_kick_discovery_drain())
    return {"id": run_id, "status": "queued", "max_prospects": min(body.max_prospects, YUCG_MAX_PROSPECTS)}


async def _kick_discovery_drain() -> None:
    from app.services.yucgoutreach_discovery import drain_queued_yucgoutreach_runs

    try:
        await drain_queued_yucgoutreach_runs()
    except Exception:
        logger.exception("immediate discovery drain failed; the scheduler tick still owns the queue")


@router.get("/runs")
async def list_runs(
    user: dict = Depends(get_current_user),
    limit: int = 50,
    companies: list[str] = Query(default=[]),
):
    """The member's own runs, newest first, plus - for each of ``companies``
    the page has open - the newest run someone else's work makes shared: a
    finished catalog builder run that found anyone, or a teammate's finished
    run within SHARED_RUN_DAYS. A teammate's run says who found it and when
    (``shared_from``), so what the club already found shows at once."""
    limit = max(1, min(limit, 200))
    keys = sorted({c.strip().lower() for c in companies if c and c.strip()})[:50]
    db = await get_db()
    try:
        cur = await db.execute(
            """SELECT * FROM yucgoutreach_discovery_runs WHERE user_id = ?
            ORDER BY id DESC LIMIT ?""",
            (user["id"], limit),
        )
        runs = [_run_to_dict(row_to_dict(r)) for r in await cur.fetchall()]
        if keys:
            marks = ",".join("?" * len(keys))
            cur = await db.execute(
                f"""SELECT r.*, u.name AS shared_by FROM yucgoutreach_discovery_runs r
                    LEFT JOIN users u ON u.id = r.user_id
                    WHERE r.user_id IS NOT ?
                      AND lower(trim(r.company_name)) IN ({marks})
                      AND ((r.origin = 'builder' AND r.status = 'completed' AND r.prospects_count > 0)
                           OR {_SHARED_MEMBER_RUN})
                    ORDER BY r.id DESC""",
                (user["id"], *keys),
            )
            seen: set[str] = set()
            for r in await cur.fetchall():
                key = (r["company_name"] or "").strip().lower()
                if key not in seen:
                    seen.add(key)
                    row = row_to_dict(r)
                    run = _run_to_dict(row)
                    if run["origin"] == "member":
                        run["shared_from"] = {
                            "name": (row.get("shared_by") or "").strip() or "A teammate",
                            "completed_at": row.get("completed_at"),
                        }
                    runs.append(run)
        return runs
    finally:
        await db.close()


@router.get("/role-suggestions")
async def role_suggestions(
    company: str,
    domain: str | None = None,
    hints: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Roles observed at this company (prior runs, SEC roster, shared
    contacts, one LinkedIn search) and, when hints are given, the company's
    equivalents for the roles the member asked for. The equivalence mapping
    charges the assistant allowance only when it calls the model (not on a
    cached answer); see role_suggestions.map_equivalents."""
    from app.services.role_suggestions import suggest_roles

    return await suggest_roles(user_id=user["id"], company=company, domain=domain, hints=hints)


@router.get("/domain-guess")
async def domain_guess(company: str, user: dict = Depends(get_current_user)):
    """Best-effort company website domain, confirmed live before it's
    offered. Never authoritative - a member confirms or corrects it; the
    search never uses it silently on its own."""
    from app.services.company_email_cache import verify_domain_guess

    return await verify_domain_guess(company)


@router.get("/resolve-company")
async def resolve_company_endpoint(q: str, user: dict = Depends(get_current_user)):
    """One company from what a member typed or pasted (a name, or a
    LinkedIn company page URL): display name, a domain only when verified,
    its HQ country and entity profile, and alternatives (the entity's
    offshoots and regions first, then up to four name-alikes). LinkedIn is
    never fetched; at most two web searches run (the LinkedIn title, and
    "@domain" citations when the entity's domain is not already settled),
    both on this member's web quota."""
    from app.services.company_resolve import resolve_company

    return await resolve_company(q, user_id=user["id"])


@router.get("/register")
async def browse_register(
    request: Request,
    q: str | None = None,
    tier: str | None = None,
    country: str | None = None,
    sector: str | None = None,
    min_amount: float | None = None,
    with_officers: bool = False,
    include_hidden: bool = False,
    limit: int = 50,
    offset: int = 0,
    sort: Literal["value", "recent"] = "value",
    user: dict = Depends(get_current_user),
):
    """Browse the company register (SEC listed, SEC Form D filers, Form 5500
    employers, IRS nonprofits, Companies House) that Find people and the
    recommender draw from: one entry per company under its public name, the
    records behind it counted but not listed (see `/register/records`).
    Without a query, `sort=value` puts the companies worth reaching first;
    `sort=recent` is the newest-filing feed. Investment funds, acquisition
    vehicles and IRS group returns are left out unless `include_hidden`.
    Shared and rate-limited by app/services/register_cache.py."""
    from app.services import register_cache

    return await register_cache.browse(dict(
        q=(q or "").strip() or None, tier=tier, country=country, sector=sector, min_amount=min_amount,
        with_officers=with_officers, include_hidden=include_hidden, limit=limit, offset=offset, sort=sort,
    ), request.is_disconnected)


@router.get("/register/summary")
async def register_stats(request: Request, user: dict = Depends(get_current_user)):
    from app.services import register_cache

    return await register_cache.summary(request.is_disconnected)


@router.get("/register/records")
async def register_company_records(key: str, user: dict = Depends(get_current_user)):
    """The records log of one company (an `entity_key` from the list): a line
    per stored record, with its source, date, state and the legal name as
    filed."""
    from app.services.company_register import register_records

    return await register_records(key)


@router.get("/register/entity-people")
async def register_company_people(key: str, user: dict = Depends(get_current_user)):
    """Everyone named on any record of one company, once each."""
    from app.services.company_register import register_entity_people

    return await register_entity_people(key)


class RegisterLoad(BaseModel):
    """One batch from an off-box loader."""

    source: str
    batch: str
    companies: list[dict[str, Any]] = []
    people: dict[str, list[dict[str, Any]]] = {}
    done: bool = False


@router.post("/register/load")
async def load_register(payload: RegisterLoad, request: Request):
    """Accept register rows parsed on another machine.

    The bulk sources are large - the Companies House file is 2.8 GB
    uncompressed and a year of IRS 990 filings is about 1.5 GB across sixteen
    archives - and parsing them competes with serving the site on a t3.small.
    This lets a second machine do the downloading and parsing and post the
    result, while the SQLite file here stays the only thing members read.

    Off by default: with no REGISTER_LOADER_TOKEN set, the endpoint refuses
    everything, so it adds no reachable surface until it is deliberately
    turned on.
    """
    from app.services.company_register import _attach_people, _record_ingest, upsert_companies

    expected = (os.getenv("REGISTER_LOADER_TOKEN") or "").strip()
    if not expected:
        raise HTTPException(404, "Not found")
    offered = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not compare_digest(offered, expected):
        raise HTTPException(401, "Loader token is not valid")
    if not payload.source or not payload.batch:
        raise HTTPException(422, "A source and batch are required")

    written = await upsert_companies(payload.companies) if payload.companies else 0
    attached = 0
    if payload.people:
        attached = await _attach_people({
            (payload.source, key): rows for key, rows in payload.people.items() if rows
        })
    # Only the final call records the batch, so a run that dies halfway is not
    # remembered as complete and the loader repeats it next time.
    if payload.done:
        await _record_ingest(payload.source, payload.batch, len(payload.companies), written, "ok",
                             f"loaded off-box, {attached} people")
    return {"ok": True, "written": written, "people": attached}


@router.post("/register/{register_id}/people")
async def fetch_register_people(register_id: int, user: dict = Depends(get_current_user)):
    """Fetch this company's officers from its own register, on demand.

    Listed companies and UK companies carry no people in the bulk files, so
    they arrive empty and are filled in per company when a member asks for
    one. Deliberately not a bulk job: it is one request per company against a
    rate-limited public API, and the club works companies one at a time.
    """
    from app.services.company_register import fetch_officers_for

    return await fetch_officers_for(register_id)


@router.get("/runs/{run_id}")
async def get_run(run_id: int, user: dict = Depends(get_current_user)):
    row = await _get_run_for_user(run_id, user["id"], shared=True)
    if not row:
        raise HTTPException(404, "Run not found")
    return _run_to_dict(row)


@router.get("/runs/{run_id}/prospects")
async def list_prospects(run_id: int, user: dict = Depends(get_current_user), limit: int = 500):
    if not await _get_run_for_user(run_id, user["id"], shared=True):
        raise HTTPException(404, "Run not found")
    limit = max(1, min(limit, 2000))
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM yucgoutreach_prospects WHERE run_id = ? ORDER BY score DESC, id LIMIT ?",
            (run_id, limit),
        )
        rows = await cur.fetchall()
        # The same level the contacts list gives a title, so the sheet can put
        # the people who match the chosen level first.
        from app.services.company_register import classify_person_level

        out = []
        for r in rows:
            row = row_to_dict(r)
            row["person_level"] = classify_person_level(row.get("title"))
            out.append(row)
        return out
    finally:
        await db.close()


class YucgOutreachImportBody(BaseModel):
    """Which of a run's people to bring on file. Absent means everyone
    eligible, which is what the one-click flow and older callers expect."""

    prospect_ids: list[int] | None = None


@router.post("/runs/{run_id}/import-contacts")
async def import_run_to_contacts(
    run_id: int,
    body: YucgOutreachImportBody | None = None,
    user: dict = Depends(get_current_user),
):
    """Copy verified YUCG prospects into the main contacts table."""
    run = await _get_run_for_user(run_id, user["id"], shared=True)
    if not run:
        raise HTTPException(404, "Run not found")
    prospect_ids = body.prospect_ids if body else None
    if prospect_ids is not None and len(prospect_ids) == 0:
        raise HTTPException(422, "Choose at least one person")
    from app.services.yucgoutreach_import import import_run_prospects
    db = await get_db()
    try:
        result = await import_run_prospects(db, run=run, user_id=user["id"], prospect_ids=prospect_ids)
        await db.commit()
        return {"created": result["created"], "updated": result["updated"],
                "skipped": result["skipped"], "results": result["results"],
                "evidence": result["evidence"]}
    finally:
        await db.close()


@router.get("/runs/{run_id}/export.xlsx")
async def export_run(run_id: int, user: dict = Depends(get_current_user)):
    run = await _get_run_for_user(run_id, user["id"], shared=True)
    if not run:
        raise HTTPException(404, "Run not found")
    try:
        data = await build_yucgoutreach_excel_bytes(run_id)
    except ValueError:
        raise HTTPException(404, "Run not found")
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (run.get("company_name") or "export"))[
        :80
    ]
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="YUCGoutreach_{safe_name}_{run_id}.xlsx"',
        },
    )


@router.delete("/runs/{run_id}")
async def delete_run(run_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        cur = await db.execute(
            "DELETE FROM yucgoutreach_discovery_runs WHERE id = ? AND user_id = ?",
            (run_id, user["id"]),
        )
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "Run not found")
    finally:
        await db.close()
    return {"ok": True, "deleted": run_id}
