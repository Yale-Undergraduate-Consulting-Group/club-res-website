"""Alumni: a separate sign-in into the alumni portal (docs/night/security.md,
docs/product/ALUMNI-PORTAL.md).

An alumnus signs in with Google on ``/alumni/login`` (``/api/auth/alumni/google``,
the same registered callback as members) when their verified address is on the
admin's allow-list or its domain is on the admin's domain list. Alumni are not
member accounts: they live in ``alumni_accounts``, never in ``users``, so no
member route, job, digest, Slack tool or export can see or act for one. Their
session is the ``yucg_alumni`` cookie (path ``/api/alumni``) holding a token with
its own audience, which the member session check refuses (app.jwt_utils).

Every alumni request re-checks the account: not revoked, not deleted, still on
the lists. Removing a domain or an address, or revoking the account, ends access
at the next request. Alumni get no club data: the portal's own content (news,
events, directory, board inbox) is in app.routers.alumni_portal. Club admins
decide who may sign in and who is on the alumni board.
"""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.auth_deps import get_current_admin
from app.database import get_db
from app.jwt_utils import ALUMNI_COOKIE, alumni_cookie_kwargs, create_alumni_token, decode_alumni_token
from app.services.transaction_audit import audit

router = APIRouter()
admin_router = APIRouter()

#: Sign-in records older than this are deleted at the next sign-in.
SIGN_IN_RETENTION_SECONDS = 365 * 86400
_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
#: Domains that would open the alumni area to the world or to every student.
#: ponytail: the obvious mistakes only; the admin still decides every domain.
_REFUSED_DOMAINS = frozenset({
    "yale.edu", "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "proton.me", "protonmail.com", "aol.com",
})


async def create_tables(db) -> None:
    """Migration core:4. Additive; the previous release reads none of it."""
    for statement in (
        """CREATE TABLE alumni_domains (
               domain TEXT PRIMARY KEY, added_by INTEGER, added_at INTEGER NOT NULL)""",
        """CREATE TABLE alumni_allowlist (
               email TEXT PRIMARY KEY, note TEXT, added_by INTEGER, added_at INTEGER NOT NULL)""",
        """CREATE TABLE alumni_accounts (
               id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, google_id TEXT,
               name TEXT, created_at INTEGER NOT NULL, last_sign_in_at INTEGER,
               revoked_at INTEGER, revoked_by INTEGER)""",
        """CREATE TABLE alumni_sign_ins (
               id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, account_id INTEGER,
               email_domain TEXT NOT NULL, outcome TEXT NOT NULL)""",
        "CREATE INDEX alumni_sign_ins_at ON alumni_sign_ins(at)",
    ):
        await db.execute(statement)


# --- Eligibility ---------------------------------------------------------------

def clean_domain(value: str) -> str:
    domain = value.strip().lower().lstrip("@")
    if len(domain) > 253 or not _DOMAIN.match(domain):
        raise HTTPException(400, "Enter a domain such as aya.yale.edu")
    if domain in _REFUSED_DOMAINS:
        raise HTTPException(400, f"{domain} cannot be an alumni domain: it would let in people who are not alumni. "
                                 "Add individual addresses instead.")
    return domain


def clean_email(value: str) -> str:
    email = value.strip().lower()
    local, _, domain = email.partition("@")
    if len(email) > 254 or not local or any(c.isspace() for c in email) or "@" in domain or not _DOMAIN.match(domain):
        raise HTTPException(400, "Enter a valid email address")
    return email


async def allowed(db, email: str) -> bool:
    """Whether ``email`` is on the allow-list or in an allowed domain right now."""
    domain = email.rpartition("@")[2]
    row = await (await db.execute(
        "SELECT 1 FROM alumni_allowlist WHERE email = ? UNION ALL SELECT 1 FROM alumni_domains WHERE domain = ? LIMIT 1",
        (email, domain),
    )).fetchone()
    return row is not None


async def sign_in(db, email: str, google_id: str, name: str | None) -> tuple[int | None, str]:
    """(account id or None, outcome) for a verified Google identity, recorded
    in ``alumni_sign_ins``. Runs in the caller's transaction."""
    now = int(time.time())
    account = await (await db.execute(
        "SELECT id, google_id, revoked_at FROM alumni_accounts WHERE email = ?", (email,),
    )).fetchone()
    account_id = account["id"] if account else None
    if account and account["revoked_at"]:
        outcome = "revoked"
    elif not await allowed(db, email):
        outcome = "not_eligible"
    elif account and account["google_id"] and account["google_id"] != google_id:
        outcome = "identity_mismatch"
    else:
        outcome = "signed_in"
        if account:
            await db.execute("UPDATE alumni_accounts SET google_id = ?, name = ?, last_sign_in_at = ? WHERE id = ?",
                             (google_id, name, now, account_id))
        else:
            cursor = await db.execute(
                "INSERT INTO alumni_accounts (email, google_id, name, created_at, last_sign_in_at) VALUES (?, ?, ?, ?, ?)",
                (email, google_id, name, now, now),
            )
            account_id = cursor.lastrowid
    await db.execute("DELETE FROM alumni_sign_ins WHERE at < ?", (now - SIGN_IN_RETENTION_SECONDS,))
    await db.execute("INSERT INTO alumni_sign_ins (at, account_id, email_domain, outcome) VALUES (?, ?, ?, ?)",
                     (now, account_id, email.rpartition("@")[2], outcome))
    return (account_id if outcome == "signed_in" else None), outcome


