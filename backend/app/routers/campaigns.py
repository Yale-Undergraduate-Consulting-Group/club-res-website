"""
Campaigns API - Campaign Manager & Mass Sender
"""
import asyncio
import json
import os

from fastapi import APIRouter, HTTPException, Depends
from app.database import get_db
from app.auth_deps import get_current_user, get_current_user_optional
from app.services.audit_service import log_audit
from app.services.usage_service import log_event
from datetime import datetime
from pydantic import BaseModel
from app.models import CampaignCreate, CampaignContactAdd


class CampaignUpdate(BaseModel):
    sequence_id: int | None = None
    attachment_ids: list[int] | None = None


class OwnershipConfirmation(BaseModel):
    sender_user_id: int
    confirmed: bool = False
    reason: str


class DispatchRecoveryRequest(BaseModel):
    dispatch_key: str


class CampaignContactEmailUpdate(BaseModel):
    """Subject/body arrive as a JSON body. They used to be query parameters,
    which put whole HTML emails in the URL -- that exceeds CloudFront's 8 KB
    request-line limit in production and logs message content in access logs."""

    subject: str | None = None
    body: str | None = None


router = APIRouter()


@router.get("/{campaign_id}/dispatches")
async def list_dispatches(campaign_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await require_campaign_owner(db,campaign_id,user['id'])
        rows = await (await db.execute('''SELECT d.dispatch_key,d.campaign_contact_id,d.sender_user_id,
            d.recipient,d.state,d.claimed_at,d.completed_at,
            CASE WHEN d.state IN ('claimed','ambiguous') THEN 'Delivery requires reconciliation' ELSE NULL END AS last_error
            FROM outreach_dispatches d
            JOIN campaign_contacts cc ON cc.id=d.campaign_contact_id WHERE cc.campaign_id=?
            ORDER BY d.campaign_contact_id,d.dispatch_key''',(campaign_id,))).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()


@router.post("/{campaign_id}/dispatches/reconcile")
async def reconcile_dispatch(campaign_id: int, payload: DispatchRecoveryRequest, user: dict = Depends(get_current_user)):
    from app.services.dispatch_recovery import recover_dispatch
    return await recover_dispatch(campaign_id,payload.dispatch_key,user['id'])


@router.get("/{campaign_id}/ownership-evidence")
async def ownership_evidence(campaign_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        admin = await (await db.execute("SELECT role FROM users WHERE id=? AND is_active=1", (user["id"],))).fetchone()
        if not admin or admin["role"] != "admin":
            raise HTTPException(403, "Administrator required")
        campaign = await (await db.execute("SELECT id,owner_user_id,sender_user_id FROM campaigns WHERE id=?", (campaign_id,))).fetchone()
        if not campaign:
            raise HTTPException(404, "Campaign not found")
        rows = await (await db.execute(
            """SELECT DISTINCT sender_id FROM outreach_messages WHERE sent_at IS NOT NULL
            AND campaign_contact_id IN (SELECT id FROM campaign_contacts WHERE campaign_id=?)
            UNION SELECT DISTINCT sent_by_user_id FROM campaign_contacts WHERE campaign_id=? AND sent_by_user_id IS NOT NULL""",
            (campaign_id, campaign_id),
        )).fetchall()
        return {"campaign": dict(campaign), "historical_sender_ids": [r[0] for r in rows], "requires_explicit_confirmation": True}
    finally:
        await db.close()


@router.post("/{campaign_id}/reconcile-owner")
async def reconcile_owner(campaign_id: int, payload: OwnershipConfirmation, user: dict = Depends(get_current_user)):
    """Assign only unowned legacy campaigns, never rewrite historical message facts."""
    if not payload.confirmed or len(payload.reason.strip()) < 10:
        raise HTTPException(422, "Explicit confirmation and a meaningful evidence reason are required")
    from app.services.dispatch_service import begin_write
    db = await get_db()
    try:
        await begin_write(db)
        admin = await (await db.execute("SELECT role FROM users WHERE id=? AND is_active=1", (user["id"],))).fetchone()
        if not admin or admin["role"] != "admin":
            raise HTTPException(403, "Administrator required")
        campaign = await (await db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,))).fetchone()
        if not campaign:
            raise HTTPException(404, "Campaign not found")
        if campaign["owner_user_id"] or campaign["sender_user_id"]:
            raise HTTPException(409, "Existing ownership cannot be reassigned through reconciliation")
        active = await (await db.execute("SELECT id FROM users WHERE id=? AND is_active=1", (payload.sender_user_id,))).fetchone()
        if not active:
            raise HTTPException(409, "Selected sender is inactive or missing")
        conflict = await (await db.execute(
            """SELECT 1 FROM campaign_contacts cc WHERE cc.campaign_id=? AND
            (cc.status='sending' OR (cc.sent_by_user_id IS NOT NULL AND cc.sent_by_user_id<>?) OR EXISTS
            (SELECT 1 FROM outreach_messages m WHERE m.campaign_contact_id=cc.id AND m.sender_id<>? AND m.sent_at IS NOT NULL)) LIMIT 1""",
            (campaign_id, payload.sender_user_id, payload.sender_user_id),
        )).fetchone()
        if conflict:
            raise HTTPException(409, "Conflicting sender evidence or in-flight work requires manual review")
        await db.execute("UPDATE campaigns SET owner_user_id=?,sender_user_id=?,status=CASE WHEN status='releasing' THEN 'paused' ELSE status END WHERE id=?", (payload.sender_user_id,payload.sender_user_id,campaign_id))
        # Audit in the same transaction; failure must prevent reassignment.
        await db.execute("INSERT INTO audit_log(user_id,action,resource_type,resource_id,details) VALUES (?,?,?,?,?)", (user["id"],"campaign_owner_reconcile","campaign",str(campaign_id),payload.reason.strip()))
        await db.commit()
        return {"ok": True, "sender_user_id": payload.sender_user_id}
    finally:
        await db.close()

