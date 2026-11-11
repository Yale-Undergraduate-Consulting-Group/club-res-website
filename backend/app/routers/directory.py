"""
Contact directory API - every known person, their best address proof, and
whether the club has reached them (services/contact_directory.py).
"""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.auth_deps import get_current_user
from app.database import get_db
from app.services import contact_directory
from app.services.contact_directory import PROOFS, REACH_STATES, SCAN_LIMIT

router = APIRouter()

_PROOF_LABELS = {
    "replied": "Replied", "delivered": "Delivered before", "bounced": "Bounced",
    "mailbox_confirmed": "Mailbox confirmed", "catch_all": "Catch-all domain",
    "mailbox_rejected": "Mailbox rejected", "unverified": "Unverified",
}
_REACH_LABELS = {
    "replied": "Replied", "bounced": "Bounced", "contacted": "Contacted",
    "queued": "Queued", "not_contacted": "Not contacted",
}


def _filters(q: str | None, company: str | None, proof: str | None, reach: str | None) -> dict:
    if proof and proof not in PROOFS:
        raise HTTPException(422, f"proof must be one of: {', '.join(PROOFS)}")
    if reach and reach not in REACH_STATES:
        raise HTTPException(422, f"reach must be one of: {', '.join(REACH_STATES)}")
    return {"q": q, "company": company, "proof": proof or None, "reach": reach or None}


@router.get("")
async def list_directory(
    q: str | None = None,
    company: str | None = None,
    proof: str | None = None,
    reach: str | None = None,
    ready: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    """People the viewer may see - their contacts, unowned contacts, and prospects
    from runs they may read - one row per address."""
    filters = _filters(q, company, proof, reach)
    db = await get_db()
    try:
        return await contact_directory.search(db, user, ready=ready, limit=limit, offset=offset, **filters)
    finally:
        await db.close()


@router.get("/summary")
async def directory_summary(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        return await contact_directory.summary(db, user)
    finally:
        await db.close()


def _cell(value) -> str:
    """Text a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


@router.get("/export")
async def export_directory(
    q: str | None = None,
    company: str | None = None,
    proof: str | None = None,
    reach: str | None = None,
    ready: bool = False,
    user: dict = Depends(get_current_user),
):
    """The current filter as CSV: the first SCAN_LIMIT people, newest first."""
    filters = _filters(q, company, proof, reach)
    db = await get_db()
    try:
        found = await contact_directory.search(db, user, ready=ready, limit=SCAN_LIMIT, offset=0, **filters)
    finally:
        await db.close()
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["Name", "Title", "Company", "Email", "Source", "Address proof", "Reach", "By", "Last contacted", "Sends", "Company held by"])
    for r in found["items"]:
        reached = r["reach"]
        out.writerow([_cell(v) for v in (
            r["name"], r["title"], r["company"], r["email"], r["source"], _PROOF_LABELS[r["proof"]],
            _REACH_LABELS[reached["state"]], reached["by"], reached["at"], reached["sends"], reached["claimed_by"],
        )])
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=directory.csv",
            "X-Directory-Rows": f"{len(found['items'])}/{found['total']}",
        },
    )
