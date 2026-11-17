"""Alumni: a separate sign-in and a small read-only area (docs/night/security.md).

An alumnus signs in with Google on ``/alumni/login`` (``/api/auth/alumni/google``,
the same registered callback as members) when their verified address is on the
admin's allow-list or its domain is on the admin's domain list. Alumni are not
member accounts: they live in ``alumni_accounts``, never in ``users``, so no
member route, job, digest, Slack tool or export can see or act for one. Their
session is the ``yucg_alumni`` cookie (path ``/api/alumni``) holding a token with
its own audience, which the member session check refuses (app.jwt_utils).

Every alumni request re-checks the account: not revoked, not deleted, still on
the lists. Removing a domain or an address, or revoking the account, ends access
at the next request. What alumni may read is decided in app.services.resources
(``is_alumni``): past projects whose client agreed to public disclosure.
"""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.auth_deps import get_current_admin
from app.database import get_db
from app.jwt_utils import ALUMNI_COOKIE, alumni_cookie_kwargs, create_alumni_token, decode_alumni_token
from app.services import document_storage, resources
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
                "SELECT id, email, name FROM alumni_accounts WHERE id = ? AND revoked_at IS NULL", (int(subject),),
            )).fetchone()
            if row and await allowed(db, row["email"]):
                return {"id": row["id"], "email": row["email"], "name": row["name"], "role": "alumni"}
        finally:
            await db.close()
    raise HTTPException(401, "Sign in on the alumni page")


async def _resources_open(db) -> None:
    """Alumni follow the Resources app's restriction: a restricted app is
    closed to them as well (app.app_access)."""
    row = await (await db.execute("SELECT restricted FROM app_access WHERE app = 'resources'")).fetchone()
    if row and row["restricted"]:
        raise HTTPException(403, "Resources are not open to alumni at the moment")


# --- The alumni area -------------------------------------------------------------

@router.get("/me")
async def me(alumnus: dict = Depends(get_current_alumni)):
    return {"email": alumnus["email"], "name": alumnus["name"]}


@router.post("/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(ALUMNI_COOKIE, path="/api/alumni")
    return response


@router.get("/resources")
async def list_resources(
    q: str = Query("", max_length=200), semester: str = Query("", max_length=40), industry: str = Query("", max_length=200),
    limit: int = 60, offset: int = 0, alumnus: dict = Depends(get_current_alumni),
):
    db = await get_db()
    try:
        await _resources_open(db)
        rows, total, facets = await resources.search(
            db, alumnus, q=q, semester=semester, industry=industry, limit=limit, offset=offset)
        return {"items": await resources.present(db, alumnus, rows), "total": total,
                "facets": {"semesters": facets["semesters"], "industries": facets["industries"]}}
    finally:
        await db.close()


@router.get("/resources/{resource_id}/files/{file_id}/download")
async def download_file(resource_id: int, file_id: int, alumnus: dict = Depends(get_current_alumni)):
    from app.routers.workspace import download_url

    db = await get_db()
    try:
        await _resources_open(db)
        found = await resources.file_for_download(db, alumnus, resource_id, file_id)
        if not found:
            raise HTTPException(404, "Not found")
        url = await download_url(db, file_id)
        if url.startswith(document_storage.LOCAL_URL_PATH):
            # No-bucket environments: the same signed link, served behind the alumni session.
            url = "/api/alumni/local-file" + url[len(document_storage.LOCAL_URL_PATH):]
        await db.execute("BEGIN IMMEDIATE")
        await audit(db, None, "alumni_resource_download", "resource", resource_id,
                    {"alumni_account_id": alumnus["id"], "document_id": file_id})
        await db.commit()
        name = await (await db.execute(
            "SELECT v.filename FROM workspace_document_versions v JOIN workspace_documents d ON d.current_version = v.id WHERE d.id = ?",
            (file_id,),
        )).fetchone()
        return {"url": url, "filename": name["filename"] if name else ""}
    finally:
        await db.close()


@router.get("/local-file")
async def local_file(token: str, alumnus: dict = Depends(get_current_alumni)):
    """A file from the local storage directory (no-bucket environments only),
    for the short-lived link the download route issued. The link is not the
    authority: the file must still be open to this alumnus, current version only."""
    version_id = document_storage.read_local_token(token)
    if version_id is None:
        raise HTTPException(404, "Not found")
    db = await get_db()
    try:
        await _resources_open(db)
        row = await (await db.execute(
            """SELECT v.object_key, v.filename, v.storage_bucket, f.resource_id, f.document_id
               FROM workspace_document_versions v
               JOIN resource_files f ON f.document_id = v.document_id
               JOIN workspace_documents d ON d.id = v.document_id AND d.current_version = v.id
               WHERE v.id = ? AND v.state = 'ready'""",
            (version_id,),
        )).fetchone()
        if (not row or row["storage_bucket"] != document_storage.LOCAL
                or not await resources.file_for_download(db, alumnus, row["resource_id"], row["document_id"])):
            raise HTTPException(404, "Not found")
        path = document_storage.local_path(row["object_key"])
        if not path.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(path, filename=row["filename"], media_type="application/octet-stream",
                            content_disposition_type="attachment")
    finally:
        await db.close()


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
            "SELECT id, email, name, created_at, last_sign_in_at, revoked_at FROM alumni_accounts ORDER BY email",
        )).fetchall():
            accounts.append({**dict(row), "allowed": await allowed(db, row["email"])})
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


@admin_router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, admin: dict = Depends(get_current_admin)):
    """Deletes the account and its sign-in records. The address can sign in
    again while it is allowed; revoke to keep someone out. Daily backups keep
    a copy for up to 30 days."""
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT email FROM alumni_accounts WHERE id = ?", (account_id,))).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        await db.execute("DELETE FROM alumni_sign_ins WHERE account_id = ?", (account_id,))
        await db.execute("DELETE FROM alumni_accounts WHERE id = ?", (account_id,))
        await audit(db, admin["id"], "alumni_delete", "alumni_account", account_id, {"email": row["email"]})
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()
