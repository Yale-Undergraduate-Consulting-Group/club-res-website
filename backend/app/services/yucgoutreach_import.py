"""Shared entry points for the Find people run lifecycle that both the manual
UI (yucgoutreach router) and the one-click outreach flow use, so a company
search enqueued or imported either way follows one set of rules."""
from __future__ import annotations

import json
import os
from typing import Any

from fastapi import HTTPException

from app.database import row_to_dict
from app.services.contact_intelligence import assess_address, ingest_contact
from app.services.contact_scraper import is_valid_person_contact, normalize_domain, sanitize_email
from app.services.yucgoutreach_discovery import YUCG_MAX_PROSPECTS


async def enqueue_discovery_run(
    db,
    *,
    user_id: int,
    company_name: str,
    company_domain: str | None,
    title_hints: str | None,
    max_prospects: int,
    worker_concurrency: int = 4,
    entity: dict[str, Any] | None = None,
) -> int:
    """Queue one durable company search for a member, enforcing the same
    per-member (one at a time) and club-wide queue limits as the UI. The
    caller owns the write transaction and commit."""
    cap = max(1, min(int(max_prospects), YUCG_MAX_PROSPECTS))
    active = await (await db.execute(
        "SELECT COUNT(*) AS n FROM yucgoutreach_discovery_runs WHERE user_id = ? AND status IN ('queued','running')",
        (user_id,),
    )).fetchone()
    if int(active["n"] or 0):
        raise HTTPException(409, "You already have a company search queued or running")
    club_limit = max(1, int(os.getenv("DISCOVERY_QUEUE_LIMIT", "20") or 20))
    club = await (await db.execute(
        "SELECT COUNT(*) AS n FROM yucgoutreach_discovery_runs WHERE status IN ('queued','running')"
    )).fetchone()
    if int(club["n"] or 0) >= club_limit:
        raise HTTPException(429, "The club search queue is full; try again after a current search finishes")
    hints = (title_hints or "").strip()[:500]
    research_data: dict[str, Any] = {"title_hints": hints} if hints else {}
    # The entity the member picked in resolve-company, cut to the contract;
    # without one the run behaves exactly as before.
    from app.services.company_entity import sanitize_entity

    clean_entity = sanitize_entity(entity) if entity else None
    if clean_entity:
        research_data["entity"] = clean_entity
    research = json.dumps(research_data) if research_data else None
    cur = await db.execute(
        """INSERT INTO yucgoutreach_discovery_runs (
            user_id, company_name, company_domain,
            max_prospects, worker_concurrency, status, progress_message, research_json
        ) VALUES (?, ?, ?, ?, ?, 'queued', 'Queued', ?)""",
        (
            user_id,
            company_name.strip(),
            (company_domain or "").strip() or None,
            cap,
            worker_concurrency,
            research,
        ),
    )
    return int(cur.lastrowid)


