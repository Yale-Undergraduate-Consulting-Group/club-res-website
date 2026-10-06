"""Week slate releases — companies picked from the public company register."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth_deps import get_current_user
from app.database import get_db, row_to_dict

router = APIRouter()


async def require_release_owner(db, release_id: int, user_id: int):
    row = await (await db.execute('SELECT created_by FROM outreach_releases WHERE id=?',(release_id,))).fetchone()
    if not row:
        raise HTTPException(404,'Release not found')
    if row['created_by'] != user_id:
        raise HTTPException(403,'Only the release owner can change its contents')



class ReleaseCreate(BaseModel):
    name: str
    register_ids: list[int] = Field(default_factory=list)
    notes: str | None = None


@router.get("")
async def list_releases(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT id, name, status, created_by, created_at, updated_at FROM outreach_releases ORDER BY id DESC LIMIT 50",
        )
        return [row_to_dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


@router.get("/{release_id}")
async def get_release(release_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await require_release_owner(db, release_id, user["id"])
        cur = await db.execute("SELECT * FROM outreach_releases WHERE id = ?", (release_id,))
        rel = row_to_dict(await cur.fetchone())
        if not rel:
            raise HTTPException(404, "Release not found")
        cur = await db.execute(
            "SELECT * FROM outreach_release_targets WHERE release_id = ? ORDER BY id",
            (release_id,),
        )
        rel["targets"] = [row_to_dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM outreach_release_people WHERE release_id = ? ORDER BY id",
            (release_id,),
        )
        rel["people"] = [row_to_dict(r) for r in await cur.fetchall()]
        return rel
    finally:
        await db.close()


async def _register_targets(register_ids: list[int]) -> list[dict]:
    """Shape register companies like spreadsheet rows so one release can hold
    both. row_index stays null for these: they have no spreadsheet line, and
    inventing one would collide with a real row."""
    ids = [int(i) for i in register_ids][:500]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    db = await get_db()
    try:
        rows = await (await db.execute(
            f"""SELECT company_name, company_domain, sector_label, source, tier, employees
                FROM company_register WHERE id IN ({placeholders})""",
            ids,
        )).fetchall()
    finally:
        await db.close()
    return [{
        "row_index": None,
        "company": row["company_name"],
        "company_domain": row["company_domain"],
        "sector": row["sector_label"],
        # The register knows what kind of organisation this is, which is the
        # closest thing it has to the spreadsheet's contact type.
        "contact_type": row["tier"],
        "incentive_score": None,
        "verification_source_url": None,
        "_register_source": row["source"],
    } for row in rows]


@router.post("")
async def create_release(body: ReleaseCreate, user: dict = Depends(get_current_user)):
    picked: list[dict] = []
    if body.register_ids:
        picked.extend(await _register_targets(body.register_ids))
    if not picked:
        raise HTTPException(400, "Select at least one company")

    db = await get_db()
    try:
        cur = await db.execute(
            "INSERT INTO outreach_releases (name, status, created_by, notes) VALUES (?, 'draft', ?, ?)",
            (body.name, user["id"], body.notes),
        )
        release_id = cur.lastrowid
        for row in picked:
            await db.execute(
                """INSERT INTO outreach_release_targets
                   (release_id, row_index, company, company_domain, sector, contact_type,
                    incentive_score, verification_source_url, find_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    release_id,
                    row.get("row_index"),
                    row.get("company"),
                    # A register company already carries its domain; only a
                    # spreadsheet row has to have one guessed from its source
                    # URL, and guessing over a known value loses it.
                    row.get("company_domain") or _domain_guess(row),
                    row.get("sector"),
                    row.get("contact_type"),
                    row.get("incentive_score"),
                    row.get("verification_source_url"),
                ),
            )
        await db.commit()
        try:
            from app.services.thinkcell_pack import rebuild_release_pack, write_week_slate

            write_week_slate(release_id, body.name, picked)
            await rebuild_release_pack(db, release_id)
        except Exception:
            pass
        from app.services.week_slack import post_week_event

        post_week_event(f"Week slate created: {body.name} ({len(picked)} companies).")
        return {"id": release_id, "status": "draft", "targets": len(picked)}
    finally:
        await db.close()


class MintBody(BaseModel):
    full_name: str
    title: str | None = None
    source_url: str | None = None
    blurb: str | None = None


@router.post("/{release_id}/targets/{target_id}/mint")
async def mint_person(
    release_id: int,
    target_id: int,
    body: MintBody,
    user: dict = Depends(get_current_user),
):
    """1–2 pattern emails from a name + target domain. Status inferred, never verified."""
    from app.services.company_email_cache import build_email_for_person
    from app.services.email_verifier import verify_mx

    db = await get_db()
    try:
        await require_release_owner(db, release_id, user["id"])
        cur = await db.execute(
            "SELECT * FROM outreach_release_targets WHERE id = ? AND release_id = ?",
            (target_id, release_id),
        )
        target = row_to_dict(await cur.fetchone())
        if not target:
            raise HTTPException(404, "Target not found")
        domain = target.get("company_domain")
        email = await build_email_for_person(body.full_name, domain or "") if domain else None
        mx_ok = False
        host = (email or "").rsplit("@", 1)[-1] if email and "@" in email else domain
        if host:
            mx_ok, _ = await verify_mx(host)
        cur = await db.execute(
            """INSERT INTO outreach_release_people
               (release_id, target_id, full_name, title, email, company_domain,
                email_status, source_url, blurb, kept)
               VALUES (?, ?, ?, ?, ?, ?, 'inferred', ?, ?, 0)""",
            (
                release_id,
                target_id,
                body.full_name,
                body.title,
                email,
                domain,
                body.source_url,
                body.blurb,
            ),
        )
        await db.commit()
        return {
            "id": cur.lastrowid,
            "email": email,
            "email_status": "inferred",
            "mx_ok": mx_ok,
        }
    finally:
        await db.close()