async def sign_in_response(frontend_url: str, email: str, google_id: str, name: str | None) -> RedirectResponse:
    """The end of the Google callback for an alumni sign-in (routers/auth.py)."""
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        account_id, outcome = await sign_in(db, email, google_id, name)
        await db.commit()
    finally:
        await db.close()
    if account_id is None:
        return RedirectResponse(f"{frontend_url}/alumni/login?error={outcome}")
    response = RedirectResponse(f"{frontend_url}/alumni")
    cookie = alumni_cookie_kwargs()
    response.set_cookie(cookie.pop("key"), create_alumni_token(account_id), **cookie)
    return response


async def get_current_alumni(request: Request) -> dict:
    """The signed-in alumnus, checked against the lists on every request.
    Reads only the alumni cookie: no Bearer header, no API key."""
    payload = decode_alumni_token(request.cookies.get(ALUMNI_COOKIE) or "")
    subject = str((payload or {}).get("sub") or "")
    if subject.isdigit():
        db = await get_db()
        try:
            row = await (await db.execute(
                "SELECT id, email, name, board_since FROM alumni_accounts WHERE id = ? AND revoked_at IS NULL",
                (int(subject),),
            )).fetchone()
            if row and await allowed(db, row["email"]):
                return {"id": row["id"], "email": row["email"], "name": row["name"], "board": bool(row["board_since"])}
        finally:
            await db.close()
    raise HTTPException(401, "Sign in on the alumni page")


# --- The alumni session ----------------------------------------------------------

@router.get("/me")
async def me(alumnus: dict = Depends(get_current_alumni)):
    return {"email": alumnus["email"], "name": alumnus["name"], "board": alumnus["board"]}