async def import_run_prospects(
    db, *, run: dict[str, Any], user_id: int, prospect_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Copy a completed run's non-junk prospects into the shared contacts
    table for this member. Returns counts plus the ids of every contact this
    member can now use (created or updated), so a caller can attach them to
    a campaign without a second lookup, and one outcome per prospect so the
    picker can move exactly the people that landed. The caller commits.

    ``prospect_ids`` narrows the import to those rows: a member adding one
    person from a search must not silently import the other fifty-nine. Ids
    from another run are reported as skipped rather than imported - the run
    is the ownership boundary and the caller has already checked it."""
    run_id = int(run["id"])
    wanted: list[int] | None = None
    if prospect_ids is not None:
        wanted = sorted({int(pid) for pid in prospect_ids})
    cur = await db.execute(
        """SELECT * FROM yucgoutreach_prospects
           WHERE run_id = ? AND email IS NOT NULL AND email != ''
           AND (ai_verdict IS NULL OR ai_verdict != 'junk')
           ORDER BY score DESC""",
        (run_id,),
    )
    rows = [row_to_dict(r) for r in await cur.fetchall()]
    results: list[dict[str, Any]] = []
    if wanted is not None:
        eligible = {int(r["id"]): r for r in rows}
        in_run = {
            int(r["id"]) for r in await (await db.execute(
                "SELECT id FROM yucgoutreach_prospects WHERE run_id = ?", (run_id,),
            )).fetchall()
        }
        rows = [eligible[pid] for pid in wanted if pid in eligible]
        for pid in wanted:
            if pid in eligible:
                continue
            results.append({
                "prospect_id": pid, "contact_id": None, "outcome": "skipped",
                "reason": "no usable address" if pid in in_run else "not in this run",
            })
    created = updated = 0
    skipped = len(results)
    held_by: dict[str, int] = {}
    contact_ids: list[int] = []
    for pr in rows:
        pr["mailbox_assessment"] = await assess_address(pr.get("email") or "", actor_id=user_id)
    company = run.get("company_name") or ""
    domain = normalize_domain(run.get("company_domain") or "")

    def _skip(pr: dict[str, Any], reason: str) -> None:
        nonlocal skipped
        skipped += 1
        results.append({"prospect_id": int(pr["id"]), "contact_id": None, "outcome": "skipped", "reason": reason})

    def _row(pr: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": " ".join(p for p in [pr.get("first_name"), pr.get("last_name")] if p).strip(),
            "email": sanitize_email(pr.get("email") or ""),
            "title": pr.get("title"),
            "company": pr.get("company") or company,
            "company_domain": domain,
            "linkedin_url": pr.get("linkedin_url"),
            "contact_source": pr.get("contact_source") or "yucgoutreach",
            "mailbox_assessment": pr.get("mailbox_assessment"),
        }

    def _sources(pr: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        sources: list[dict[str, Any]] = []
        source_url = (pr.get("contact_profile_url") or pr.get("contact_url") or "").strip()
        if source_url:
            sources.append({
                "url": source_url,
                "excerpt": (pr.get("qualification_notes") or "")[:500] or None,
                "observed_at": None,
            })
        if pr.get("linkedin_url"):
            sources.append({"url": pr["linkedin_url"], "excerpt": None, "observed_at": None})
        return sources, source_url

    for pr in rows:
        row = _row(pr)
        email = row["email"]
        name = row["name"]
        if not email or not is_valid_person_contact(row, company_name=company, domain=domain):
            _skip(pr, "not a person's work address")
            continue
        ex = await (await db.execute(
            """SELECT c.id, c.owner_id, COALESCE(u.name, u.email) AS owner
               FROM contacts c LEFT JOIN users u ON u.id = c.owner_id
               WHERE c.email = ?""", (email,))).fetchone()
        if ex:
            if ex["owner_id"] is not None and int(ex["owner_id"]) != user_id:
                # Silently dropping these is how two members discover each
                # other from a client. Count them by owner so the caller can
                # say who to ask.
                owner = ex["owner"] or "another member"
                held_by[owner] = held_by.get(owner, 0) + 1
                _skip(pr, f"already worked by {owner}")
                continue
            await db.execute(
                """UPDATE contacts SET name = COALESCE(NULLIF(name, ''), ?),
                   title = COALESCE(NULLIF(title, ''), ?),
                   company = COALESCE(NULLIF(company, ''), ?),
                   company_domain = COALESCE(NULLIF(company_domain, ''), ?),
                   linkedin_url = COALESCE(NULLIF(linkedin_url, ''), ?),
                   contact_source = COALESCE(NULLIF(contact_source, ''), ?),
                   email_verification_status = COALESCE(NULLIF(email_verification_status, ''), ?),
                   ai_verdict = COALESCE(NULLIF(ai_verdict, ''), ?),
                   ai_reason = COALESCE(NULLIF(ai_reason, ''), ?)
                   WHERE id = ?""",
                (
                    name or None,
                    pr.get("title"),
                    pr.get("company") or company or None,
                    domain or None,
                    pr.get("linkedin_url"),
                    pr.get("contact_source") or "yucgoutreach",
                    pr.get("email_verification_status"),
                    pr.get("ai_verdict"),
                    pr.get("ai_reason"),
                    ex["id"],
                ),
            )
            updated += 1
            contact_ids.append(int(ex["id"]))
            results.append({"prospect_id": int(pr["id"]), "contact_id": int(ex["id"]), "outcome": "updated"})
        else:
            cursor = await db.execute(
                """INSERT INTO contacts (name, email, title, company, company_domain, linkedin_url,
                   contact_source, email_verification_status, ai_verdict, ai_reason, confidence, owner_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    email,
                    pr.get("title"),
                    pr.get("company") or company,
                    domain or None,
                    pr.get("linkedin_url"),
                    pr.get("contact_source") or "yucgoutreach",
                    pr.get("email_verification_status"),
                    pr.get("ai_verdict"),
                    pr.get("ai_reason"),
                    "medium",
                    user_id,
                ),
            )
            created += 1
            contact_ids.append(int(cursor.lastrowid))
            results.append({"prospect_id": int(pr["id"]), "contact_id": int(cursor.lastrowid), "outcome": "created"})

    evidence_rows = []
    for pr in rows:
        row = _row(pr)
        if not row["email"]:
            continue
        sources, source_url = _sources(pr)
        existing = await (await db.execute("SELECT id, owner_id FROM contacts WHERE email = ?", (row["email"],))).fetchone()
        if existing and (existing["owner_id"] is None or int(existing["owner_id"]) == user_id):
            evidence_rows.append(await ingest_contact(
                db, contact={**row, "id": existing["id"]}, actor_id=user_id,
                origin="published_by_independent_source" if source_url else None,
                sources=sources,
            ))
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "contact_ids": contact_ids,
        "results": results,
        "evidence": evidence_rows,
        # Who to ask, rather than an unexplained smaller number.
        "held_by": held_by,
        "held_by_note": "; ".join(
            f"{count} already worked by {owner}" for owner, count in sorted(held_by.items())
        ),
    }
