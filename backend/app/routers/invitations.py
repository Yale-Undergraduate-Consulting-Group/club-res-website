"""Admin-created invitations; sending is an explicit authenticated operation."""
import hashlib
import json
import secrets
import time
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.auth_deps import get_current_admin
from app.database import get_db
from app.services.transaction_audit import audit

router = APIRouter()

def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

class InvitationCreate(BaseModel):
    email: str
    project_ids: list[int] = Field(default_factory=list, max_length=100)

class InvitationSend(BaseModel):
    token: str | None = Field(default=None, min_length=20, max_length=200)

@router.get('')
async def list_invitations(admin: dict = Depends(get_current_admin)):
    db = await get_db()
    try:
        rows = await (await db.execute('''SELECT id,email,role,project_ids,
            CASE WHEN state='pending' AND expires_at<=? THEN 'expired' ELSE state END AS state,
            delivery_state,expires_at,created_at,sent_at,accepted_at,delivery_error
            FROM membership_invitations ORDER BY created_at DESC''', (int(time.time()),))).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()

def _valid_yale(email: str) -> bool:
    return (len(email) <= 254 and email.count('@') == 1 and email.endswith('@yale.edu')
            and not any(c.isspace() for c in email) and bool(email.split('@')[0]))


async def _insert(db, email: str, project_ids: list[int], admin: dict) -> dict:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    cursor = await db.execute('''INSERT INTO membership_invitations
        (email,token_hash,created_by,project_ids,expires_at,created_at) VALUES (?,?,?,?,?,?)''',
        (email,digest(token),admin['id'],json.dumps(project_ids),now+7*86400,now))
    await audit(db,admin['id'],'invitation_create','invitation',cursor.lastrowid,{'email':email,'project_ids':project_ids})
    return {'id':cursor.lastrowid,'email':email,'token':token,'state':'pending'}


async def _skip_reason(db, email: str) -> str | None:
    if not _valid_yale(email):
        return 'Not a Yale email address'
    if await (await db.execute('SELECT 1 FROM users WHERE lower(email)=?', (email,))).fetchone():
        return 'Already a member'
    if await (await db.execute("SELECT 1 FROM membership_invitations WHERE email=? AND state='pending' AND expires_at>?", (email,int(time.time())))).fetchone():
        return 'Invitation already pending'
    return None


async def _check_projects(db, project_ids: list[int]) -> list[int]:
    for project in set(project_ids):
        if not await (await db.execute('SELECT 1 FROM projects WHERE id=?',(project,))).fetchone():
            raise HTTPException(400, 'Unknown project')
    return sorted(set(project_ids))


@router.post('')
async def create_invitation(payload: InvitationCreate, admin: dict = Depends(get_current_admin)):
    email = payload.email.strip().lower()
    if not _valid_yale(email):
        raise HTTPException(400, 'Enter a valid Yale email address')
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        reason = await _skip_reason(db, email)
        if reason == 'Already a member':
            raise HTTPException(409, 'Member already exists; manage their access in Members')
        if reason:
            raise HTTPException(409, 'An invitation is already pending; revoke it before replacing it')
        created = await _insert(db, email, await _check_projects(db, payload.project_ids), admin)
        await db.commit()
        return created
    finally:
        await db.close()


class InvitationBulk(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=300)
    project_ids: list[int] = Field(default_factory=list, max_length=100)


@router.post('/bulk')
async def create_invitations(payload: InvitationBulk, admin: dict = Depends(get_current_admin)):
    """Invite a pasted list at once. Every address gets a result: created, or
    skipped with the reason; one bad address never stops the rest."""
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        project_ids = await _check_projects(db, payload.project_ids)
        results, seen = [], set()
        for raw in payload.emails:
            email = raw.strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            reason = await _skip_reason(db, email)
            if reason:
                results.append({'email': email, 'status': 'skipped', 'reason': reason})
            else:
                results.append({**await _insert(db, email, project_ids, admin), 'status': 'created'})
        await db.commit()
        return {'results': results,
                'created': sum(r['status'] == 'created' for r in results),
                'skipped': sum(r['status'] == 'skipped' for r in results)}
    finally:
        await db.close()

