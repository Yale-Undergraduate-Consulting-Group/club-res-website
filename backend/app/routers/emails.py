"""Member-owned email drafts and controlled test delivery."""
from fastapi import APIRouter, HTTPException, Depends
import hashlib
import json
from starlette.concurrency import run_in_threadpool
from app.services.generation_policy import reserve_generation
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.auth_deps import get_current_user, get_current_user_optional
from app.models import EmailGenerateRequest, EmailGenerateResponse, EmailGenerateTemplateRequest
from app.services.ollama_email_service import generate_email
from app.services.usage_service import log_event

router = APIRouter()


class TestSendRequest(BaseModel):
    to_email: str
    subject: str
    body: str
    attachment_ids: Optional[list[int]] = None

class DraftSaveRequest(BaseModel):
    contact_id: int
    subject: str = ""
    body: str = ""


class DraftUpdateRequest(BaseModel):
    subject: str
    body: str




async def _generate_or_release(reservation, fn, **kwargs):
    """Run a draft, returning its reservation when the model was never reached."""
    from app.services.generation_policy import release_generation
    try:
        return await run_in_threadpool(fn, **kwargs)
    except HTTPException as exc:
        # 429 busy slot and 503 provider unavailable happen before any tokens
        # are spent; a 502 is a draft the model did write and failed checks.
        if exc.status_code in (429, 503):
            await release_generation(reservation)
        raise