@router.post("/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(ALUMNI_COOKIE, path="/api/alumni")
    return response


# --- Admin: who may sign in, who did, and revocation -----------------------------

class DomainAdd(BaseModel):
    domain: str = Field(min_length=3, max_length=253)


class EmailAdd(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    note: str | None = Field(None, max_length=200)


@admin_router.get("")
async def overview(admin: dict = Depends(get_current_admin)):
    db = await get_db()
    try:
        domains = [dict(r) for r in await (await db.execute(
            "SELECT domain, added_at FROM alumni_domains ORDER BY domain")).fetchall()]
        allowlist = [dict(r) for r in await (await db.execute(
            "SELECT email, note, added_at FROM alumni_allowlist ORDER BY email")).fetchall()]
        accounts = []
        for row in await (await db.execute(
            "SELECT id, email, name, created_at, last_sign_in_at, revoked_at, board_since FROM alumni_accounts ORDER BY email",
        )).fetchall():
            account = dict(row)
            account["board"] = bool(account.pop("board_since"))
            accounts.append({**account, "allowed": await allowed(db, row["email"])})
        sign_ins = [dict(r) for r in await (await db.execute(
            """SELECT s.at, s.outcome, s.email_domain, a.email FROM alumni_sign_ins s
               LEFT JOIN alumni_accounts a ON a.id = s.account_id ORDER BY s.id DESC LIMIT 200""")).fetchall()]
        return {"domains": domains, "allowlist": allowlist, "accounts": accounts, "sign_ins": sign_ins}
    finally:
        await db.close()


async def _change(admin: dict, sql: str, params: tuple, action: str, resource_type: str, resource_id, details=None) -> int:
    """Run one admin change with its audit record; returns the rows it touched."""
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute(sql, params)
        if cursor.rowcount:
            await audit(db, admin["id"], action, resource_type, resource_id, details)
        await db.commit()
        return cursor.rowcount
    finally:
        await db.close()


@admin_router.post("/domains", status_code=201)
async def add_domain(payload: DomainAdd, admin: dict = Depends(get_current_admin)):
    domain = clean_domain(payload.domain)
    await _change(admin, "INSERT OR IGNORE INTO alumni_domains (domain, added_by, added_at) VALUES (?, ?, ?)",
                  (domain, admin["id"], int(time.time())), "alumni_domain_add", "alumni_domain", domain)
    return {"domain": domain}


@admin_router.delete("/domains/{domain}")
async def remove_domain(domain: str, admin: dict = Depends(get_current_admin)):
    domain = domain.strip().lower()
    if not await _change(admin, "DELETE FROM alumni_domains WHERE domain = ?", (domain,),
                         "alumni_domain_remove", "alumni_domain", domain):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@admin_router.post("/emails", status_code=201)
async def add_email(payload: EmailAdd, admin: dict = Depends(get_current_admin)):
    email = clean_email(payload.email)
    note = (payload.note or "").strip() or None
    await _change(admin, """INSERT INTO alumni_allowlist (email, note, added_by, added_at) VALUES (?, ?, ?, ?)
                            ON CONFLICT(email) DO UPDATE SET note = excluded.note""",
                  (email, note, admin["id"], int(time.time())), "alumni_email_add", "alumni_email", email)
    return {"email": email, "note": note}


@admin_router.delete("/emails/{email}")
async def remove_email(email: str, admin: dict = Depends(get_current_admin)):
    email = email.strip().lower()
    if not await _change(admin, "DELETE FROM alumni_allowlist WHERE email = ?", (email,),
                         "alumni_email_remove", "alumni_email", email):
        raise HTTPException(404, "Not found")
    return {"ok": True}


@admin_router.post("/accounts/{account_id}/revoke")
async def revoke(account_id: int, admin: dict = Depends(get_current_admin)):
    """Ends the account's access at its next request and keeps it out even if
    its address stays allowed."""
    if not await _change(admin, "UPDATE alumni_accounts SET revoked_at = ?, revoked_by = ? WHERE id = ? AND revoked_at IS NULL",
                         (int(time.time()), admin["id"], account_id), "alumni_revoke", "alumni_account", account_id):
        raise HTTPException(404, "No account to revoke")
    return {"ok": True}


@admin_router.post("/accounts/{account_id}/restore")
async def restore(account_id: int, admin: dict = Depends(get_current_admin)):
    if not await _change(admin, "UPDATE alumni_accounts SET revoked_at = NULL, revoked_by = NULL WHERE id = ? AND revoked_at IS NOT NULL",
                         (account_id,), "alumni_restore", "alumni_account", account_id):
        raise HTTPException(404, "No revoked account")
    return {"ok": True}


class BoardChange(BaseModel):
    board: bool


@admin_router.put("/accounts/{account_id}/board")
async def set_board(account_id: int, payload: BoardChange, admin: dict = Depends(get_current_admin)):
    """Marks an alumni account as an alumni board member, or not. The board
    writes the portal's news and events and reads its inbox; it never manages
    who may sign in and never reaches a member or admin route."""
    if payload.board:
        changed = await _change(admin, "UPDATE alumni_accounts SET board_since = ?, board_set_by = ? WHERE id = ? AND board_since IS NULL",
                                (int(time.time()), admin["id"], account_id), "alumni_board_add", "alumni_account", account_id)
    else:
        changed = await _change(admin, "UPDATE alumni_accounts SET board_since = NULL, board_set_by = ? WHERE id = ? AND board_since IS NOT NULL",
                                (admin["id"], account_id), "alumni_board_remove", "alumni_account", account_id)
    if not changed:
        db = await get_db()
        try:
            if not await (await db.execute("SELECT 1 FROM alumni_accounts WHERE id = ?", (account_id,))).fetchone():
                raise HTTPException(404, "Not found")
        finally:
            await db.close()
    return {"board": payload.board}


@admin_router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, admin: dict = Depends(get_current_admin)):
    """Deletes the account, its sign-in records and its directory profile. What
    the board published or received stays, without the account: posts and
    events keep a generic byline, messages lose their sender. The address can
    sign in again while it is allowed; revoke to keep someone out. Daily backups
    keep a copy for up to 30 days."""
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT email FROM alumni_accounts WHERE id = ?", (account_id,))).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        await db.execute("DELETE FROM alumni_sign_ins WHERE account_id = ?", (account_id,))
        await db.execute("DELETE FROM alumni_profiles WHERE account_id = ?", (account_id,))
        await db.execute("DELETE FROM alumni_rate_events WHERE actor = ?", (f"a:{account_id}",))
        await db.execute("UPDATE alumni_posts SET author_alumni_id = NULL WHERE author_alumni_id = ?", (account_id,))
        await db.execute("UPDATE alumni_events SET author_alumni_id = NULL WHERE author_alumni_id = ?", (account_id,))
        await db.execute("UPDATE alumni_board_messages SET sender_account_id = NULL WHERE sender_account_id = ?", (account_id,))
        await db.execute("UPDATE alumni_post_images SET uploaded_by_alumni = NULL WHERE uploaded_by_alumni = ?", (account_id,))
        await db.execute("DELETE FROM alumni_accounts WHERE id = ?", (account_id,))
        await audit(db, admin["id"], "alumni_delete", "alumni_account", account_id, {"email": row["email"]})
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()