async def require_campaign_owner(db, campaign_id: int, user_id: int):
    row = await (await db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))).fetchone()
    if not row:
        raise HTTPException(404, "Campaign not found")
    if row["owner_user_id"] != user_id or row["sender_user_id"] != user_id:
        raise HTTPException(403, "Campaign sender ownership is required; legacy campaigns need reconciliation")
    active = await (await db.execute("SELECT is_active FROM users WHERE id = ?", (user_id,))).fetchone()
    if not active or not active["is_active"]:
        raise HTTPException(403, "Sender account is inactive")
    return row


async def _campaign_readiness(db, campaign_id: int) -> dict:
    campaign = await (await db.execute(
        "SELECT id, status FROM campaigns WHERE id = ?", (campaign_id,)
    )).fetchone()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    counts = {
        row["status"]: int(row["count"])
        for row in await (await db.execute(
            """SELECT status, COUNT(*) AS count FROM campaign_contacts
               WHERE campaign_id = ? GROUP BY status""",
            (campaign_id,),
        )).fetchall()
    }
    total = sum(counts.values())
    incomplete = int((await (await db.execute(
        """SELECT COUNT(*) AS count
           FROM campaign_contacts cc JOIN contacts c ON c.id = cc.contact_id
           LEFT JOIN outreach_dispatches d ON d.dispatch_key = 'initial:' || cc.id
           WHERE cc.campaign_id = ? AND cc.status IN ('pending', 'sending', 'failed')
             AND (trim(COALESCE(d.recipient, c.email, '')) = ''
                  OR instr(COALESCE(d.recipient,c.email), '@') = 0
                  OR trim(COALESCE(d.subject,cc.email_subject, '')) = ''
                  OR trim(COALESCE(d.body,cc.email_body, '')) = '')""",
        (campaign_id,),
    )).fetchone())["count"])
    from app.services.mail_address import validate_header, validate_recipient
    recipient_rows = await (await db.execute(
        """SELECT COALESCE(d.recipient,c.email) AS email,
        COALESCE(d.subject,cc.email_subject,'') AS subject,
        COALESCE(d.body,cc.email_body,'') AS body FROM campaign_contacts cc
        JOIN contacts c ON c.id=cc.contact_id
        LEFT JOIN outreach_dispatches d ON d.dispatch_key='initial:' || cc.id
        WHERE cc.campaign_id=? AND cc.status IN ('pending','sending','failed')""",(campaign_id,),
    )).fetchall()
    invalid_recipients = 0
    invalid_content = 0
    for row in recipient_rows:
        try:
            validate_recipient(row['email'])
        except ValueError:
            invalid_recipients += 1
        try:
            validate_header(row["subject"])
            if len(row["subject"]) > 200 or len(row["body"]) > 50000:
                raise ValueError("Message is too large")
        except ValueError:
            invalid_content += 1
    issues = []
    duplicate_rows = await (await db.execute(
        """SELECT COUNT(*) AS n FROM (
               SELECT contact_id FROM campaign_contacts WHERE campaign_id=?
               GROUP BY contact_id HAVING COUNT(*)>1
           ) duplicates""",
        (campaign_id,),
    )).fetchone()
    duplicate_contacts = int(duplicate_rows["n"] or 0)
    if duplicate_contacts:
        issues.append(
            f"Remove duplicate rows for {duplicate_contacts} recipient(s) before release; legacy duplicates are never sent automatically."
        )
    if invalid_recipients:
        issues.append(f"Use one valid bare email address for each of {invalid_recipients} recipient(s).")
    if invalid_content:
        issues.append(f"Fix unsafe or oversized content for {invalid_content} recipient(s).")
    if total == 0:
        issues.append("Add at least one recipient.")
    if incomplete:
        issues.append(f"Complete the email address, subject, and body for {incomplete} recipient(s).")
    if counts.get("failed", 0):
        issues.append(f"Retry or remove {counts['failed']} failed recipient(s).")
    # Not an issue to fix: a statement of what release will do. Companies with
    # no proven address are mailed one at a time first, so a wrong format
    # costs one bounce instead of the whole company.
    from app.services.send_gating import grace_minutes, mail_host, proven_hosts

    hosts = {mail_host(row["email"]) for row in recipient_rows}
    hosts.discard("")
    unproven = sorted(hosts - await proven_hosts(db, hosts))
    return {
        "ready": not issues,
        "issues": issues,
        "counts": counts,
        "total": total,
        "status": campaign["status"],
        "unproven_companies": unproven,
        "mailbox_proof_note": (
            f"No address has been proven at {', '.join(unproven)}. The first email to each goes alone; "
            f"the rest follow about {grace_minutes()} minutes later unless it bounces."
            if unproven else ""
        ),
    }




