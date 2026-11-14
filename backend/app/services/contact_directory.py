"""The contact directory: every person the club knows, the best proof its
mailbox has, and whether anyone has reached them.

Contacts, Find-people prospects, the send ledger and the verification store each
answered a slice of "who is this and may I write to them" with their own
predicate. This module is the one place that joins them, keyed on the lowercase
address - the only identity the sources share.

``reach_for`` is the single definition of "has the club reached this person".
``proof_for`` (mailbox_verdicts) is the single definition of how well their
address is proven. Everything here is read-only.
"""
from __future__ import annotations

from typing import Any, Iterable

from app.routers.yucgoutreach import _SHARED_MEMBER_RUN
from app.services.company_claims import claims_for, company_key
from app.services.mailbox_verdicts import backlog_status, proof_for

PROOFS = ("replied", "delivered", "bounced", "mailbox_confirmed", "catch_all", "mailbox_rejected", "unverified")
REACH_STATES = ("replied", "bounced", "contacted", "queued", "not_contacted")
# A usable address: the club delivered to it, or a mailbox check confirmed it.
# A catch-all domain accepts anything, so it proves nothing about the person.
READY_PROOFS = ("delivered", "mailbox_confirmed")

# Filtering by proof or reach needs both computed per person, so those requests
# scan the matching people up to this many (newest first) and page the result.
# ponytail: past the limit the answer says ``capped`` instead of pretending.
SCAN_LIMIT = 20000
_CHUNK = 500

_SOURCE_LABELS = {
    "domain_scrape": "Company website",
    "web_discovery": "Web search",
    "inferred": "Guessed from format",
    "linkedin_inferred": "Guessed from format",
    "roster_sec": "SEC filings",
    "roster_cache": "Club roster",
    "yucgoutreach": "Find people",
    "studio": "Added in Drafts",
    "release_keep": "Release",
}

_LEDGER_STATUSES = ("pending", "sending", "sent", "replied", "bounced")
# A member who moved a person to Replied or Meeting heard back; Contacted or
# Closed means the club wrote, whoever did it and wherever.
_PIPELINE_REPLIED = ("replied", "meeting")
_PIPELINE_CONTACTED = ("contacted", "closed")


def source_label(raw: str | None) -> str:
    key = (raw or "").strip().lower()
    if not key:
        return "Not recorded"
    if key in _SOURCE_LABELS:
        return _SOURCE_LABELS[key]
    return "Guessed from format" if key.endswith("_pattern") else key.replace("_", " ").capitalize()


def _marks(count: int) -> str:
    return ",".join("?" * count)


def _stamp(value: Any) -> str:
    """One sortable form for the mix of 'YYYY-MM-DD HH:MM:SS' and ISO stamps."""
    return str(value or "").replace("T", " ")[:19]