@router.post('/{invitation_id}/send')
async def send_invitation(invitation_id: int, payload: InvitationSend, admin: dict = Depends(get_current_admin)):
    from app.routers.auth import BACKEND_URL
    from app.services.gmail_api import send_via_gmail_api
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        row = await (await db.execute('SELECT * FROM membership_invitations WHERE id=?',(invitation_id,))).fetchone()
        if not row:
            raise HTTPException(404,'Invitation not found')
        if row['created_by'] != admin['id']:
            raise HTTPException(403,'Only the invitation creator may send it from their Gmail account')
        if payload.token is not None and row['token_hash'] != digest(payload.token):
            raise HTTPException(404,'Invitation not found')
        if row['state'] != 'pending' or row['expires_at'] <= time.time() or row['delivery_state'] != 'ready':
            raise HTTPException(409,'Invitation cannot be sent; inspect its state before replacing it')
        token = payload.token or secrets.token_urlsafe(32)
        await db.execute("UPDATE membership_invitations SET delivery_state='sending',token_hash=? WHERE id=?",(digest(token),invitation_id))
        await audit(db,admin['id'],'invitation_send_claim','invitation',invitation_id)
        await db.commit()
        link = f'{BACKEND_URL}/api/auth/google?invitation={token}'
        try:
            await send_via_gmail_api(user_id=admin['id'],to_email=row['email'],subject='Invitation to YUCG Outreach',body=f'You have been invited to the club workspace. Sign in with {row["email"]}:\n\n{link}\n\nThis invitation expires in seven days.')
        except Exception:
            await db.execute("UPDATE membership_invitations SET delivery_state='ambiguous',delivery_error='Delivery could not be confirmed. Check Sent mail before replacing this invitation.' WHERE id=?",(invitation_id,))
            await audit(db,admin['id'],'invitation_send_ambiguous','invitation',invitation_id)
            await db.commit()
            raise HTTPException(502,'Delivery could not be confirmed; automatic retry is disabled')
        await db.execute("UPDATE membership_invitations SET delivery_state='sent',sent_at=? WHERE id=?",(int(time.time()),invitation_id))
        await audit(db,admin['id'],'invitation_send_complete','invitation',invitation_id)
        await db.commit()
        return {'ok':True}
    finally:
        await db.close()

@router.post('/{invitation_id}/link')
async def replace_with_shareable_link(invitation_id: int, admin: dict = Depends(get_current_admin)):
    """Rotate a pending invite's hashed token and return the only plaintext copy.

    This is the recovery path when the recipient cannot find an email. It
    deliberately invalidates that email's link rather than storing a reusable
    invite secret in the database.
    """
    from app.routers.auth import BACKEND_URL

    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        row = await (await db.execute(
            'SELECT * FROM membership_invitations WHERE id=?', (invitation_id,))).fetchone()
        if not row:
            raise HTTPException(404, 'Invitation not found')
        if row['created_by'] != admin['id']:
            raise HTTPException(403, 'Only the invitation creator may replace its link')
        if row['state'] != 'pending' or row['expires_at'] <= time.time():
            raise HTTPException(409, 'Invitation is no longer pending')
        token = secrets.token_urlsafe(32)
        await db.execute(
            """UPDATE membership_invitations
               SET token_hash=?, delivery_state='manual',
                   delivery_error='The emailed link was replaced. Share the new link directly.'
               WHERE id=?""",
            (digest(token), invitation_id),
        )
        await audit(db, admin['id'], 'invitation_link_rotate', 'invitation', invitation_id)
        await db.commit()
        return {
            'link': f'{BACKEND_URL}/api/auth/google?invitation={token}',
            'expires_at': row['expires_at'],
        }
    finally:
        await db.close()


@router.delete('/{invitation_id}')
async def revoke_invitation(invitation_id: int, admin: dict = Depends(get_current_admin)):
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await db.execute("UPDATE membership_invitations SET state='revoked' WHERE id=? AND state='pending'",(invitation_id,))
        await audit(db,admin['id'],'invitation_revoke','invitation',invitation_id)
        await db.commit()
        return {'ok':True}
    finally:
        await db.close()


# --- Join sessions: a QR code shown in a room -------------------------------
# While a session is open, any verified @yale.edu Google account that signs in
# through its link becomes a member at once (auth._do_google_callback). It is
# bounded three ways: it expires (at most four hours), it admits at most
# `max_members` new people, and an admin can close it early. Members who were
# deactivated stay deactivated; existing members simply sign in.

JOIN_MAX_MINUTES = 240


