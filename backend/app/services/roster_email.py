"""Roster work-email derivation + cache-first reads for Find people.

Contract: a person exists only from name+role evidence; mailboxes here are DERIVED
from learned domain patterns and must pass strict name alignment. A harvested or
role inbox never enters this path. Bounced addresses are tombstoned (email_status=
'bounced'), never silently rebuilt.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import get_db
from app.services.company_email_cache import build_email_for_person
from app.services.contact_scraper import (
    is_employee_outreach_email,
    names_entity_or_place,
    normalize_domain,
    sanitize_email,
    strict_email_name_alignment,
)
from app.services.email_verifier import get_mx_cached
from app.services.roster_watch import _ensure_roster, company_key, roster_detail


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).isoformat()


def _recheck_days() -> int:
    return max(7, min(int(os.getenv("ROSTER_EMAIL_RECHECK_DAYS", "30") or 30), 180))


def _drain_limit() -> int:
    return max(1, min(int(os.getenv("ROSTER_EMAIL_DRAIN_LIMIT", "10") or 10), 50))


async def derive_roster_email(full_name: str, domain: str | None) -> str | None:
    """Work email for a known person, or None. Never a role inbox; must align to the name."""
    dom = normalize_domain(domain or "")
    parts = re.findall(r"[A-Za-z][a-z'.-]+", full_name or "")
    if not dom or len(parts) < 2:
        return None
    return _row_email(full_name, dom, await _build_email(full_name, dom))


async def _build_email(full_name: str, dom: str, ctx=None) -> str | None:
    try:
        return sanitize_email(await build_email_for_person(full_name, dom, ctx) or "")
    except Exception:
        return None


def _row_email(full_name: str, dom: str, built: str | None) -> str | None:
    if built and is_employee_outreach_email(built) and strict_email_name_alignment(full_name, built):
        return built
    return None


async def cached_roster_contacts(
    company_name: str, domain: str | None, limit: int = 100, *, pin_domain: bool = False
) -> list[dict[str, Any]]:
    """Current roster people as contact dicts for the Find people merge stage.

    Rows carry NO email unless a derived+aligned work email already exists; the
    verify pipeline rebuilds missing emails through the same learned-pattern path,
    so cache rows go through identical gates as live sources.

    ``pin_domain`` says the member typed ``domain`` on this run. It then replaces
    the roster's stored domain before anything is read, because below the stored
    domain wins over the run's: a wrong one (globaldata.com for Meta) otherwise
    kept minting addresses no matter what the member entered.
    """
    name = (company_name or "").strip()
    if not name:
        return []
    key = company_key(name)
    dom = normalize_domain(domain or "")
    if pin_domain and dom:
        await _ensure_roster(name, dom, pin_domain=True)
    like = f"%{name}%"
    db = await get_db()
    try:
        rows = await (
            await db.execute(
                """SELECT id FROM company_rosters
                   WHERE company_key=? OR company_name=? OR company_name LIKE ?
                      OR (? != '' AND IFNULL(company_domain,'') = ?)
                   ORDER BY (company_key=?) DESC, current_count DESC
                   LIMIT 4""",
                (key, name, like, dom, dom, key),
            )
        ).fetchall()
        people: dict[str, dict[str, Any]] = {}
        for r in rows:
            detail = await roster_detail(int(r["id"]))
            if not detail:
                continue
            roster_dom = normalize_domain(detail.get("company_domain") or dom or "")
            for person in detail.get("people") or []:
                if person.get("employment") != "current":
                    continue
                if person.get("email_status") == "bounced":
                    continue
                full = (person.get("full_name") or "").strip()
                norm = (person.get("normalized_name") or "").strip() or norm_key(full)
                if not full or not norm:
                    continue
                # Rows stored before ingestion checked names ("Plc Barclays",
                # "West London") stay in the table; they are not read as people.
                if names_entity_or_place(full):
                    continue
                derived = _row_email(full, roster_dom, person.get("inferred_email"))
                if not derived and roster_dom and person.get("email_status") not in {"collision", "invalid_domain"}:
                    derived = _row_email(full, roster_dom, await _build_email(full, roster_dom))
                candidate = {
                    "name": full,
                    "email": derived or "",
                    "title": person.get("title") or "",
                    "company": detail.get("company_name") or name,
                    "company_domain": roster_dom or dom,
                    "contact_source": "roster_sec" if str(person.get("source") or "").startswith("sec") else "roster_cache",
                    "source_url": person.get("source_url"),
                    "discovery_context": f"club roster · {person.get('source')}",
                    "_roster_norm": norm,
                }
                prev = people.get(norm)
                sec_rank = lambda row: 0 if row["contact_source"] == "roster_sec" else 1  # noqa: E731
                if prev is None or sec_rank(candidate) < sec_rank(prev):
                    people[norm] = candidate
        return list(people.values())[: max(1, min(int(limit), 200))]
    finally:
        await db.close()


async def roster_refresh_note(company_name: str, domain: str | None = None) -> str:
    """One line for Find people empty/error copy: register status and last SEC error."""
    name = (company_name or "").strip()
    if not name:
        return ""
    key = company_key(name)
    dom = normalize_domain(domain or "")
    db = await get_db()
    try:
        row = await (
            await db.execute(
                """SELECT source_status, people_count, last_error FROM company_rosters
                   WHERE company_key=? OR company_name=?
                      OR (? != '' AND IFNULL(company_domain,'') = ?)
                   ORDER BY (company_key=?) DESC LIMIT 1""",
                (key, name, dom, dom, key),
            )
        ).fetchone()
    finally:
        await db.close()
    if not row:
        return "no club roster yet"
    err = (row["last_error"] or "").strip()
    line = f"{row['source_status'] or 'pending'} · {int(row['people_count'] or 0)} officers"
    return f"{line} · {err[:180]}" if err else line


def norm_key(name: str) -> str:
    from app.services.contact_scraper import person_name_key

    return person_name_key(name)


async def _ensure_emails(roster: dict[str, Any], *, mx_cache: dict[str, tuple[bool | None, list[str]]] | None = None) -> int:
    """Derive/repair work emails for one roster; MX-check every mailbox host. Returns touched count."""
    from app.services.company_email_cache import load_reconcile_context
    from app.services.mail_domain_map import list_mail_hosts, store_mx_result

    dom = normalize_domain(roster.get("company_domain") or "")
    roster_id = int(roster["id"])
    if not dom or not roster_id:
        return 0
    mx_cache = mx_cache if mx_cache is not None else {}
    hosts = await list_mail_hosts(dom)
    for host in hosts:
        if host not in mx_cache:
            mx_cache[host] = await get_mx_cached(host, None)
        try:
            await store_mx_result(host, mx_cache[host][0])
        except Exception:
            pass
    usable = [h for h in hosts if mx_cache.get(h, (None, []))[0] is not False]
    if not usable:
        db = await get_db()
        try:
            cur = await db.execute(
                """UPDATE company_roster_people SET email_status='invalid_domain', email_checked_at=?
                   WHERE roster_id=? AND IFNULL(email_status,'') NOT IN ('bounced','invalid_domain','mx_valid','previously_delivered','collision')""",
                (_iso(), roster_id),
            )
            await db.execute(
                "UPDATE company_rosters SET next_email_check_at=? WHERE id=?",
                (_iso(_now() + timedelta(days=7)), roster_id),
            )
            await db.commit()
            return cur.rowcount or 0
        finally:
            await db.close()
    ctx = await load_reconcile_context({dom})
    ctx.mx_cache = {h: (False if v[0] is False else True, v[1]) for h, v in mx_cache.items()}

    db = await get_db()
    touched = 0
    try:
        rows = await (
            await db.execute(
                """SELECT id, full_name, inferred_email, email_status FROM company_roster_people
                   WHERE roster_id=? AND employment='current'
                     AND (
                         inferred_email IS NULL OR inferred_email=''
                         OR IFNULL(email_status,'') IN ('', 'inconclusive', 'invalid_domain', 'collision')
                     )""",
                (roster_id,),
            )
        ).fetchall()
        preferred = usable[0]
        held = {int(r["id"]) for r in rows}
        extras = await (
            await db.execute(
                """SELECT id, full_name, inferred_email, email_status FROM company_roster_people
                   WHERE roster_id=? AND employment='current'
                     AND IFNULL(inferred_email,'') != ''
                     AND IFNULL(email_status,'') NOT IN ('bounced','previously_delivered','collision')""",
                (roster_id,),
            )
        ).fetchall()
        extra_rows = []
        for row in extras:
            pid = int(row["id"])
            if pid in held:
                continue
            host = normalize_domain((row["inferred_email"] or "").rsplit("@", 1)[-1])
            if host != preferred:
                extra_rows.append(row)
                held.add(pid)
        if extra_rows:
            rows = list(rows) + extra_rows
        # Collision guard: a derived address that fits two current people is not
        # evidence for either — leave both empty until a real pattern separates them.
        taken: dict[str, list[int]] = {}
        for other in await (
            await db.execute(
                """SELECT id, IFNULL(inferred_email,'') AS email FROM company_roster_people
                   WHERE roster_id=? AND employment='current' AND IFNULL(inferred_email,'') != ''""",
                (roster_id,),
            )
        ).fetchall():
            taken.setdefault(other["email"].lower(), []).append(int(other["id"]))
        planned: list[tuple[int, str]] = []
        for row in rows:
            planned.append((int(row["id"]), await _build_email(row["full_name"], dom, ctx) or ""))
        counts: dict[str, int] = {}
        for _pid, email in planned:
            if email:
                counts[email.lower()] = counts.get(email.lower(), 0) + 1
        for pid, email in planned:
            if not email:
                await db.execute(
                    "UPDATE company_roster_people SET inferred_email=NULL, email_status='inconclusive', email_checked_at=? WHERE id=?",
                    (_iso(), pid),
                )
                touched += 1
                continue
            low = email.lower()
            others = [i for i in taken.get(low, []) if i != pid]
            if counts.get(low, 0) > 1 or others:
                await db.execute(
                    "UPDATE company_roster_people SET inferred_email=NULL, email_status='collision', email_checked_at=? WHERE id=?",
                    (_iso(), pid),
                )
            else:
                host = low.rsplit("@", 1)[-1]
                host_mx = mx_cache.get(host, (None, []))[0]
                status = "mx_valid" if host_mx is True else "inconclusive"
                await db.execute(
                    "UPDATE company_roster_people SET inferred_email=?, email_status=?, email_checked_at=? WHERE id=?",
                    (email, status, _iso(), pid),
                )
            touched += 1
        await db.execute(
            "UPDATE company_rosters SET next_email_check_at=? WHERE id=?",
            (_iso(_now() + timedelta(days=_recheck_days())), roster_id),
        )
        await db.commit()
        return touched
    finally:
        await db.close()

async def refresh_roster_on_demand(
    company_name: str, domain: str | None = None, *, pin_domain: bool = False
) -> list[dict[str, Any]]:
    """SEC-refresh a single company now (Find people cold path), then return cache rows."""
    from app.services.roster_watch import load_tickers_or_reason, refresh_roster

    # _ensure_roster fills an empty domain, and replaces a stored one when the
    # member typed this one (pin_domain).
    roster = await _ensure_roster(company_name, domain, pin_domain=pin_domain)
    if int(roster.get("id") or 0):
        tickers, tickers_error = await load_tickers_or_reason()
        await refresh_roster(roster, tickers, tickers_error=tickers_error)
        await _ensure_emails(roster)
    return await cached_roster_contacts(company_name, domain)


async def drain_roster_emails() -> dict[str, Any]:
    """Background MX/pattern maintenance over due rosters (free: DNS + learned patterns only)."""
    db = await get_db()
    claimed: list[dict[str, Any]] = []
    try:
        now_s = _iso()
        lease = _iso(_now() + timedelta(seconds=200))
        await db.execute("BEGIN IMMEDIATE")
        rows = await (
            await db.execute(
                """SELECT * FROM company_rosters
                   WHERE IFNULL(company_domain,'') != ''
                     AND people_count > 0
                     AND IFNULL(next_email_check_at,'') <= ?
                   ORDER BY next_email_check_at, id
                   LIMIT ?""",
                (now_s, _drain_limit()),
            )
        ).fetchall()
        for row in rows:
            record = dict(row)
            await db.execute(
                "UPDATE company_rosters SET next_email_check_at=? WHERE id=?",
                (lease, record["id"]),
            )
            claimed.append(record)
        await db.commit()
    finally:
        await db.close()
    mx_cache: dict[str, tuple[bool | None, list[str]]] = {}
    touched = 0
    for record in claimed:
        try:
            touched += await _ensure_emails(record, mx_cache=mx_cache)
        except Exception:
            continue
    return {"ok": True, "claimed": len(claimed), "touched": touched}


async def drain_roster_verification(limit: int | None = None) -> dict[str, Any]:
    """Spend the free provider credits on the best roster guesses: adjudicated-real
    people first, then rank. A decisive verdict sticks; exhausted/disabled stops the
    run so the remaining credits are not burned retrying a closed door."""
    if (os.getenv("ROSTER_PROVIDER_VERIFY", "1") or "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by env"}
    cap = limit if limit is not None else max(1, min(int(os.getenv("ROSTER_PROVIDER_ATTEMPTS", "25") or 25), 60))
    db = await get_db()
    try:
        due = await (
            await db.execute(
                """SELECT p.id, p.full_name, p.inferred_email
                   FROM company_roster_people p JOIN company_rosters r ON r.id = p.roster_id
                   WHERE p.employment='current' AND IFNULL(p.inferred_email,'') != ''
                     AND p.email_status = 'mx_valid'
                     AND IFNULL(p.email_provider_checked_at,'') = ''
                   ORDER BY (CASE WHEN IFNULL(p.verdict,'')='real' THEN 0 ELSE 1 END),
                            r.current_count DESC, p.id
                   LIMIT ?""",
                (cap,),
            )
        ).fetchall()
    finally:
        await db.close()
    if not due:
        return {"ok": True, "checked": 0, "confirmed": 0, "rejected": 0}

    from app.services.contact_intelligence import assess_address

    checked = confirmed = rejected = 0
    unknown_streak = 0
    for row in due:
        try:
            result = await assess_address(row["inferred_email"], actor_id=0, external=True, manual=False)
        except Exception:
            continue
        state = str((result or {}).get("mailbox") or "")
        provider = str((result or {}).get("provider_state") or "")
        if provider in ("disabled", "exhausted"):
            break
        if provider == "unavailable" or state in ("inconclusive", "not_checked", ""):
            unknown_streak += 1
            if unknown_streak >= 5:
                break
            continue
        unknown_streak = 0
        db = await get_db()
        try:
            if state in ("provider_high_confidence", "provider_medium_confidence"):
                await db.execute(
                    "UPDATE company_roster_people SET email_status='provider_valid', email_provider_checked_at=? WHERE id=?",
                    (_iso(), row["id"]),
                )
                confirmed += 1
            elif state == "accept_all_or_risky":
                await db.execute(
                    "UPDATE company_roster_people SET email_status='catch_all', email_provider_checked_at=? WHERE id=?",
                    (_iso(), row["id"]),
                )
            else:
                await db.execute(
                    "UPDATE company_roster_people SET email_provider_checked_at=? WHERE id=?",
                    (_iso(), row["id"]),
                )
                if state == "recipient_rejected":
                    rejected += 1
            await db.commit()
        finally:
            await db.close()
        checked += 1
    return {"ok": True, "checked": checked, "confirmed": confirmed, "rejected": rejected}


async def apply_provider_verdict(db, email: str, verdict: str) -> None:
    """A Verifalia hard Failure decays the learned pattern and tombstones the
    address even before it is ever mailed. 'Success' deliberately does NOT
    boost — only human replies move confidence up, so the loop stays honest."""
    if verdict != "rejected" or not email:
        return
    cand = await (
        await db.execute("SELECT id FROM email_candidates WHERE email=? COLLATE NOCASE", (email,))
    ).fetchone()
    if cand:
        await apply_mailbox_proof(db, int(cand["id"]), "permanent_failure_observed")
    else:
        row = await (
            await db.execute(
                "SELECT id, full_name, inferred_email, email_status FROM company_roster_people WHERE inferred_email=? COLLATE NOCASE LIMIT 1",
                (email,),
            )
        ).fetchone()
        if row and row["email_status"] != "bounced":
            from app.services.company_email_cache import pattern_for_email

            key = pattern_for_email(email, row["full_name"])
            dom = email.split("@")[-1].lower() if "@" in email else ""
            if key and dom:
                await db.execute(
                    """UPDATE company_email_patterns
                       SET confidence = MAX(0.05, confidence - 0.12),
                           verified_samples = MAX(0, verified_samples - 1),
                           updated_at = CURRENT_TIMESTAMP
                       WHERE company_domain = ? AND pattern_key = ?""",
                    (dom, key),
                )
            await db.execute(
                "UPDATE company_roster_people SET email_status='bounced', email_checked_at=? WHERE id=?",
                (_iso(), row["id"]),
            )
            from app.services.mail_domain_map import record_mailbox_outcome
            await record_mailbox_outcome(email, kind="bounce", db=db)
    # A provider-confirmed deadbox never gets mailed, wherever it lives.
    await db.execute(
        """INSERT INTO candidate_suppressions(email,state,observed_at)
           VALUES(?, 'permanent_failure', ?) ON CONFLICT(email) DO NOTHING""",
        (email, _iso()),
    )



async def apply_mailbox_proof(db, candidate_id: int, state: str) -> None:
    """Send-time truth loop: replies up-weight the learned pattern, permanent
    failures down-weight it and tombstone the roster person's email.

    Called INSIDE record_mailbox_event before its check row is inserted, so a
    prior identical check means the event already moved the pattern. Permanent
    failures dedupe on the suppression row that records right after this call.
    """
    if state not in ("permanent_failure_observed", "human_reply_observed", "previously_delivered"):
        return
    cand = await (
        await db.execute(
            "SELECT email, company_domain, pattern_key FROM email_candidates WHERE id=?",
            (candidate_id,),
        )
    ).fetchone()
    if not cand or not cand["pattern_key"] or not cand["company_domain"]:
        return
    if state == "permanent_failure_observed":
        if await (
            await db.execute(
                "SELECT 1 FROM candidate_suppressions WHERE email=? COLLATE NOCASE", (cand["email"],)
            )
        ).fetchone():
            return
    elif await (
        await db.execute(
            "SELECT 1 FROM email_checks WHERE candidate_id=? AND result=? LIMIT 1", (candidate_id, state)
        )
    ).fetchone():
        return
    if state == "permanent_failure_observed":
        delta, sample_delta, floor = -0.12, -1, 0.05
    elif state == "human_reply_observed":
        delta, sample_delta, floor = 0.12, 1, 0.05
    else:
        delta, sample_delta, floor = 0.04, 0, 0.05
    mail_host = (cand["email"] or "").rsplit("@", 1)[-1].lower() if cand["email"] else ""
    hosts = [h for h in {mail_host, str(cand["company_domain"] or "").lower()} if h]
    for host in hosts:
        await db.execute(
            """UPDATE company_email_patterns
               SET confidence = MIN(0.98, MAX(?, confidence + ?)),
                   verified_samples = MAX(0, verified_samples + ?),
                   updated_at = CURRENT_TIMESTAMP
               WHERE company_domain = ? AND pattern_key = ?""",
            (floor, delta, sample_delta, host, cand["pattern_key"]),
        )
    from app.services.mail_domain_map import record_mailbox_outcome

    kind = {
        "permanent_failure_observed": "bounce",
        "human_reply_observed": "reply",
        "previously_delivered": "delivered",
    }[state]
    await record_mailbox_outcome(
        cand["email"],
        kind=kind,
        website_domain=str(cand["company_domain"] or "") or None,
        db=db,
    )
    if state == "permanent_failure_observed":
        await db.execute(
            """UPDATE company_roster_people
               SET email_status='bounced', email_checked_at=?
               WHERE inferred_email = ? COLLATE NOCASE""",
            (_iso(), cand["email"]),
        )