async def reach_for(
    db,
    emails: Iterable[str],
    viewer_id: int,
    companies: dict[str, tuple[str | None, str | None]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Whether the club has reached each address, by whom, when, how often.

    state: replied > bounced > contacted > queued > not_contacted. A send in
    any campaign by any member counts, and so does a member having moved the
    contact to Contacted/Replied by hand. ``companies`` maps an address to
    (company name, domain); with it, ``claimed_by`` names the member holding
    that company when it is not the viewer.
    """
    wanted = {e.strip().lower() for e in emails if e and e.strip()}
    found: dict[str, dict[str, Any]] = {
        e: {"replied": False, "replied_at": "", "bounced": False, "pipeline_contacted": False, "queued": False,
            "sends": 0, "last_sent": "", "last_uid": None, "queued_uid": None}
        for e in wanted
    }
    if not wanted:
        return {}
    # A big page costs less as one pass over the (small) ledger than as many
    # IN lists; either way only requested addresses are kept.
    scoped = f"AND lower(c.email) IN ({_marks(len(wanted))})" if len(wanted) <= _CHUNK else ""
    args = sorted(wanted) if scoped else []

    ledger = await (await db.execute(
        f"""SELECT lower(c.email) AS email, cc.status, cc.sent_at, cc.replied_at,
                   COALESCE(cc.sent_by_user_id, camp.sender_user_id, camp.owner_user_id) AS uid,
                   (SELECT COUNT(*) FROM outreach_messages m
                     WHERE m.campaign_contact_id = cc.id AND m.sent_at IS NOT NULL) AS messages
            FROM campaign_contacts cc
            JOIN contacts c ON c.id = cc.contact_id
            LEFT JOIN campaigns camp ON camp.id = cc.campaign_id
            WHERE (cc.sent_at IS NOT NULL OR cc.status IN ({_marks(len(_LEDGER_STATUSES))})) {scoped}""",
        [*_LEDGER_STATUSES, *args],
    )).fetchall()
    for row in ledger:
        person = found.get(row["email"])
        if person is None:
            continue
        status = row["status"]
        sent = bool(row["sent_at"]) or status in ("sent", "replied", "bounced")
        if sent:
            person["sends"] += max(int(row["messages"] or 0), 1)
            when = _stamp(row["sent_at"])
            if when >= person["last_sent"]:
                person["last_sent"], person["last_uid"] = when, row["uid"]
        elif status in ("pending", "sending"):
            person["queued"] = True
            person["queued_uid"] = row["uid"] if row["uid"] is not None else person["queued_uid"]
        if status == "replied" or row["replied_at"]:
            person["replied"] = True
            person["replied_at"] = max(person["replied_at"], _stamp(row["replied_at"]))
        if status == "bounced":
            person["bounced"] = True

    pipeline = await (await db.execute(
        f"""SELECT lower(c.email) AS email, c.pipeline_status FROM contacts c
            WHERE c.pipeline_status IN ({_marks(len(_PIPELINE_REPLIED) + len(_PIPELINE_CONTACTED))}) {scoped}""",
        [*_PIPELINE_REPLIED, *_PIPELINE_CONTACTED, *args],
    )).fetchall()
    for row in pipeline:
        person = found.get(row["email"])
        if person is None:
            continue
        if row["pipeline_status"] in _PIPELINE_REPLIED:
            person["replied"] = True
        else:
            person["pipeline_contacted"] = True

    uids = {int(uid) for p in found.values() for uid in (p["last_uid"], p["queued_uid"]) if uid is not None}
    names: dict[int, str] = {}
    if uids:
        users = await (await db.execute(
            f"SELECT id, COALESCE(NULLIF(name, ''), email) AS name FROM users WHERE id IN ({_marks(len(uids))})",
            sorted(uids),
        )).fetchall()
        names = {int(u["id"]): u["name"] for u in users}

    claims: dict[str, dict[str, Any]] = {}
    if companies:
        wanted_companies = [
            {"company_name": name, "company_domain": domain}
            for email, (name, domain) in companies.items() if email in wanted
        ]
        for start in range(0, len(wanted_companies), _CHUNK):
            claims.update(await claims_for(wanted_companies[start:start + _CHUNK]))

    reach: dict[str, dict[str, Any]] = {}
    for email, p in found.items():
        if p["replied"]:
            state, uid, at = "replied", p["last_uid"], p["replied_at"] or p["last_sent"]
        elif p["bounced"]:
            state, uid, at = "bounced", p["last_uid"], p["last_sent"]
        elif p["sends"] or p["pipeline_contacted"]:
            state, uid, at = "contacted", p["last_uid"], p["last_sent"]
        elif p["queued"]:
            state, uid, at = "queued", p["queued_uid"], ""
        else:
            state, uid, at = "not_contacted", None, ""
        claim = claims.get(company_key(*(companies or {}).get(email, (None, None))))
        reach[email] = {
            "state": state,
            "by": names.get(int(uid)) if uid is not None else None,
            "by_me": uid is not None and int(uid) == int(viewer_id),
            "at": at or None,
            "sends": p["sends"],
            "claimed_by": claim["member"] if claim and int(claim["member_id"]) != int(viewer_id) else None,
        }
    return reach


def is_ready(proof: str, reach: dict[str, Any]) -> bool:
    """A usable address nobody has written to or queued, at a company no other member holds."""
    return proof in READY_PROOFS and reach["state"] == "not_contacted" and not reach["claimed_by"]


async def _proofs(db, emails: list[str]) -> dict[str, str]:
    proofs: dict[str, str] = {}
    for start in range(0, len(emails), _CHUNK):
        proofs.update(await proof_for(db, emails[start:start + _CHUNK]))
    return proofs


def _like(term: str) -> str:
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _people_sql(viewer: dict, q: str | None, company: str | None) -> tuple[str, list[Any], str]:
    """One row per lowercase address the viewer may see.

    Contacts follow list_contacts' rule (members: their own plus unowned; admins:
    all). Prospects come from runs the viewer may read - the rule
    yucgoutreach._get_run_for_user applies. Where an address is in both, the
    contact row wins and fills its blanks from the prospect rows.
    """
    admin = viewer.get("role") == "admin"
    params: list[Any] = ["%@%"]
    contact_scope = ""
    if not admin:
        contact_scope = "AND (c.owner_id = ? OR c.owner_id IS NULL)"
        params.append(viewer["id"])
    params.extend(["%@%", viewer["id"]])
    sql = f"""
        WITH people AS (
            SELECT lower(c.email) AS email, 0 AS pri, c.id AS rid,
                   COALESCE(c.name, '') AS name, COALESCE(c.title, '') AS title,
                   COALESCE(c.company, '') AS company, COALESCE(c.company_domain, '') AS domain,
                   COALESCE(c.contact_source, '') AS source, COALESCE(c.created_at, '') AS found_at,
                   NULL AS run_id
            FROM contacts c
            WHERE c.email LIKE ? {contact_scope}
            UNION ALL
            SELECT lower(p.email), 1, p.id,
                   trim(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, '')),
                   COALESCE(p.title, ''), COALESCE(NULLIF(p.company, ''), r.company_name, ''),
                   COALESCE(r.company_domain, ''), COALESCE(p.contact_source, ''), COALESCE(p.created_at, ''), p.run_id
            FROM yucgoutreach_prospects p
            JOIN yucgoutreach_discovery_runs r ON r.id = p.run_id
            WHERE p.email LIKE ?
              AND (r.user_id = ? OR r.origin = 'builder' OR {_SHARED_MEMBER_RUN})
        ),
        ranked AS (
            SELECT email, pri, rid, found_at, run_id,
                   ROW_NUMBER() OVER (PARTITION BY email ORDER BY pri, rid DESC) AS rn,
                   name, MAX(NULLIF(name, '')) OVER w AS any_name,
                   title, MAX(NULLIF(title, '')) OVER w AS any_title,
                   company, MAX(NULLIF(company, '')) OVER w AS any_company,
                   domain, MAX(NULLIF(domain, '')) OVER w AS any_domain,
                   source, MAX(NULLIF(source, '')) OVER w AS any_source
            FROM people
            WINDOW w AS (PARTITION BY email)
        ),
        directory AS (
            SELECT email, found_at, pri = 0 AS on_file, CASE WHEN pri = 0 THEN rid END AS contact_id,
                   CASE WHEN pri = 1 THEN run_id END AS run_id, CASE WHEN pri = 1 THEN rid END AS prospect_id,
                   COALESCE(NULLIF(name, ''), any_name, '') AS name,
                   COALESCE(NULLIF(title, ''), any_title, '') AS title,
                   COALESCE(NULLIF(company, ''), any_company, '') AS company,
                   COALESCE(NULLIF(domain, ''), any_domain, '') AS domain,
                   COALESCE(NULLIF(source, ''), any_source, '') AS source
            FROM ranked WHERE rn = 1
        )"""
    where: list[str] = []
    if q and q.strip():
        like = _like(q)
        where.append(
            "(name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\')"
        )
        params.extend([like] * 4)
    if company and company.strip():
        where.append("company LIKE ? ESCAPE '\\'")
        params.append(_like(company))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return sql, params, clause


async def _enrich(db, rows: list[dict[str, Any]], viewer_id: int) -> None:
    emails = [r["email"] for r in rows]
    proofs = await _proofs(db, emails)
    reach = await reach_for(db, emails, viewer_id, {r["email"]: (r["company"], r["domain"]) for r in rows})
    for r in rows:
        r["proof"] = proofs.get(r["email"], "unverified")
        r["reach"] = reach[r["email"]]
        r["ready"] = is_ready(r["proof"], r["reach"])
        r["source"] = source_label(r["source"] or ("" if r["on_file"] else "yucgoutreach"))
        r["on_file"] = bool(r["on_file"])
        del r["domain"], r["found_at"]


_ORDER = " ORDER BY found_at DESC, email"


async def search(
    db,
    viewer: dict,
    *,
    q: str | None = None,
    company: str | None = None,
    proof: str | None = None,
    reach: str | None = None,
    ready: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """A page of the directory. Text filters and paging run in SQL; proof, reach
    and ready are properties of the joined evidence, so those requests compute
    them for everyone matching the text (up to SCAN_LIMIT) and page afterwards -
    the filter never runs on an already-cut page."""
    cte, params, clause = _people_sql(viewer, q, company)
    if not (proof or reach or ready):
        total = int((await (await db.execute(f"{cte} SELECT COUNT(*) AS n FROM directory{clause}", params)).fetchone())["n"])
        page = await (await db.execute(
            f"{cte} SELECT * FROM directory{clause}{_ORDER} LIMIT ? OFFSET ?", [*params, limit, offset]
        )).fetchall()
        rows = [dict(r) for r in page]
        await _enrich(db, rows, viewer["id"])
        return {"items": rows, "total": total, "limit": limit, "offset": offset, "capped": False}

    rows, capped = await _scan(db, viewer, q, company)
    rows = [
        r for r in rows
        if (not proof or r["proof"] == proof)
        and (not reach or r["reach"]["state"] == reach)
        and (not ready or r["ready"])
    ]
    return {"items": rows[offset:offset + limit], "total": len(rows), "limit": limit, "offset": offset, "capped": capped}


async def _scan(db, viewer: dict, q: str | None, company: str | None) -> tuple[list[dict[str, Any]], bool]:
    """Everyone matching the text filters, enriched, newest first; capped at SCAN_LIMIT."""
    cte, params, clause = _people_sql(viewer, q, company)
    page = await (await db.execute(
        f"{cte} SELECT * FROM directory{clause}{_ORDER} LIMIT ?", [*params, SCAN_LIMIT + 1]
    )).fetchall()
    rows = [dict(r) for r in page[:SCAN_LIMIT]]
    await _enrich(db, rows, viewer["id"])
    return rows, len(page) > SCAN_LIMIT


async def summary(db, viewer: dict) -> dict[str, Any]:
    """Counts per proof and reach over everyone the viewer may see, plus the
    verification backlog. ``capped`` says the counts cover only the newest SCAN_LIMIT people."""
    rows, capped = await _scan(db, viewer, None, None)
    proofs = dict.fromkeys(PROOFS, 0)
    reaches = dict.fromkeys(REACH_STATES, 0)
    for r in rows:
        proofs[r["proof"]] += 1
        reaches[r["reach"]["state"]] += 1
    return {
        "total": len(rows),
        "capped": capped,
        "ready": sum(1 for r in rows if r["ready"]),
        "proof": proofs,
        "reach": reaches,
        "backlog": await backlog_status(db),
    }
