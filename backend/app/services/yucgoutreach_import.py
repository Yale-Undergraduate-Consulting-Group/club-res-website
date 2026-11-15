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
from app.services.company_register import FUNCTION_IDS
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
    origin: str = "member",
    level: str = "working",
    function: str | None = None,
) -> int:
    """Queue one durable company search for a member, enforcing the same
    per-member (one at a time) and club-wide queue limits as the UI. The
    caller owns the write transaction and commit. ``origin='builder'`` marks
    a catalog builder run, which the drain takes only when no member waits."""
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
    from app.services.web_contact_discovery import wanted_level

    research_data: dict[str, Any] = {"level": wanted_level(level)}
    if function in FUNCTION_IDS:
        research_data["function"] = function
    if hints:
        research_data["title_hints"] = hints
    # The entity the member picked in resolve-company, cut to the contract;
    # without one the run behaves exactly as before.
    from app.services.company_entity import sanitize_entity

    clean_entity = sanitize_entity(entity) if entity else None
    if clean_entity:
        research_data["entity"] = clean_entity
    research = json.dumps(research_data)
    cur = await db.execute(
        """INSERT INTO yucgoutreach_discovery_runs (
            user_id, company_name, company_domain,
            max_prospects, worker_concurrency, status, progress_message, research_json, origin
        ) VALUES (?, ?, ?, ?, ?, 'queued', 'Queued', ?, ?)""",
        (
            user_id,
            company_name.strip(),
            (company_domain or "").strip() or None,
            cap,
            worker_concurrency,
            research,
            origin,
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
    return await import_prospect_rows(
        db, rows, results,
        company=run.get("company_name") or "",
        domain=normalize_domain(run.get("company_domain") or ""),
        user_id=user_id,
    )


async def import_prospect_rows(
    db, rows: list[dict[str, Any]], results: list[dict[str, Any]], *, company: str, domain: str, user_id: int,
) -> dict[str, Any]:
    """Bring prospect-shaped rows (first_name, last_name, email, title,
    company, contact_source, contact_profile_url, qualification_notes) into
    the shared contacts table for this member, with the evidence record. One
    path for a run's prospects and for officers read from a company filing,
    so both follow the same address checks and ownership rules. ``results``
    carries outcomes the caller already decided and is extended in place."""
    created = updated = 0
    skipped = len(results)
    held_by: dict[str, int] = {}
    contact_ids: list[int] = []
    for pr in rows:
        pr["mailbox_assessment"] = await assess_address(pr.get("email") or "", actor_id=user_id)

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
                   ai_reason = COALESCE(NULLIF(ai_reason, ''), ?),
                   yale_signal = COALESCE(NULLIF(yale_signal, ''), ?)
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
                    pr.get("yale_signal"),
                    ex["id"],
                ),
            )
            updated += 1
            contact_ids.append(int(ex["id"]))
            results.append({"prospect_id": int(pr["id"]), "contact_id": int(ex["id"]), "outcome": "updated"})
        else:
            cursor = await db.execute(
                """INSERT INTO contacts (name, email, title, company, company_domain, linkedin_url,
                   contact_source, email_verification_status, ai_verdict, ai_reason, confidence, owner_id, yale_signal)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    pr.get("yale_signal"),
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


# Registers that publish a per-company officer list, read on demand when the
# company has none stored (see company_register.fetch_officers_for).
_ON_DEMAND_REGISTERS = ("sec_public", "companies_house")


async def import_register_officers(
    *, user_id: int, company_name: str, company_domain: str | None, entity: dict[str, Any] | None,
) -> dict[str, Any]:
    """Put the officers a company filed on the member's list, with no web
    search: the register's officers (fetched first when the company has none
    stored) become contacts through the same import a search run uses, with
    addresses derived on the company's website the way roster people are.

    Raises 404 when the register holds nothing for the company and 502 when
    its register could not be read. Officers without an address (no website
    known) are counted, not imported."""
    from app.database import get_db
    from app.services.company_entity import sanitize_entity
    from app.services.company_register import fetch_officers_for
    from app.services.roster_email import register_entity_keys, register_officer_contacts

    clean = sanitize_entity(entity) if entity else None
    legal_name = (clean or {}).get("legal_name") or None
    domain = normalize_domain(company_domain or "") or normalize_domain((clean or {}).get("mail_domain") or "")
    keys = await register_entity_keys(company_name, domain, legal_name=legal_name)
    if not keys:
        raise HTTPException(
            404, f"No company filing is on record for {company_name}. Use Find more people to search the web instead.",
        )

    db = await get_db()
    try:
        marks = ",".join("?" * len(keys))
        records = [row_to_dict(r) for r in await (await db.execute(
            f"SELECT id, source, officer_count, company_domain FROM company_register WHERE entity_key IN ({marks})",
            tuple(sorted(keys)),
        )).fetchall()]
    finally:
        await db.close()
    # The register's own website fills a gap; it never replaces the member's.
    domain = domain or next((normalize_domain(r["company_domain"] or "") for r in records if r["company_domain"]), "")

    # Fetching holds no connection of ours: it writes through its own.
    problems: list[str] = []
    for record in records:
        if record["source"] in _ON_DEMAND_REGISTERS and not record["officer_count"]:
            fetched = await fetch_officers_for(int(record["id"]))
            if not fetched.get("ok"):
                problems.append(str(fetched.get("error") or "the register did not answer"))

    officers = await register_officer_contacts(company_name, domain, legal_name=legal_name)
    outcome: dict[str, Any] = {
        "created": 0, "updated": 0, "skipped": 0, "results": [], "held_by_note": "",
        "officers": len(officers), "without_address": 0,
    }
    if not officers:
        if problems:
            raise HTTPException(502, f"Could not read the filing for {company_name}: {problems[0]}")
        outcome["note"] = f"The filing for {company_name} lists no current officers."
        return outcome

    prospects = []
    for index, officer in enumerate(officers, start=1):
        if not officer.get("email"):
            outcome["without_address"] += 1
            continue
        first, _, last = officer["name"].partition(" ")
        prospects.append({
            "id": index,
            "first_name": first,
            "last_name": last.strip(),
            "email": officer["email"],
            "title": officer.get("title"),
            # Filed under the name the member chose, so they list under it.
            "company": company_name,
            "contact_source": officer.get("contact_source") or "roster_register",
            "contact_profile_url": officer.get("source_url"),
            "qualification_notes": officer.get("discovery_context"),
        })
    if not prospects:
        outcome["note"] = (
            f"The filing for {company_name} names {len(officers)} officers, but no website is known "
            "to build their addresses from."
        )
        return outcome

    db = await get_db()
    try:
        result = await import_prospect_rows(
            db, prospects, [], company=company_name, domain=domain, user_id=user_id,
        )
        await db.commit()
    finally:
        await db.close()
    outcome.update({k: result[k] for k in ("created", "updated", "skipped", "results", "held_by_note")})
    return outcome