class KeepBody(BaseModel):
    keep: bool = True


@router.post("/{release_id}/people/{person_id}/keep")
async def keep_or_drop_person(
    release_id: int,
    person_id: int,
    body: KeepBody,
    user: dict = Depends(get_current_user),
):
    db = await get_db()
    try:
        await require_release_owner(db, release_id, user["id"])
        cur = await db.execute(
            "SELECT * FROM outreach_release_people WHERE id = ? AND release_id = ?",
            (person_id, release_id),
        )
        person = row_to_dict(await cur.fetchone())
        if not person:
            raise HTTPException(404, "Person not found")
        from app.services.contact_intelligence import assess_address, ingest_contact
        vendor_check = person.get("vendor_check")
        vendor = person.get("vendor")
        evidence = None
        if body.keep and person.get("email"):
            # No writer transaction may surround verifier reservations or network I/O.
            assessment = await assess_address(person["email"], actor_id=user["id"])
            vendor_check, vendor = assessment["mailbox"], assessment["method"]
            await db.execute("BEGIN IMMEDIATE")
            await require_release_owner(db, release_id, user["id"])
            current = await (await db.execute(
                "SELECT * FROM outreach_release_people WHERE id=? AND release_id=?",
                (person_id, release_id),
            )).fetchone()
            if not current or dict(current) != person:
                raise HTTPException(409, "Person changed during review. Reload before keeping.")
            contact_id = await _upsert_kept_contact(db, person)
            target = await (await db.execute(
                "SELECT company FROM outreach_release_targets WHERE id=?", (person["target_id"],),
            )).fetchone()
            evidence = await ingest_contact(
                db, actor_id=user["id"], origin="imported_without_evidence",
                contact={"id": contact_id, "name": person.get("full_name"),
                         "email": person["email"], "title": person.get("title"),
                         "company": target["company"] if target else None,
                         "domain": person.get("company_domain"), **assessment},
                sources=[{"url": person["source_url"], "excerpt": person["blurb"]}]
                if person.get("source_url") and person.get("blurb") else [],
            )
            await db.execute(
                "UPDATE outreach_release_people SET contact_id = ? WHERE id = ?",
                (contact_id, person_id),
            )
        await db.execute(
            """UPDATE outreach_release_people
               SET kept = ?, vendor = COALESCE(?, vendor), vendor_check = COALESCE(?, vendor_check)
               WHERE id = ?""",
            (1 if body.keep else -1, vendor, vendor_check, person_id),
        )
        await db.commit()
        try:
            from app.services.thinkcell_pack import rebuild_release_pack

            await rebuild_release_pack(db, release_id)
        except Exception:
            pass
        return {
            "id": person_id,
            "kept": body.keep,
            "vendor": vendor,
            "vendor_check": vendor_check,
            "contact_id": contact_id if body.keep and person.get("email") else None,
            "evidence": evidence,
        }
    finally:
        await db.close()


async def _upsert_kept_contact(db, person: dict) -> int | None:
    email = (person.get("email") or "").strip().lower()
    if not email:
        return None
    cur = await db.execute("SELECT id FROM contacts WHERE email = ?", (email,))
    row = row_to_dict(await cur.fetchone())
    if row:
        return row["id"]
    cur = await db.execute(
        """INSERT INTO contacts (name, email, title, company, company_domain, contact_source, email_verification_status)
           VALUES (?, ?, ?, ?, ?, 'release_keep', ?)""",
        (
            person.get("full_name"),
            email,
            person.get("title"),
            None,
            person.get("company_domain"),
            person.get("email_status") or "inferred",
        ),
    )
    return cur.lastrowid




@router.post("/{release_id}/pack")
async def rebuild_pack(release_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await require_release_owner(db, release_id, user["id"])
        cur = await db.execute("SELECT id FROM outreach_releases WHERE id = ?", (release_id,))
        if not row_to_dict(await cur.fetchone()):
            raise HTTPException(404, "Release not found")
        from app.services.thinkcell_pack import rebuild_release_pack

        return await rebuild_release_pack(db, release_id)
    finally:
        await db.close()


@router.get("/{release_id}/inbox")
async def release_inbox(release_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await require_release_owner(db, release_id, user["id"])
        cur = await db.execute(
            """SELECT cc.id, cc.contact_id, cc.status, cc.sent_at, cc.replied_at, cc.opened_at,
                      c.email, c.name, c.company, c.email_verification_status
               FROM campaign_contacts cc
               JOIN contacts c ON c.id = cc.contact_id
               JOIN campaigns camp ON camp.id = cc.campaign_id
               WHERE c.id IN (
                 SELECT contact_id FROM outreach_release_people
                 WHERE release_id = ? AND kept = 1 AND contact_id IS NOT NULL
               ) AND camp.owner_user_id = ?
               ORDER BY cc.id DESC""",
            (release_id, user["id"]),
        )
        return [row_to_dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


def _domain_guess(row: dict) -> str | None:
    url = (row.get("verification_source_url") or "").strip().lower()
    if "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0]
        return host[4:] if host.startswith("www.") else host
    return None