@router.post("/generate", response_model=EmailGenerateResponse)
async def generate_email_for_contact(req: EmailGenerateRequest, user: dict = Depends(get_current_user)):
    """Generate a grounded starting draft for a contact."""
    db = await get_db()
    try:
        from app.services.contact_access import require_contact_access
        row = await require_contact_access(db, req.contact_id, user)
        contact = dict(row)

        from app.services.contact_scraper import normalize_domain
        company_domain = normalize_domain(contact.get("company_domain") or "")
        from app.services.generation_policy import draft_evidence
        evidence = await draft_evidence(db, contact, user["id"])
        # The register's notes on the company steer which projects an
        # advisory draft proposes; the other angles do not use them.
        company_context = ""
        if req.angle == "advisory" and contact.get("company"):
            from app.routers.campaigns import _company_notes
            notes = await _company_notes(db, [contact["company"]])
            company_context = notes.get(contact["company"].strip().lower(), "")

        # A draft is a pure function of its brief. Regenerating an unchanged
        # brief bills a second Bedrock call to produce a near-identical email,
        # which is the most common wasted spend in the app: members press
        # Generate again while reading. Fingerprint the inputs and reuse.
        brief_hash = hashlib.sha256(json.dumps({
            "contact": [contact.get("name"), contact.get("title"), contact.get("company"), company_domain],
            "tone": req.tone, "length": req.length, "angle": req.angle,
            "instructions": req.custom_instructions, "value": req.value_proposition,
            "model": req.model, "company_context": company_context,
            "evidence": sorted(str(s.get("id")) for s in (evidence or {}).get("sources", [])),
        }, sort_keys=True, default=str).encode()).hexdigest()

        cached = await (await db.execute(
            """SELECT subject, body FROM generated_emails
               WHERE user_id=? AND contact_id=? AND brief_hash=?
               ORDER BY id DESC LIMIT 1""",
            (user["id"], req.contact_id, brief_hash),
        )).fetchone()
        if cached and cached["subject"] and cached["body"]:
            await log_event(
                user["id"], "email_generation_reused", "email",
                {"contact_id": req.contact_id},
            )
            return EmailGenerateResponse(
                subject=cached["subject"], body=cached["body"], contact_id=req.contact_id,
            )

        reservation = await reserve_generation(user['id'], req.model)
        subject, body = await _generate_or_release(
            reservation, generate_email,
            contact_name=contact.get("name"),
            contact_title=contact.get("title"),
            company_name=contact.get("company"),
            company_domain=company_domain,
            tone=req.tone,
            length=req.length.replace("-", "_"),
            angle=req.angle,
            custom_instructions=req.custom_instructions,
            value_proposition=req.value_proposition,
            model=req.model,
            evidence=evidence,
            company_context=company_context,
        )
        await db.execute(
            """INSERT INTO generated_emails (user_id, contact_id, subject, body, evidence_json, brief_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user["id"], req.contact_id, subject, body, json.dumps(evidence), brief_hash),
        )
        await db.commit()

        await log_event(
            user["id"], "email_generated", "email",
            {"contact_id": req.contact_id, "tone": req.tone, "length": req.length, "angle": req.angle},
        )
        return EmailGenerateResponse(
            subject=subject,
            body=body,
            contact_id=req.contact_id,
        )
    finally:
        await db.close()


async def _upsert_contact_by_email(
    db,
    *,
    email: str,
    name: str | None,
    title: str | None,
    company: str | None,
) -> int:
    from app.services.contact_scraper import normalize_domain, sanitize_email

    em = sanitize_email(email).strip().lower()
    if not em or "@" not in em:
        raise HTTPException(400, "Valid email required to save a catalog contact")
    domain = normalize_domain(company or "") if company else ""
    cur = await db.execute("SELECT id FROM contacts WHERE lower(email) = ?", (em,))
    row = await cur.fetchone()
    if row:
        cid = int(row["id"])
        await db.execute(
            """UPDATE contacts SET
                   name = COALESCE(NULLIF(?, ''), name),
                   title = COALESCE(NULLIF(?, ''), title),
                   company = COALESCE(NULLIF(?, ''), company),
                   company_domain = COALESCE(NULLIF(?, ''), company_domain)
               WHERE id = ?""",
            ((name or "").strip(), (title or "").strip(), (company or "").strip(), domain, cid),
        )
        return cid
    cur = await db.execute(
        """INSERT INTO contacts (name, email, title, company, company_domain, contact_source)
           VALUES (?, ?, ?, ?, ?, 'studio')""",
        (name, em, title, company, domain or None),
    )
    return int(cur.lastrowid)


@router.post("/generate-template", response_model=EmailGenerateResponse)
async def generate_email_template(
    req: EmailGenerateTemplateRequest,
    user: dict = Depends(get_current_user),
):
    """Generate a quick-compose draft, reusing the same unchanged brief."""
    from app.services.contact_scraper import normalize_domain, sanitize_email

    company_domain = normalize_domain(req.company or "") if req.company else ""
    em = sanitize_email(req.email or "").strip().lower()
    evidence = {
        "sources": [],
        "context_origin": "user_provided",
        "user_provided": {"name": req.name, "title": req.title, "company": req.company},
    }
    contact_id = None
    brief_hash = None
    if em:
        db = await get_db()
        try:
            from app.services.delivery_policy import require_recipient_allowed
            await require_recipient_allowed(db, em)
            existing = await (await db.execute(
                "SELECT * FROM contacts WHERE lower(email)=?", (em,)
            )).fetchone()
            if existing:
                from app.services.contact_access import require_contact_access
                await require_contact_access(db, existing["id"], user)
                contact_id = int(existing["id"])
                from app.services.generation_policy import draft_evidence
                evidence = {
                    **await draft_evidence(db, dict(existing), user["id"]),
                    "user_provided": evidence["user_provided"],
                }
            else:
                contact_id = await _upsert_contact_by_email(
                    db, email=em, name=req.name, title=req.title, company=req.company
                )
                from app.services.contact_intelligence import ingest_contact
                await ingest_contact(
                    db, actor_id=user["id"], origin="user_supplied",
                    contact={"id": contact_id, "email": em, "name": req.name,
                             "title": req.title, "company": req.company},
                )
                await db.commit()

            brief_hash = hashlib.sha256(json.dumps({
                "contact": [req.name, req.title, req.company, company_domain, em],
                "tone": req.tone, "length": req.length, "angle": req.angle,
                "instructions": req.custom_instructions, "value": req.value_proposition,
                "model": req.model,
                "evidence": sorted(str(s.get("id")) for s in evidence.get("sources", [])),
            }, sort_keys=True, default=str).encode()).hexdigest()
            cached = await (await db.execute(
                """SELECT subject, body FROM generated_emails
                   WHERE user_id=? AND contact_id=? AND brief_hash=?
                   ORDER BY id DESC LIMIT 1""",
                (user["id"], contact_id, brief_hash),
            )).fetchone()
            if cached and cached["subject"] and cached["body"]:
                await log_event(
                    user["id"], "email_generation_reused", "email",
                    {"contact_id": contact_id},
                )
                return EmailGenerateResponse(
                    subject=cached["subject"], body=cached["body"], contact_id=contact_id,
                )
        finally:
            await db.close()

    await reserve_generation(user["id"], req.model)
    subject, body = await run_in_threadpool(
        generate_email,
        contact_name=req.name,
        contact_title=req.title,
        company_name=req.company,
        company_domain=company_domain,
        tone=req.tone,
        length=req.length.replace("-", "_"),
        angle=req.angle,
        custom_instructions=req.custom_instructions,
        value_proposition=req.value_proposition,
        model=req.model,
        evidence=evidence,
    )
    if contact_id is not None:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO generated_emails
                   (user_id, contact_id, subject, body, evidence_json, brief_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user["id"], contact_id, subject, body, json.dumps(evidence), brief_hash),
            )
            await db.commit()
        finally:
            await db.close()
    return EmailGenerateResponse(subject=subject, body=body, contact_id=contact_id)


@router.post("/test-send")
async def test_send_email(req: TestSendRequest, user: dict = Depends(get_current_user)):
    """Send a one-off test of exactly what a recipient would receive."""
    from app.services.gmail_api import send_via_gmail_api_multipart
    try:
        attachments_data = []
        if req.attachment_ids:
            from app.routers.attachments import get_attachment_data_for_send
            attachments_data = await get_attachment_data_for_send(req.attachment_ids, user["id"])
        from app.services.settings_service import load_sign_off
        await send_via_gmail_api_multipart(
            user_id=user["id"],
            to_email=req.to_email,
            subject=req.subject,
            body=req.body,
            attachments=attachments_data if attachments_data else None,
            sign_off=await load_sign_off(user["id"]),
        )
        return {"ok": True, "message": f"Test email sent to {req.to_email}"}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        err = str(e)
        raise HTTPException(500, f"Failed to send: {err}")


@router.post("/generated")
async def save_generated_email_draft(
    req: DraftSaveRequest,
    user: dict = Depends(get_current_user),
):
    """Persist a member-owned Studio draft for an existing shared contact."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM contacts WHERE id = ?", (req.contact_id,))
        if not await cursor.fetchone():
            raise HTTPException(404, "Contact not found")
        cursor = await db.execute(
            """INSERT INTO generated_emails (user_id, contact_id, subject, body)
               VALUES (?, ?, ?, ?)""",
            (user["id"], req.contact_id, req.subject, req.body),
        )
        await db.commit()
        return {"id": int(cursor.lastrowid), "contact_id": req.contact_id, "subject": req.subject, "body": req.body}
    finally:
        await db.close()


