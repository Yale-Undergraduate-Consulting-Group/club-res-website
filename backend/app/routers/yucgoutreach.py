"""
YUCGoutreach company discovery: SQL-backed runs, parallel enrichment, Excel export.
"""
from __future__ import annotations

import asyncio
import logging
import os
from hmac import compare_digest
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
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
    max_prospects: int = Field(250, ge=1, le=800)
    worker_concurrency: int = Field(4, ge=1, le=16)


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
        "research_json": row.get("research_json"),
        "error_message": row.get("error_message"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "completed_at": row.get("completed_at"),
    }


async def _get_run_for_user(run_id: int, user_id: int) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM yucgoutreach_discovery_runs WHERE id = ? AND user_id = ?",
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
async def list_runs(user: dict = Depends(get_current_user), limit: int = 50):
    limit = max(1, min(limit, 200))
    db = await get_db()
    try:
        cur = await db.execute(
            """SELECT * FROM yucgoutreach_discovery_runs WHERE user_id = ?
            ORDER BY id DESC LIMIT ?""",
            (user["id"], limit),
        )
        rows = await cur.fetchall()
        return [_run_to_dict(row_to_dict(r)) for r in rows]
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
    equivalents for the roles the member asked for."""
    from app.services.generation_policy import reserve_assistant_request
    from app.services.role_suggestions import suggest_roles

    if (hints or "").strip():
        # The equivalence mapping is a model call; use the advisory allowance.
        await reserve_assistant_request(user["id"])
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
    q: str | None = None,
    tier: str | None = None,
    country: str | None = None,
    sector: str | None = None,
    min_amount: float | None = None,
    with_officers: bool = False,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(get_current_user),
):
    """Browse the bulk company register (SEC listed, SEC Form D filers,
    Companies House) that Find people and the recommender draw from."""
    from app.services.company_register import search_register

    return await search_register(
        q=q, tier=tier, country=country, sector=sector, min_amount=min_amount,
        with_officers=with_officers, limit=limit, offset=offset,
    )


@router.get("/register/summary")
async def register_stats(user: dict = Depends(get_current_user)):
    from app.services.company_register import register_summary

    return await register_summary()


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


@router.get("/register/{register_id}/people")
async def register_people(register_id: int, user: dict = Depends(get_current_user)):
    """Officers named on this company's own filings - evidence, not guesses."""
    db = await get_db()
    try:
        rows = await (await db.execute(
            """SELECT full_name, relationship, observed_at, source_url
               FROM company_register_people WHERE register_id = ? ORDER BY full_name""",
            (register_id,),
        )).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        await db.close()


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
    row = await _get_run_for_user(run_id, user["id"])
    if not row:
        raise HTTPException(404, "Run not found")
    return _run_to_dict(row)


@router.get("/runs/{run_id}/prospects")
async def list_prospects(run_id: int, user: dict = Depends(get_current_user), limit: int = 500):
    if not await _get_run_for_user(run_id, user["id"]):
        raise HTTPException(404, "Run not found")
    limit = max(1, min(limit, 2000))
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM yucgoutreach_prospects WHERE run_id = ? ORDER BY score DESC, id LIMIT ?",
            (run_id, limit),
        )
        rows = await cur.fetchall()
        return [row_to_dict(r) for r in rows]
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
    run = await _get_run_for_user(run_id, user["id"])
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
    run = await _get_run_for_user(run_id, user["id"])
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