@router.get("")
async def list_campaigns(user: dict = Depends(get_current_user)):
    """List campaigns visible to this member; admins can audit the whole club."""
    db = await get_db()
    try:
        where = "" if user.get("role") == "admin" else "WHERE c.owner_user_id = ?"
        params = () if user.get("role") == "admin" else (user["id"],)
        cursor = await db.execute(
            """SELECT c.*,
               (SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = c.id) AS contact_count,
               (SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = c.id AND sent_at IS NOT NULL) AS sent_count,
               (SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = c.id AND status = 'pending') AS pending_count,
               (SELECT COUNT(*) FROM outreach_dispatches d JOIN campaign_contacts cc ON cc.id=d.campaign_contact_id
                  WHERE cc.campaign_id=c.id AND d.dispatch_key='initial:' || cc.id AND d.state='ready') AS queued_count,
               (SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = c.id AND status = 'sending') AS sending_count,
               (SELECT COUNT(*) FROM campaign_contacts WHERE campaign_id = c.id AND status = 'failed') AS failed_count
               FROM campaigns c """ + where + " ORDER BY c.created_at DESC",
            params,
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.post("")
async def create_campaign(campaign: CampaignCreate, user: dict = Depends(get_current_user)):
    """Create a new campaign."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO campaigns (name, status, owner_user_id, sender_user_id) VALUES (?, 'draft', ?, ?)",
            (campaign.name, user["id"], user["id"]),
        )
        await db.commit()
        row_id = cursor.lastrowid
        if user:
            await log_event(user["id"], "campaign_created", "campaign", {"campaign_id": row_id, "name": campaign.name})
        cursor = await db.execute("SELECT * FROM campaigns WHERE id = ?", (row_id,))
        row = await cursor.fetchone()
        return dict(row)
    finally:
        await db.close()


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: int, user: dict = Depends(get_current_user)):
    """Get campaign with contacts."""
    db = await get_db()
    try:
        await require_campaign_owner(db, campaign_id, user["id"])
        cursor = await db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        campaign = await cursor.fetchone()
        if not campaign:
            raise HTTPException(404, "Campaign not found")
        cursor = await db.execute(
            """SELECT cc.*, c.name, COALESCE(d.recipient,c.email) AS email, c.title, c.company
               FROM campaign_contacts cc 
               JOIN contacts c ON cc.contact_id = c.id
               LEFT JOIN outreach_dispatches d ON d.dispatch_key = 'initial:' || cc.id
               WHERE cc.campaign_id = ?""",
            (campaign_id,),
        )
        contacts = await cursor.fetchall()
        messages = await (await db.execute(
            """SELECT m.id, m.campaign_contact_id, m.sent_at FROM outreach_messages m
               JOIN campaign_contacts cc ON cc.id = m.campaign_contact_id
               WHERE cc.campaign_id = ? ORDER BY m.id""", (campaign_id,),
        )).fetchall()
        events = await (await db.execute(
            """SELECT e.message_id, e.kind, e.occurred_at, e.detail FROM outreach_events e
               JOIN outreach_messages m ON m.id = e.message_id
               JOIN campaign_contacts cc ON cc.id = m.campaign_contact_id
               WHERE cc.campaign_id = ? ORDER BY e.occurred_at""", (campaign_id,),
        )).fetchall()
        readiness = await _campaign_readiness(db, campaign_id)
        return {
            **dict(campaign),
            "counts": readiness["counts"],
            "readiness": {"ready": readiness["ready"], "issues": readiness["issues"]},
            "contacts": [{**dict(r), "messages": [
                {**dict(m), "events": [dict(e) for e in events if e["message_id"] == m["id"]]}
                for m in messages if m["campaign_contact_id"] == r["id"]
            ]} for r in contacts],
        }
    finally:
        await db.close()


class BuildFromTemplate(BaseModel):
    """One written message, many recipients, across one or more companies."""

    name: str = ""
    companies: list[str] = []
    contact_ids: list[int] = []
    subject: str = ""
    body: str = ""
    # One written message per company, keyed by company name. The group
    # subject/body stays the fallback for companies without one.
    messages: dict[str, dict[str, str]] = {}
    preview_only: bool = False


# One campaign stays something a member can review before release. Beyond
# this the work belongs in several releases, paced by the daily send cap.
MAX_RECIPIENTS = 5_000
MAX_COMPANIES = 500

async def _pipeline_recipients(db, payload: BuildFromTemplate, user_id: int) -> list[dict]:
    """The people this message would go to.

    Chosen by company, because that is how the work is chosen: a member picks
    companies and everyone found there is a candidate. Explicit contact_ids
    narrow it when they have pruned the list by hand.
    """
    clauses = ["(owner_id IS NULL OR owner_id = ?)", "email IS NOT NULL", "TRIM(email) <> ''"]
    args: list[object] = [user_id]
    if payload.contact_ids:
        ids = [int(i) for i in payload.contact_ids]
        if len(ids) > MAX_RECIPIENTS:
            # Silently keeping the first 500 shipped a campaign that was not
            # the one the member selected. A batch this size is a company
            # selection, not a hand-pruned list: say so instead of truncating.
            raise HTTPException(
                422,
                f"{len(ids)} recipients selected by hand; at most {MAX_RECIPIENTS} per campaign. "
                "Select the companies instead, or split the release.",
            )
        clauses.append(f"id IN ({','.join('?' * len(ids))})")
        args.extend(ids)
    elif payload.companies:
        names = [c.strip().lower() for c in payload.companies if c.strip()]
        if not names:
            return []
        if len(names) > MAX_COMPANIES:
            raise HTTPException(
                422,
                f"{len(names)} companies chosen; at most {MAX_COMPANIES} per campaign.",
            )
        clauses.append(f"LOWER(TRIM(company)) IN ({','.join('?' * len(names))})")
        args.extend(names)
    else:
        return []
    rows = await (await db.execute(
        f"""SELECT id, name, email, title, company FROM contacts WHERE {' AND '.join(clauses)}
            ORDER BY company, name LIMIT ?""",
        [*args, MAX_RECIPIENTS + 1],
    )).fetchall()
    if len(rows) > MAX_RECIPIENTS:
        raise HTTPException(
            422,
            f"Those companies hold more than {MAX_RECIPIENTS} contacts. "
            "Narrow the selection so one campaign stays reviewable.",
        )
    return [dict(r) for r in rows]


class DraftGroupTemplate(BaseModel):
    """Ask for one message that will personalise itself per recipient."""

    companies: list[str] = []
    roles: str = ""
    goal: str
    proof: str = ""
    length: str = "short"
    # One message each, written from what the club already recorded about that
    # company, instead of one message for all of them.
    per_company: bool = False


#: Drafting is a model call and a quota unit per company, so a run stays
#: something a member waits through rather than abandons.
MAX_DRAFTED_COMPANIES = 12


async def _company_notes(db, companies: list[str]) -> dict[str, str]:
    """What the club already decided about each company, from the register."""
    names = [c.strip() for c in companies if c.strip()]
    if not names:
        return {}
    rows = await (await db.execute(
        f"""SELECT company_name, sector_label, metadata_json FROM company_register
            WHERE LOWER(TRIM(company_name)) IN ({','.join('?' * len(names))})""",
        [n.lower() for n in names],
    )).fetchall()
    notes: dict[str, str] = {}
    for row in rows:
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        parts = [row["sector_label"] or "", meta.get("why_attractive") or "",
                 meta.get("engagement_theme") or "", meta.get("yale_hook") or "",
                 meta.get("first_message_angle") or ""]
        text = ". ".join(p.strip() for p in parts if p and str(p).strip())
        if text:
            notes.setdefault(row["company_name"].strip().lower(), text)
    return notes


@router.post("/draft-template")
async def draft_group_template(payload: DraftGroupTemplate, user: dict = Depends(get_current_user)):
    """Write the campaign's message: one for the group, or one per company.

    Studio drafts to one person, which is right for a bespoke email and wrong
    for a campaign. Without this a member either writes the same message
    twenty times or sends twenty identical notes addressed to nobody.
    """
    from starlette.concurrency import run_in_threadpool

    from app.services.generation_policy import reserve_generation
    from app.services.ollama_email_service import generate_group_template

    async def write(companies: list[str], notes: str) -> tuple[str, str]:
        await reserve_generation(user["id"], None)
        try:
            return await run_in_threadpool(
                generate_group_template,
                companies=companies,
                goal=payload.goal,
                proof=payload.proof,
                roles=payload.roles,
                length=payload.length,
                company_notes=notes,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    if not payload.per_company:
        subject, body = await write(payload.companies, "")
        return {"subject": subject, "body": body}

    named = [c.strip() for c in payload.companies if c.strip()]
    if not named:
        raise HTTPException(422, "Name at least one company")
    if len(named) > MAX_DRAFTED_COMPANIES:
        raise HTTPException(
            422,
            f"{len(named)} companies chosen; a message each is written for at most "
            f"{MAX_DRAFTED_COMPANIES}. Write one message for the group, or split the campaign.",
        )
    db = await get_db()
    try:
        notes = await _company_notes(db, named)
    finally:
        await db.close()
    messages: dict[str, dict[str, str]] = {}
    for company in named:
        subject, body = await write([company], notes.get(company.lower(), ""))
        messages[company] = {"subject": subject, "body": body}
    return {"messages": messages, "grounded": sorted(c for c in named if notes.get(c.lower()))}


@router.post("/build")
async def build_campaign_from_template(payload: BuildFromTemplate, user: dict = Depends(get_current_user)):
    """Render one message per recipient and, unless previewing, make the campaign.

    Preview and build share this path deliberately: what the member reads
    before pressing the button is produced by the code that will write the
    drafts, so it cannot promise something different from what is created.
    """
    from app.services.merge_fields import render_for_each, unknown_fields

    subject = (payload.subject or "").strip()
    body = (payload.body or "").strip()
    per_company = {
        company.strip().lower(): (
            (message.get("subject") or "").strip(), (message.get("body") or "").strip())
        for company, message in (payload.messages or {}).items()
        if company.strip() and (message.get("subject") or "").strip() and (message.get("body") or "").strip()
    }
    if not per_company and (not subject or not body):
        raise HTTPException(422, "A subject and a message are required")
    for text_subject, text_body in [(subject, body), *per_company.values()]:
        bad = sorted(set(unknown_fields(text_subject) + unknown_fields(text_body)))
        if bad:
            raise HTTPException(422, f"Unknown field(s): {', '.join('{' + b + '}' for b in bad)}")

    db = await get_db()
    try:
        recipients = await _pipeline_recipients(db, payload, user["id"])
        # Each company's people get that company's message. A company with no
        # message of its own falls back to the one written for the group; with
        # neither, nothing is invented for them - they are held and named.
        ready: list[dict] = []
        held: list[dict] = []
        groups: dict[str, list[dict]] = {}
        for person in recipients:
            groups.setdefault((person.get("company") or "").strip().lower(), []).append(person)
        for company_key, people in groups.items():
            text = per_company.get(company_key) or ((subject, body) if subject and body else None)
            if not text:
                held.extend({
                    "contact_id": p["id"], "email": p.get("email"), "name": p.get("name"),
                    "reason": f"No message written for {p.get('company') or 'their company'} yet.",
                } for p in people)
                continue
            rendered, withheld = render_for_each(text[0], text[1], people)
            ready.extend(rendered)
            held.extend(withheld)
        if payload.preview_only:
            return {
                "recipients": len(recipients),
                "ready": len(ready),
                "held": held,
                # A real rendered message, not the template, so the member
                # reads what the first recipient will read.
                "sample": ready[0] if ready else None,
                "messages_used": len(per_company) or (1 if subject and body else 0),
            }
        if not ready:
            raise HTTPException(422, "No recipient can receive this message yet")

        from app.services.dispatch_service import begin_write
        await begin_write(db)
        label = (payload.name or "").strip() or f"Outreach {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
        cur = await db.execute(
            "INSERT INTO campaigns (name, status, owner_user_id, sender_user_id) VALUES (?, 'draft', ?, ?)",
            (label, user["id"], user["id"]),
        )
        campaign_id = int(cur.lastrowid)
        await db.executemany(
            """INSERT INTO campaign_contacts (campaign_id, contact_id, email_subject, email_body, status)
               VALUES (?, ?, ?, ?, 'pending')""",
            [(campaign_id, item["contact_id"], item["subject"], item["body"]) for item in ready],
        )
        await db.commit()
        return {"campaign_id": campaign_id, "created": len(ready), "held": held, "name": label}
    finally:
        await db.close()


@router.post("/{campaign_id}/contacts")
async def add_contacts_to_campaign(
    campaign_id: int,
    payload: CampaignContactAdd,
    user: dict = Depends(get_current_user),
):
    """Add contacts to a mail campaign. Latest unattached Studio draft fills empty subject/body."""
    db = await get_db()
    try:
        from app.services.dispatch_service import begin_write
        await begin_write(db)
        campaign_state = await require_campaign_owner(db, campaign_id, user["id"])
        frozen = await (await db.execute(
            """SELECT 1 FROM outreach_dispatches d JOIN campaign_contacts cc ON cc.id=d.campaign_contact_id
            WHERE cc.campaign_id=? LIMIT 1""", (campaign_id,),
        )).fetchone()
        if frozen:
            raise HTTPException(409, "Released content is immutable; create a new campaign for changes")
        if campaign_state["status"] not in {"draft", "paused", "needs_attention"}:
            raise HTTPException(409, "Pause the campaign before changing its contents")
        in_flight = await (await db.execute("SELECT 1 FROM campaign_contacts WHERE campaign_id=? AND status='sending' LIMIT 1", (campaign_id,))).fetchone()
        if in_flight:
            raise HTTPException(409, "Wait for in-flight sends to finish")
        cursor = await db.execute("SELECT id FROM campaigns WHERE id = ?", (campaign_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "Campaign not found")

        subjects = payload.email_subjects or {}
        bodies = payload.email_bodies or {}
        attached = 0

        from app.services.contact_access import require_contact_access
        added = 0
        seen_contact_ids = set()
        for cid in payload.contact_ids:
            if cid in seen_contact_ids:
                continue
            seen_contact_ids.add(cid)
            await require_contact_access(db, cid, user)
            exists = await (await db.execute(
                "SELECT 1 FROM campaign_contacts WHERE campaign_id = ? AND contact_id = ? LIMIT 1",
                (campaign_id, cid),
            )).fetchone()
            if exists:
                continue
            subj = subjects.get(str(cid), "")
            body = bodies.get(str(cid), "")
            draft_id = None
            if user and (not subj or not body):
                cur = await db.execute(
                    """SELECT id, subject, body FROM generated_emails
                       WHERE contact_id = ? AND user_id = ? AND campaign_id IS NULL
                       ORDER BY id DESC LIMIT 1""",
                    (cid, user["id"]),
                )
                draft = await cur.fetchone()
                if draft:
                    draft_id = draft["id"]
                    subj = subj or (draft["subject"] or "")
                    body = body or (draft["body"] or "")
            await db.execute(
                """INSERT INTO campaign_contacts
                   (campaign_id, contact_id, email_subject, email_body, status)
                   VALUES (?, ?, ?, ?, 'pending')""",
                (campaign_id, cid, subj, body),
            )
            added += 1
            if draft_id:
                await db.execute(
                    "UPDATE generated_emails SET campaign_id = ? WHERE id = ?",
                    (campaign_id, draft_id),
                )
                attached += 1
        await db.commit()
        return {"ok": True, "added": added, "drafts_attached": attached}
    finally:
        await db.close()


async def _claim_pending_rows(db, campaign_id: int, limit: int, user_id=None) -> tuple[list, list[dict]]:
    """Atomically recheck sender and campaign state, then claim immutable intent.

    Returns the claimed rows and the rows withheld because the company has no
    proven mailbox yet, so the caller can report why a queue is not moving.
    """
    from app.services.dispatch_service import begin_write, claim
    from app.services.send_gating import select_sendable
    await begin_write(db)
    try:
        campaign = await (await db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,))).fetchone()
        sender = user_id if user_id is not None else campaign["sender_user_id"]
        await require_campaign_owner(db, campaign_id, sender)
        if campaign["status"] != "releasing":
            await db.commit()
            return []
        duplicate = await (await db.execute(
            """SELECT 1 FROM campaign_contacts WHERE campaign_id=?
               GROUP BY contact_id HAVING COUNT(*)>1 LIMIT 1""",
            (campaign_id,),
        )).fetchone()
        if duplicate:
            await db.execute(
                "UPDATE campaigns SET status='needs_attention', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (campaign_id,),
            )
            await db.commit()
            return [], []
        from app.services.settings_service import member_daily_send_limit
        daily_limit = await member_daily_send_limit(sender)
        reserved_today = await (await db.execute(
            """SELECT COUNT(*) AS n FROM outreach_dispatches
               WHERE sender_user_id=? AND claimed_at IS NOT NULL
                 AND date(claimed_at)=date('now')""",
            (sender,),
        )).fetchone()
        remaining = max(0, daily_limit - int(reserved_today["n"] or 0))
        if remaining == 0:
            await db.commit()
            return [], []
        # Gate before claiming: a claim marks the row sending and snapshots an
        # immutable intent, so a held row must never reach that point. Scan
        # further than this tick can send, or one held company at the head of
        # the queue would stall every other company behind it.
        window = await (await db.execute(
            """SELECT cc.*, c.email AS recipient_email
               FROM campaign_contacts cc JOIN contacts c ON c.id = cc.contact_id
               WHERE cc.campaign_id=? AND cc.status='pending' ORDER BY cc.id LIMIT ?""",
            (campaign_id, max(min(limit, remaining) * 10, 50)),
        )).fetchall()
        window = [dict(row) for row in window]
        for row in window:
            row["email"] = row.get("recipient_email")
        sendable, held = await select_sendable(db, campaign_id, window, sender)
        rows = sendable[: min(limit, remaining)]
        claimed = []
        for row in rows:
            intent = await claim(db, f"initial:{row['id']}", sender)
            if not intent:
                # Legacy unsnapshotted queues must be explicitly released again.
                await db.execute("UPDATE campaign_contacts SET status='failed',last_error=? WHERE id=?",
                                 ("Missing or already claimed dispatch; reconciliation required", row["id"]))
                continue
            await db.execute("UPDATE campaign_contacts SET status='sending' WHERE id=?", (row["id"],))
            item = dict(row)
            item.update(email=intent["recipient"], email_subject=intent["subject"], email_body=intent["body"])
            claimed.append(item)
        await db.commit()
        return claimed, held
    except Exception:
        await db.rollback()
        raise


async def drain_campaign(campaign_id: int, user_id: int, limit: int = 5) -> dict:
    """Send up to `limit` pending rows. EventBridge/local ticks call this; the HTTP request must not send the whole list."""
    from app.services.gmail_api import send_via_gmail_api_with_tracking
    from app.services.settings_service import get_member_setting

    db = await get_db()
    try:
        await require_campaign_owner(db, campaign_id, user_id)
        campaign = await (await db.execute(
            "SELECT status, attachment_ids_json FROM campaigns WHERE id = ?", (campaign_id,)
        )).fetchone()
        if not campaign:
            raise HTTPException(404, "Campaign not found")
        if campaign["status"] != "releasing":
            raise HTTPException(409, "Campaign must be released before it can send")
        pending, held = await _claim_pending_rows(db, campaign_id, limit, user_id)
        # Loaded once for the whole release: the sign-off is a property of
        # the sending member, not of each recipient.
        from app.services.settings_service import load_sign_off
        sign_off = await load_sign_off(user_id)
        attachment_ids = json.loads(campaign["attachment_ids_json"] or "[]")
        attachments = []
        if attachment_ids:
            from app.routers.attachments import get_attachment_data_for_send
            attachments = await get_attachment_data_for_send(attachment_ids, user_id)
        send_delay = float(os.getenv("CAMPAIGN_SEND_DELAY_SEC", "2.0") or 0)
        sent = 0
        errors = []
        for row in pending:
            try:
                send_meta = await send_via_gmail_api_with_tracking(
                    user_id=user_id,
                    to_email=row["email"],
                    subject=row["email_subject"] or "Quick question",
                    body=row["email_body"] or "",
                    campaign_contact_id=row["id"],
                    dispatch_key=f"initial:{row['id']}",
                    sign_off=sign_off,
                    attachments=attachments or None,
                )
                tid = send_meta.get("thread_id")
                mid = send_meta.get("message_id")
                await db.execute(
                    """UPDATE campaign_contacts SET
                       status = CASE WHEN status = 'sending' THEN 'sent' ELSE status END,
                       sent_at = COALESCE(sent_at, CURRENT_TIMESTAMP),
                       last_sequence_sent_at = CURRENT_TIMESTAMP,
                       sent_by_user_id = ?,
                       gmail_thread_id = COALESCE(?, gmail_thread_id),
                       gmail_message_id = COALESCE(?, gmail_message_id),
                       last_error = NULL
                       WHERE id = ?""",
                    (user_id, tid, mid, row["id"]),
                )
                from app.services.dispatch_service import finish
                await finish(db, f"initial:{row['id']}", send_meta)
                sent += 1
                await db.commit()
                if send_delay > 0:
                    await asyncio.sleep(send_delay)
            except Exception as e:
                from app.services.dispatch_service import finish
                from app.services.gmail_api import DeliveryNotAttemptedError
                safe_to_retry = isinstance(e, DeliveryNotAttemptedError)
                if safe_to_retry:
                    await db.execute(
                        "DELETE FROM outreach_messages WHERE dispatch_key=? AND sent_at IS NULL",
                        (f"initial:{row['id']}",),
                    )
                await finish(
                    db,
                    f"initial:{row['id']}",
                    error=e,
                    safe_to_retry=safe_to_retry,
                )
                await db.execute(
                    """UPDATE campaign_contacts SET status = 'failed', last_error = ?
                       WHERE id = ? AND status = 'sending'""",
                    (str(e)[:1000], row["id"]),
                )
                errors.append({"contact_id": row["contact_id"], "error": str(e)})
                await db.commit()

        state_rows = await (await db.execute(
            """SELECT status, COUNT(*) AS count FROM campaign_contacts
               WHERE campaign_id = ? GROUP BY status""",
            (campaign_id,),
        )).fetchall()
        counts = {row["status"]: int(row["count"]) for row in state_rows}
        left = counts.get("pending", 0)
        sending = counts.get("sending", 0)
        failed = counts.get("failed", 0)
        from app.services.send_gating import HELD_PROBE_BOUNCED, describe_hold

        hold_note = describe_hold(held)
        # A queue waiting on a probe is still working and stays releasing. A
        # queue whose probe bounced is not: every address behind it was built
        # from a format that just failed, so it needs a member, not another
        # tick.
        stopped = any(item["reason"] == HELD_PROBE_BOUNCED for item in held)
        status = (
            "needs_attention" if stopped or (not left and not sending and failed)
            else "releasing" if left or sending
            else "sent"
        )
        await db.execute(
            "UPDATE campaigns SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'releasing'",
            (status, campaign_id),
        )
        await db.commit()
        return {
            "ok": True,
            "sent": sent,
            "errors": errors,
            "pending_left": left,
            "failed": failed,
            "status": status,
            "held": len(held),
            "hold_reason": hold_note,
        }
    finally:
        await db.close()


@router.post("/{campaign_id}/release")
async def release_campaign(campaign_id: int, user: dict = Depends(get_current_user)):
    """Validate and release a draft or paused campaign for scheduled draining."""
    from app.services.dispatch_service import begin_write, snapshot_many
    db = await get_db()
    try:
        await begin_write(db)
        await require_campaign_owner(db, campaign_id, user["id"])
        readiness = await _campaign_readiness(db, campaign_id)
        if readiness["status"] == "sent":
            raise HTTPException(409, "Campaign is already complete")
        if not readiness["ready"]:
            raise HTTPException(409, {"message": "Campaign is not ready", "issues": readiness["issues"]})
        rows = await (await db.execute(
            """SELECT cc.*,c.email FROM campaign_contacts cc JOIN contacts c ON c.id=cc.contact_id
            WHERE cc.campaign_id=? AND cc.status='pending'""", (campaign_id,),
        )).fetchall()
        fresh_keys = await snapshot_many(db, [
            (f"initial:{row['id']}", row["id"], user["id"], row["email"],
             row["email_subject"], row["email_body"], 0)
            for row in rows
        ])
        newly_snapshotted = {int(key.split(":")[1]) for key in fresh_keys}
        sequence = await (await db.execute("SELECT sequence_id FROM campaigns WHERE id=?", (campaign_id,))).fetchone()
        steps = await (await db.execute(
            "SELECT * FROM follow_up_steps WHERE sequence_id=? ORDER BY step_order,days_after",
            (sequence["sequence_id"],),
        )).fetchall() if sequence["sequence_id"] and newly_snapshotted else []
        from app.services.mail_address import validate_header
        try:
            for row in rows:
                if row['id'] in newly_snapshotted:
                    validate_header(row['email_subject'] or '')
            for step in steps:
                validate_header(step['subject'] or '')
        except ValueError as exc:
            raise HTTPException(409,'Email subjects cannot contain control characters') from exc
        await snapshot_many(db, [
            (f"followup:{row['id']}:{index}", row["id"], user["id"], row["email"],
             step["subject"] or "Following up", step["body"] or "", step["days_after"])
            for row in rows if row['id'] in newly_snapshotted
            for index, step in enumerate(steps)
        ])
        await db.execute(
            "UPDATE campaigns SET status = 'releasing', released_by = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user["id"], campaign_id),
        )
        await db.commit()
        # The drain claims at most the member's daily cap, so a release larger
        # than the cap is paced over days. Saying so here is the only chance the
        # member gets: the send loop runs unattended.
        from app.services.settings_service import (
            member_daily_send_limit,
            member_send_warn_threshold,
        )
        daily_limit = await member_daily_send_limit(user["id"])
        warn_at = await member_send_warn_threshold(user["id"])
        queued = int(readiness["counts"].get("pending", 0) or 0)
        allowance = {
            "daily_limit": daily_limit,
            "queued": queued,
            "days_to_drain": max(1, -(-queued // daily_limit)) if queued else 0,
            "warn_at": warn_at,
        }
        if warn_at is not None and queued > warn_at:
            allowance["warning"] = (
                f"This release queues {queued} first sends, above your warning "
                f"threshold of {warn_at}."
            )
        return {"ok": True, "status": "releasing", "counts": readiness["counts"], "allowance": allowance}
    finally:
        await db.close()

@router.post("/{campaign_id}/pause")
async def pause_campaign(campaign_id: int, user: dict = Depends(get_current_user)):
    """Pause future claims; in-flight dispatches retain their durable state."""
    db = await get_db()
    try:
        from app.services.dispatch_service import begin_write
        await begin_write(db)
        await require_campaign_owner(db, campaign_id, user["id"])
        campaign = await (await db.execute(
            "SELECT status FROM campaigns WHERE id = ?", (campaign_id,)
        )).fetchone()
        if not campaign:
            raise HTTPException(404, "Campaign not found")
        if campaign["status"] not in {"releasing", "needs_attention"}:
            raise HTTPException(409, "Only an active campaign can be paused")
        await db.execute(
            "UPDATE campaigns SET status = 'paused', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (campaign_id,),
        )
        await db.commit()
        await log_audit(user["id"], "campaign_pause", "campaign", str(campaign_id), "Paused campaign")
        return {"ok": True, "status": "paused"}
    finally:
        await db.close()


@router.post("/{campaign_id}/resume")
async def resume_campaign(campaign_id: int, user: dict = Depends(get_current_user)):
    """Revalidate and resume a paused campaign."""
    return await release_campaign(campaign_id, user)


@router.post("/{campaign_id}/retry-failed")
async def retry_failed_campaign_contacts(
    campaign_id: int,
    user: dict = Depends(get_current_user),
):
    """Move failed recipients back to pending without sending them in this request."""
    db = await get_db()
    try:
        from app.services.dispatch_service import begin_write
        await begin_write(db)
        await require_campaign_owner(db, campaign_id, user["id"])
        if not await (await db.execute(
            "SELECT id FROM campaigns WHERE id = ?", (campaign_id,)
        )).fetchone():
            raise HTTPException(404, "Campaign not found")
        cursor = await db.execute(
            """UPDATE campaign_contacts SET status = 'pending', last_error = NULL
               WHERE campaign_id = ? AND status = 'failed' AND NOT EXISTS (
                   SELECT 1 FROM outreach_dispatches d WHERE d.campaign_contact_id=campaign_contacts.id
                   AND d.state IN ('claimed','ambiguous','sent'))""",
            (campaign_id,),
        )
        retried = cursor.rowcount
        await db.execute(
            "UPDATE campaigns SET status = 'paused', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (campaign_id,),
        )
        await db.commit()
        await log_audit(user["id"], "campaign_retry_failed", "campaign", str(campaign_id), f"Retried {retried} recipients")
        return {"ok": True, "retried": retried, "status": "paused"}
    finally:
        await db.close()



@router.post("/{campaign_id}/send")
async def send_campaign(campaign_id: int, user: dict = Depends(get_current_user)):
    """One drain tick (default 5). Does not send the entire campaign in this request."""
    limit = int(os.getenv("CAMPAIGN_DRAIN_LIMIT", "5") or 5)
    result = await drain_campaign(campaign_id, user["id"], limit=limit)
    await log_audit(user["id"], "campaign_send", "campaign", str(campaign_id), f"Drained {result['sent']} emails")
    await log_event(user["id"], "campaign_sent", "campaign", {"campaign_id": campaign_id, **result})
    return result


async def drain_releasing_campaigns(limit: int | None = None) -> dict:
    """Scheduled tick: drain every campaign left in releasing."""
    n = limit if limit is not None else int(os.getenv("CAMPAIGN_DRAIN_LIMIT", "5") or 5)
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id, sender_user_id AS released_by FROM campaigns WHERE status = 'releasing'"
        )
        rows = await cur.fetchall()
    finally:
        await db.close()
    out = []
    for row in rows:
        uid = row["released_by"]
        if not uid:
            continue
        try:
            out.append(await drain_campaign(row["id"], int(uid), limit=n))
        except HTTPException as exc:
            if exc.status_code == 403:
                failed_db = await get_db()
                try:
                    await failed_db.execute(
                        "UPDATE campaigns SET status='needs_attention' WHERE id=? AND status='releasing'",
                        (row["id"],),
                    )
                    await failed_db.commit()
                finally:
                    await failed_db.close()
            out.append({"ok": False, "campaign_id": row["id"], "error": str(exc.detail)[:1000]})
        except Exception as exc:
            out.append({"ok": False, "campaign_id": row["id"], "error": str(exc)[:1000]})
    return {"ok": all(item.get("ok") for item in out), "campaigns": len(out), "results": out}


@router.delete("/{campaign_id}")
async def delete_campaign(campaign_id: int, user: dict = Depends(get_current_user)):
    """Delete a campaign and its campaign_contacts."""
    db = await get_db()
    try:
        from app.services.dispatch_service import begin_write
        await begin_write(db)
        campaign_state = await require_campaign_owner(db, campaign_id, user["id"])
        frozen = await (await db.execute(
            """SELECT 1 FROM outreach_dispatches d JOIN campaign_contacts cc ON cc.id=d.campaign_contact_id
            WHERE cc.campaign_id=? LIMIT 1""", (campaign_id,),
        )).fetchone()
        if frozen:
            raise HTTPException(409, "Released content is immutable; create a new campaign for changes")
        if campaign_state["status"] not in {"draft", "paused", "needs_attention"}:
            raise HTTPException(409, "Pause the campaign before changing its contents")
        in_flight = await (await db.execute("SELECT 1 FROM campaign_contacts WHERE campaign_id=? AND status='sending' LIMIT 1", (campaign_id,))).fetchone()
        if in_flight:
            raise HTTPException(409, "Wait for in-flight sends to finish")
        cursor = await db.execute("SELECT id, status FROM campaigns WHERE id = ?", (campaign_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Campaign not found")
        await db.execute(
            "DELETE FROM email_events WHERE campaign_contact_id IN (SELECT id FROM campaign_contacts WHERE campaign_id = ?)",
            (campaign_id,),
        )
        await db.execute("DELETE FROM campaign_contacts WHERE campaign_id = ?", (campaign_id,))
        await db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
        await db.commit()
        await log_audit(user["id"], "campaign_delete", "campaign", str(campaign_id), f"Deleted campaign {campaign_id}")
        return {"ok": True}
    finally:
        await db.close()


@router.patch("/{campaign_id}")
async def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    user: dict = Depends(get_current_user),
):
    """Update campaign (e.g. attach or clear a follow-up sequence)."""
    db = await get_db()
    try:
        from app.services.dispatch_service import begin_write
        await begin_write(db)
        campaign_state = await require_campaign_owner(db, campaign_id, user["id"])
        frozen = await (await db.execute(
            """SELECT 1 FROM outreach_dispatches d JOIN campaign_contacts cc ON cc.id=d.campaign_contact_id
            WHERE cc.campaign_id=? LIMIT 1""", (campaign_id,),
        )).fetchone()
        if frozen:
            raise HTTPException(409, "Released content is immutable; create a new campaign for changes")
        if campaign_state["status"] not in {"draft", "paused", "needs_attention"}:
            raise HTTPException(409, "Pause the campaign before changing its contents")
        in_flight = await (await db.execute("SELECT 1 FROM campaign_contacts WHERE campaign_id=? AND status='sending' LIMIT 1", (campaign_id,))).fetchone()
        if in_flight:
            raise HTTPException(409, "Wait for in-flight sends to finish")
        data = payload.model_dump(exclude_unset=True)
        assignments = []
        values = []
        if "sequence_id" in data:
            assignments.append("sequence_id = ?")
            values.append(data["sequence_id"])
        if "attachment_ids" in data:
            attachment_ids = list(dict.fromkeys(data["attachment_ids"] or []))
            if attachment_ids:
                placeholders = ",".join("?" * len(attachment_ids))
                visible = await (await db.execute(
                    f"""SELECT id FROM email_attachments WHERE id IN ({placeholders})
                        AND (owner_user_id IS NULL OR owner_user_id = ?)""",
                    [*attachment_ids, user["id"]],
                )).fetchall()
                if len(visible) != len(attachment_ids):
                    raise HTTPException(404, "One or more attachments are unavailable")
            assignments.append("attachment_ids_json = ?")
            values.append(json.dumps(attachment_ids))
        if assignments:
            await db.execute(
                f"UPDATE campaigns SET {', '.join(assignments)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [*values, campaign_id],
            )
            await db.commit()
        cursor = await db.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, "Campaign not found")
        return dict(row)
    finally:
        await db.close()


@router.patch("/{campaign_id}/contact/{cc_id}")
async def update_campaign_contact_email(
    campaign_id: int,
    cc_id: int,
    payload: CampaignContactEmailUpdate | None = None,
    subject: str | None = None,
    body: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Update email subject/body for a campaign contact.

    The JSON body is the supported contract. Bare query parameters remain
    accepted so an older client keeps working, but a full HTML body must not
    travel in the URL.
    """
    if payload is not None:
        subject = payload.subject if payload.subject is not None else subject
        body = payload.body if payload.body is not None else body
    db = await get_db()
    try:
        from app.services.dispatch_service import begin_write
        await begin_write(db)
        campaign_state = await require_campaign_owner(db, campaign_id, user["id"])
        frozen = await (await db.execute(
            """SELECT 1 FROM outreach_dispatches d JOIN campaign_contacts cc ON cc.id=d.campaign_contact_id
            WHERE cc.campaign_id=? LIMIT 1""", (campaign_id,),
        )).fetchone()
        if frozen:
            raise HTTPException(409, "Released content is immutable; create a new campaign for changes")
        if campaign_state["status"] not in {"draft", "paused", "needs_attention"}:
            raise HTTPException(409, "Pause the campaign before changing its contents")
        in_flight = await (await db.execute("SELECT 1 FROM campaign_contacts WHERE campaign_id=? AND status='sending' LIMIT 1", (campaign_id,))).fetchone()
        if in_flight:
            raise HTTPException(409, "Wait for in-flight sends to finish")
        updates = []
        params = []
        if subject is not None:
            updates.append("email_subject = ?")
            params.append(subject)
        if body is not None:
            updates.append("email_body = ?")
            params.append(body)
        if not updates:
            raise HTTPException(400, "Provide subject or body")
        params.extend([campaign_id, cc_id])
        await db.execute(
            f"UPDATE campaign_contacts SET {', '.join(updates)} WHERE campaign_id = ? AND id = ?",
            params,
        )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.delete("/{campaign_id}/contact/{cc_id}")
async def remove_campaign_contact(
    campaign_id: int, cc_id: int, user: dict = Depends(get_current_user),
):
    """Remove a recipient while a campaign is still editable."""
    db = await get_db()
    try:
        from app.services.dispatch_service import begin_write
        await begin_write(db)
        campaign_state = await require_campaign_owner(db, campaign_id, user["id"])
        if campaign_state["status"] not in {"draft", "paused", "needs_attention"}:
            raise HTTPException(409, "Pause the campaign before changing its recipients")
        frozen = await (await db.execute(
            "SELECT 1 FROM outreach_dispatches WHERE campaign_contact_id=? LIMIT 1", (cc_id,)
        )).fetchone()
        if frozen:
            raise HTTPException(409, "Released recipients are immutable; create a new campaign")
        row = await (await db.execute(
            "SELECT contact_id FROM campaign_contacts WHERE id=? AND campaign_id=?", (cc_id, campaign_id)
        )).fetchone()
        if not row:
            raise HTTPException(404, "Campaign recipient not found")
        await db.execute(
            "UPDATE generated_emails SET campaign_id=NULL WHERE campaign_id=? AND contact_id=? AND user_id=?",
            (campaign_id, row["contact_id"], user["id"]),
        )
        await db.execute("DELETE FROM campaign_contacts WHERE id=? AND campaign_id=?", (cc_id, campaign_id))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()