async def create_join_tables(db) -> None:
    """Migration core:3 - additive (app/migrations.py)."""
    await db.execute(
        """CREATE TABLE IF NOT EXISTS join_sessions (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               token_hash TEXT NOT NULL UNIQUE,
               created_by INTEGER NOT NULL REFERENCES users(id),
               created_at INTEGER NOT NULL,
               expires_at INTEGER NOT NULL,
               max_members INTEGER NOT NULL,
               closed_at INTEGER)"""
    )
    await db.execute(
        """CREATE TABLE IF NOT EXISTS join_session_members (
               session_id INTEGER NOT NULL REFERENCES join_sessions(id),
               user_id INTEGER NOT NULL REFERENCES users(id),
               joined_at INTEGER NOT NULL,
               PRIMARY KEY (session_id, user_id))"""
    )
    await db.execute("ALTER TABLE oauth_challenges ADD COLUMN join_session_id INTEGER")


_SESSION = """SELECT s.*, (SELECT COUNT(*) FROM join_session_members m WHERE m.session_id = s.id) AS joined
              FROM join_sessions s"""


def _admits(session: dict) -> bool:
    return (not session.get('closed_at') and session['expires_at'] > time.time()
            and session['joined'] < session['max_members'])


async def open_join_session(db, token: str) -> dict | None:
    """The session behind ``token`` while it still admits people, else None."""
    row = await (await db.execute(_SESSION + ' WHERE s.token_hash=?', (digest(token),))).fetchone()
    return dict(row) if row and _admits(dict(row)) else None


async def admit_through_join_session(db, session_id: int, email: str, name: str | None,
                                     google_id: str, picture: str | None) -> int | None:
    """Create the member if the session still admits people. Runs inside the
    caller's write transaction, so the cap holds under concurrent sign-ins."""
    row = await (await db.execute(_SESSION + ' WHERE s.id=?', (session_id,))).fetchone()
    if not row or not _admits(dict(row)):
        return None
    cursor = await db.execute("INSERT INTO users(email,name,role,google_id,picture) VALUES (?,?,?,?,?)",
                              (email, name, 'standard', google_id, picture))
    user_id = cursor.lastrowid
    await db.execute("INSERT INTO join_session_members(session_id,user_id,joined_at) VALUES (?,?,?)",
                     (session_id, user_id, int(time.time())))
    await audit(db, user_id, 'join_session_accept', 'join_session', session_id, {'email': email})
    return user_id


class JoinSessionCreate(BaseModel):
    minutes: int = Field(default=120, ge=5, le=JOIN_MAX_MINUTES)
    max_members: int = Field(default=200, ge=1, le=500)


async def _join_session_view(db, session_id: int) -> dict:
    row = await (await db.execute(_SESSION + ' WHERE s.id=?', (session_id,))).fetchone()
    if not row:
        raise HTTPException(404, 'Join session not found')
    session = dict(row)
    members = await (await db.execute(
        """SELECT u.name, u.email, m.joined_at FROM join_session_members m JOIN users u ON u.id = m.user_id
           WHERE m.session_id=? ORDER BY m.joined_at DESC""", (session_id,))).fetchall()
    return {'id': session['id'], 'expires_at': session['expires_at'], 'max_members': session['max_members'],
            'closed': bool(session['closed_at']), 'open': _admits(session),
            'members': [dict(m) for m in members]}


@router.post('/join-sessions', status_code=201)
async def create_join_session(payload: JoinSessionCreate, admin: dict = Depends(get_current_admin)):
    """Open a join session; the token is returned once and only its hash is stored."""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        cursor = await db.execute(
            'INSERT INTO join_sessions(token_hash,created_by,created_at,expires_at,max_members) VALUES (?,?,?,?,?)',
            (digest(token), admin['id'], now, now + payload.minutes * 60, payload.max_members))
        await audit(db, admin['id'], 'join_session_open', 'join_session', cursor.lastrowid,
                    {'minutes': payload.minutes, 'max_members': payload.max_members})
        await db.commit()
        return {**await _join_session_view(db, cursor.lastrowid), 'token': token}
    finally:
        await db.close()


@router.get('/join-sessions/{session_id}')
async def get_join_session(session_id: int, admin: dict = Depends(get_current_admin)):
    db = await get_db()
    try:
        return await _join_session_view(db, session_id)
    finally:
        await db.close()


@router.post('/join-sessions/{session_id}/close')
async def close_join_session(session_id: int, admin: dict = Depends(get_current_admin)):
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await db.execute('UPDATE join_sessions SET closed_at=? WHERE id=? AND closed_at IS NULL',
                         (int(time.time()), session_id))
        await audit(db, admin['id'], 'join_session_close', 'join_session', session_id)
        await db.commit()
        return await _join_session_view(db, session_id)
    finally:
        await db.close()
