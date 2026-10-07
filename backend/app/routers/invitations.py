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

@router.post('')
async def create_invitation(payload: InvitationCreate, admin: dict = Depends(get_current_admin)):
    email = payload.email.strip().lower()
    if len(email) > 254 or email.count('@') != 1 or not email.endswith('@yale.edu') or any(c.isspace() for c in email) or not email.split('@')[0]:
        raise HTTPException(400, 'Enter a valid Yale email address')
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        if await (await db.execute('SELECT 1 FROM users WHERE lower(email)=?', (email,))).fetchone():
            raise HTTPException(409, 'Member already exists; manage their access in Members')
        if await (await db.execute("SELECT 1 FROM membership_invitations WHERE email=? AND state='pending' AND expires_at>?", (email,int(time.time())))).fetchone():
            raise HTTPException(409, 'An invitation is already pending; revoke it before replacing it')
        for project in set(payload.project_ids):
            if not await (await db.execute('SELECT 1 FROM projects WHERE id=?',(project,))).fetchone():
                raise HTTPException(400, 'Unknown project')
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        cursor = await db.execute('''INSERT INTO membership_invitations
            (email,token_hash,created_by,project_ids,expires_at,created_at) VALUES (?,?,?,?,?,?)''',
            (email,digest(token),admin['id'],json.dumps(sorted(set(payload.project_ids))),now+7*86400,now))
        await audit(db,admin['id'],'invitation_create','invitation',cursor.lastrowid,{'email':email,'project_ids':sorted(set(payload.project_ids))})
        await db.commit()
        return {'id':cursor.lastrowid,'email':email,'token':token,'state':'pending'}
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