@router.patch("/generated/{draft_id}")
async def update_generated_email_draft(
    draft_id: int,
    req: DraftUpdateRequest,
    user: dict = Depends(get_current_user),
):
    """Update a Studio draft only when it belongs to the current member."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """UPDATE generated_emails SET subject = ?, body = ?
               WHERE id = ? AND user_id = ?""",
            (req.subject, req.body, draft_id, user["id"]),
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Draft not found")
        return {"ok": True, "id": draft_id}
    finally:
        await db.close()


@router.delete("/generated/{draft_id}")
async def delete_generated_email_draft(
    draft_id: int,
    user: dict = Depends(get_current_user),
):
    """Delete a Studio draft only when it belongs to the current member."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM generated_emails WHERE id = ? AND user_id = ?",
            (draft_id, user["id"]),
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, "Draft not found")
        return {"ok": True, "id": draft_id}
    finally:
        await db.close()

@router.get("/generated/{draft_id}/evidence")
async def get_generated_email_evidence(draft_id: int, user: dict = Depends(get_current_user)):
    """Show the exact evidence a member-owned draft was grounded in."""
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, user_id, contact_id, evidence_json FROM generated_emails WHERE id = ?",
            (draft_id,),
        )).fetchone()
        if not row:
            raise HTTPException(404, "Draft not found")
        if row["user_id"] != user["id"]:
            raise HTTPException(403, "Drafts are member-owned")
        try:
            evidence = json.loads(row["evidence_json"] or "{}")
        except ValueError:
            evidence = {}
        return {
            "id": row["id"],
            "contact_id": row["contact_id"],
            "context_origin": evidence.get("context_origin") or "user_provided",
            "sources": evidence.get("sources", []),
            "checked_at": evidence.get("checked_at"),
        }
    finally:
        await db.close()


@router.get("/generated")
async def list_generated_emails(
    contact_id: Optional[int] = None,
    sort: str = "created_desc",
    user: dict | None = Depends(get_current_user_optional),
):
    """List generated emails for the current user. Returns [] if not authenticated."""
    if not user:
        return []
    order_map = {
        "created_desc": "ge.created_at DESC",
        "created_asc": "ge.created_at ASC",
        "contact": "c.name",
    }
    order = order_map.get(sort, "ge.created_at DESC")
    db = await get_db()
    try:
        if contact_id:
            cursor = await db.execute(
                f"""SELECT ge.*, c.name, c.email, c.company FROM generated_emails ge
                    JOIN contacts c ON ge.contact_id = c.id
                    WHERE ge.user_id = ? AND ge.contact_id = ? ORDER BY {order}""",
                (user["id"], contact_id),
            )
        else:
            cursor = await db.execute(
                f"""SELECT ge.*, c.name, c.email, c.company FROM generated_emails ge
                    JOIN contacts c ON ge.contact_id = c.id
                    WHERE ge.user_id = ? ORDER BY {order}""",
                (user["id"],),
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


@router.delete("/generated")
async def clear_generated_emails_cache(user: dict = Depends(get_current_user)):
    """Remove all AI-generated email records for the current user (studio cache only — does not delete contacts)."""
    db = await get_db()
    try:
        await db.execute("DELETE FROM generated_emails WHERE user_id = ?", (user["id"],))
        cur = await db.execute("SELECT changes() AS n")
        row = await cur.fetchone()
        n = int(row["n"]) if row and row["n"] is not None else 0
        await db.commit()
        return {"ok": True, "deleted": n}
    finally:
        await db.close()


@router.post("/generate-batch")
async def generate_emails_batch(requests: list[EmailGenerateRequest], user: dict = Depends(get_current_user)):
    """Generate emails for multiple contacts (batch)."""
    if len(requests) > 20:
        raise HTTPException(413, 'Generate at most 20 drafts per batch')
    results = []
    for req in requests:
        try:
            resp = await generate_email_for_contact(req, user)
            results.append(resp.model_dump())
        except HTTPException as exc:
            results.append({
                "contact_id": req.contact_id,
                "error": exc.detail,
                "subject": None,
                "body": None,
            })
        except Exception:
            results.append({
                "contact_id": req.contact_id,
                "error": "Draft generation failed. Your existing draft is unchanged; please retry.",
                "subject": None,
                "body": None,
            })
    return {"results": results}
