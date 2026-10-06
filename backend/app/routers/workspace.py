"""Project-scoped document register. S3 object names never grant access."""
import asyncio
import os
import secrets
import time
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from app.auth_deps import get_current_user, get_current_admin
from app.database import get_db
from app.routers.invitations import digest
from app.services.transaction_audit import audit

router = APIRouter()


async def storage_usage(db, user_id):
    configured = await (await db.execute('SELECT value FROM settings WHERE key=?', (f'document_quota_bytes:{user_id}',))).fetchone()
    try:
        quota = int(configured['value'] if configured else os.getenv('STORAGE_QUOTA_BYTES', str(1024**3)))
        if quota < 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(503, 'Storage quota configuration is invalid')
    used = await (await db.execute("SELECT COALESCE(SUM(byte_size),0) AS total FROM workspace_document_versions WHERE uploaded_by=? AND state IN ('pending','ready')", (user_id,))).fetchone()
    return {'quota_bytes': quota, 'reserved_bytes': int(used['total']), 'available_bytes': max(0,quota-int(used['total']))}


@router.get('/storage-quota')
async def get_storage_quota(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        return await storage_usage(db,user['id'])
    finally:
        await db.close()


class StorageQuota(BaseModel):
    quota_bytes: int = Field(ge=0,le=10*1024**4)


@router.put('/storage-quota/{member_id}')
async def set_storage_quota(member_id: int, payload: StorageQuota, admin: dict = Depends(get_current_admin)):
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        if not await (await db.execute('SELECT 1 FROM users WHERE id=?',(member_id,))).fetchone():
            raise HTTPException(404,'Member not found')
        await db.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(f'document_quota_bytes:{member_id}',str(payload.quota_bytes)))
        await audit(db,admin['id'],'storage_quota_update','user',member_id,{'quota_bytes':payload.quota_bytes})
        await db.commit()
        return await storage_usage(db,member_id)
    finally:
        await db.close()

async def project_member(db, project_id: int, user_id: int) -> bool:
    return bool(await (await db.execute('SELECT 1 FROM user_project_assignments WHERE project_id=? AND user_id=?',(project_id,user_id))).fetchone())

async def document_access(db, document_id: int, user: dict, write: bool = False):
    row = await (await db.execute('SELECT * FROM workspace_documents WHERE id=?',(document_id,))).fetchone()
    if not row:
        raise HTTPException(404,'Document not found')
    own = row['owner_user_id'] == user['id']
    visible = own or (not write and (row['visibility']=='club' or (row['visibility']=='project' and await project_member(db,row['project_id'],user['id']))))
    if not visible:
        raise HTTPException(404,'Document not found')
    return dict(row)

async def s3_call(method: str, **kwargs):
    import boto3
    def call():
        client = boto3.client('s3')
        return getattr(client,method)(**kwargs)
    return await asyncio.to_thread(call)

def bucket_name():
    bucket = os.getenv('DOCUMENTS_BUCKET') or os.getenv('CATALOG_BUCKET')
    if not bucket:
        raise HTTPException(503,'Document storage is not configured')
    return bucket


def stored_bucket(version):
    bucket = version['storage_bucket']
    if not bucket:
        raise HTTPException(409,'This legacy file needs a verified storage-bucket migration mapping')
    return bucket

@router.get('/projects')
async def projects(user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        rows = await (await db.execute('''SELECT p.*, a.role_in_project FROM projects p
            JOIN user_project_assignments a ON p.id=a.project_id WHERE a.user_id=? ORDER BY p.created_at DESC''',(user['id'],))).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()

@router.get('/documents')
async def documents(q: str = '', limit: int = 50, offset: int = 0, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        rows = await (await db.execute('''SELECT d.*,u.name AS owner_name,u.email AS owner_email,p.name AS project_name
            FROM workspace_documents d JOIN users u ON u.id=d.owner_user_id LEFT JOIN projects p ON p.id=d.project_id
            WHERE (d.owner_user_id=? OR d.visibility='club' OR (d.visibility='project' AND EXISTS
            (SELECT 1 FROM user_project_assignments a WHERE a.project_id=d.project_id AND a.user_id=?)))
            AND d.title LIKE ? ORDER BY d.created_at DESC,d.id DESC LIMIT ? OFFSET ?''',
            (user['id'],user['id'],'%'+q[:200]+'%',max(1,min(limit,100)),max(0,offset)))).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()

class DocumentCreate(BaseModel):
    title: str = Field(min_length=1,max_length=200)
    visibility: Literal['private','project','club'] = 'private'
    project_id: int | None = None

@router.post('/documents')
async def create_document(payload: DocumentCreate, user: dict = Depends(get_current_user)):
    if not payload.title.strip():
        raise HTTPException(422,'Document title cannot be blank')
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        if payload.project_id and not await project_member(db,payload.project_id,user['id']):
            raise HTTPException(403,'Project membership required')
        if payload.visibility=='project' and not payload.project_id:
            raise HTTPException(400,'Select a project')
        cur = await db.execute('INSERT INTO workspace_documents(title,owner_user_id,project_id,visibility,created_at) VALUES (?,?,?,?,?)',
            (payload.title.strip(),user['id'],payload.project_id,payload.visibility,int(time.time())))
        await audit(db,user['id'],'document_create','document',cur.lastrowid,{'visibility':payload.visibility,'project_id':payload.project_id})
        await db.commit()
        return {'id':cur.lastrowid}
    finally:
        await db.close()

class DocumentUpdate(DocumentCreate):
    revision: int

@router.put('/documents/{document_id}')
async def update_document(document_id: int, payload: DocumentUpdate, user: dict = Depends(get_current_user)):
    if not payload.title.strip():
        raise HTTPException(422,'Document title cannot be blank')
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        row = await document_access(db,document_id,user,True)
        if row['revision'] != payload.revision:
            raise HTTPException(409,'Document changed; reload before editing')
        if payload.project_id and not await project_member(db,payload.project_id,user['id']):
            raise HTTPException(403,'Project membership required')
        if payload.visibility=='project' and not payload.project_id:
            raise HTTPException(400,'Select a project')
        await db.execute('UPDATE workspace_documents SET title=?,project_id=?,visibility=?,revision=revision+1 WHERE id=?',
            (payload.title.strip(),payload.project_id,payload.visibility,document_id))
        await audit(db,user['id'],'document_update','document',document_id,{'revision':payload.revision+1,'visibility':payload.visibility,'project_id':payload.project_id})
        await db.commit()
        return {'ok':True,'revision':payload.revision+1}
    finally:
        await db.close()

class UploadRequest(BaseModel):
    filename: str = Field(min_length=1,max_length=255)
    byte_size: int = Field(gt=0,le=100*1024*1024)
    content_type: str = Field(min_length=1,max_length=150)

@router.post('/documents/{document_id}/uploads')
async def start_upload(document_id: int, payload: UploadRequest, user: dict = Depends(get_current_user)):
    bucket = bucket_name()
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await document_access(db,document_id,user,True)
        usage = await storage_usage(db,user['id'])
        if payload.byte_size > usage['available_bytes']:
            raise HTTPException(409,{'message':'Storage quota exceeded; pending uploads also reserve space',**usage})
        key = f'documents/{document_id}/{secrets.token_urlsafe(24)}'
        cur = await db.execute('''INSERT INTO workspace_document_versions(document_id,uploaded_by,object_key,filename,byte_size,content_type,created_at,storage_bucket)
            VALUES (?,?,?,?,?,?,?,?)''',(document_id,user['id'],key,payload.filename,payload.byte_size,payload.content_type,int(time.time()),bucket))
        version_id = cur.lastrowid
        await audit(db,user['id'],'document_upload_reserve','document',document_id,{'version_id':version_id,'byte_size':payload.byte_size})
        await db.commit()
    finally:
        await db.close()
    try:
        url = await s3_call('generate_presigned_url',ClientMethod='put_object',Params={
            'Bucket':bucket,'Key':key,'ContentType':payload.content_type,
            'ContentLength':payload.byte_size,'IfNoneMatch':'*'},ExpiresIn=300)
    except Exception:
        # No URL reached the caller, so this reservation can safely be released.
        db = await get_db()
        try:
            await db.execute('BEGIN IMMEDIATE')
            await db.execute("UPDATE workspace_document_versions SET state='failed' WHERE id=? AND state='pending'",(version_id,))
            await audit(db,user['id'],'document_upload_sign_failed','document',document_id,{'version_id':version_id})
            await db.commit()
        finally:
            await db.close()
        raise
    db = await get_db()
    try:
        # Conservative upper bound recorded after signing, never creation time.
        await db.execute('UPDATE workspace_document_versions SET presign_expires_at=? WHERE id=?',(int(time.time())+300,version_id))
        await db.commit()
    finally:
        await db.close()
    return {'version_id':version_id,'upload':{'url':url,'method':'PUT','headers':{
        'Content-Type':payload.content_type,'If-None-Match':'*'}}}

@router.post('/documents/{document_id}/uploads/{version_id}/complete')
async def finish_upload(document_id: int, version_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await document_access(db,document_id,user,True)
        row = await (await db.execute('SELECT * FROM workspace_document_versions WHERE id=? AND document_id=?',(version_id,document_id))).fetchone()
        if not row:
            raise HTTPException(404,'Upload not found')
        if row['state']=='ready':
            return {'ok':True}
        if row['state']!='pending':
            raise HTTPException(409,'Upload is no longer pending')
        head = await s3_call('head_object',Bucket=stored_bucket(row),Key=row['object_key'])
        if head['ContentLength'] != row['byte_size'] or head.get('ContentType') != row['content_type'] or not head.get('VersionId') or head['VersionId']=='null':
            raise HTTPException(409,'File metadata mismatch or S3 versioning is disabled')
        await db.execute('BEGIN IMMEDIATE')
        await document_access(db,document_id,user,True)
        current = await (await db.execute('SELECT state FROM workspace_document_versions WHERE id=?',(version_id,))).fetchone()
        if current['state']=='ready':
            await db.commit()
            return {'ok':True}
        if current['state']!='pending':
            raise HTTPException(409,'Upload is no longer pending')
        await db.execute("UPDATE workspace_document_versions SET state='ready',s3_version_id=? WHERE id=? AND state='pending'",(head['VersionId'],version_id))
        await db.execute('UPDATE workspace_documents SET current_version=?,revision=revision+1 WHERE id=? AND current_version<?',(version_id,document_id,version_id))
        # Uploads used to be queued for chatbot retrieval indexing. The
        # assistant is gone, so nothing drains that queue; recording rows no
        # reader consumes would just accumulate false 'pending' state.
        await audit(db,user['id'],'document_upload_complete','document',document_id,{'version_id':version_id,'byte_size':row['byte_size']})
        await db.commit()
        return {'ok':True}
    finally:
        await db.close()

@router.post('/documents/{document_id}/uploads/{version_id}/abandon')
async def abandon_upload(document_id: int, version_id: int, user: dict = Depends(get_current_user)):
    """Release only an expired, empty reservation, sealing its key against late PUTs."""
    from botocore.exceptions import ClientError
    db = await get_db()
    try:
        await document_access(db,document_id,user,True)
        row = await (await db.execute('SELECT * FROM workspace_document_versions WHERE id=? AND document_id=?',(version_id,document_id))).fetchone()
        if not row:
            raise HTTPException(404,'Upload not found')
        if row['state']=='abandoned':
            return {'ok':True,'state':'abandoned'}
        if row['state']!='pending':
            raise HTTPException(409,'Only pending uploads may be abandoned')
        if not row['presign_expires_at'] or row['presign_expires_at']>int(time.time()):
            raise HTTPException(409,'Wait until the upload URL expires; unknown expiry requires administrator review')
        upload = dict(row)
    finally:
        await db.close()
    marker = {'abandoned-reservation':str(version_id)}
    try:
        head = await s3_call('head_object',Bucket=stored_bucket(upload),Key=upload['object_key'])
    except ClientError as exc:
        if str(exc.response.get('Error',{}).get('Code')) not in ('404','NoSuchKey','NotFound'):
            raise HTTPException(502,'Cannot confirm that the upload is empty') from exc
        # Expiry alone does not cancel a PUT already in progress. A conditional
        # empty object wins the same key or fails if the actual file won first.
        try:
            head = await s3_call('put_object',Bucket=stored_bucket(upload),Key=upload['object_key'],Body=b'',IfNoneMatch='*',
                ContentType='application/octet-stream',Metadata=marker)
        except ClientError as put_exc:
            if str(put_exc.response.get('Error',{}).get('Code')) in ('412','PreconditionFailed','409','ConditionalRequestConflict'):
                raise HTTPException(409,'Upload arrived during cleanup; complete the file instead') from put_exc
            raise HTTPException(502,'Could not safely reserve the abandoned object key') from put_exc
    else:
        if head.get('ContentLength')!=0 or head.get('Metadata')!=marker:
            raise HTTPException(409,'An uploaded object exists; complete the file or use reviewed retention cleanup')
        # The marker also makes recovery idempotent after a process interruption.
    if not head.get('VersionId') or head['VersionId']=='null':
        raise HTTPException(409,'Storage versioning must be enabled before cleanup can be confirmed')
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await document_access(db,document_id,user,True)
        current = await (await db.execute('SELECT state FROM workspace_document_versions WHERE id=?',(version_id,))).fetchone()
        if current['state']=='abandoned':
            await db.commit()
            return {'ok':True,'state':'abandoned'}
        if current['state']!='pending':
            raise HTTPException(409,'Upload state changed; reload before cleanup')
        await db.execute("UPDATE workspace_document_versions SET state='abandoned',s3_version_id=? WHERE id=?",(head['VersionId'],version_id))
        await audit(db,user['id'],'document_upload_abandon','document',document_id,{'version_id':version_id,'released_bytes':upload['byte_size']})
        await db.commit()
        return {'ok':True,'state':'abandoned'}
    finally:
        await db.close()

@router.get('/documents/{document_id}/versions')
async def versions(document_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        document = await document_access(db,document_id,user)
        rows = await (await db.execute("SELECT id,filename,byte_size,content_type,created_at,state,uploaded_by,presign_expires_at FROM workspace_document_versions WHERE document_id=? AND (state='ready' OR ?=1) ORDER BY id DESC",(document_id,int(document['owner_user_id']==user['id'])))).fetchall()
        return [dict(row) for row in rows]
    finally:
        await db.close()

async def download_url(db, document_id: int, version_id: int | None = None, expires: int = 60):
    if version_id is None:
        doc = await (await db.execute('SELECT current_version FROM workspace_documents WHERE id=?',(document_id,))).fetchone()
        version_id = doc['current_version'] if doc else 0
    row = await (await db.execute("SELECT * FROM workspace_document_versions WHERE id=? AND document_id=? AND state='ready'",(version_id,document_id))).fetchone()
    if not row:
        raise HTTPException(404,'No completed file version')
    return await s3_call('generate_presigned_url',ClientMethod='get_object',Params={
        'Bucket':stored_bucket(row),'Key':row['object_key'],'VersionId':row['s3_version_id'],
        'ResponseContentDisposition':'attachment','ResponseContentType':'application/octet-stream'},ExpiresIn=expires)

@router.get('/documents/{document_id}/download')
async def download(document_id: int, version_id: int | None = None, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await document_access(db,document_id,user)
        return {'url':await download_url(db,document_id,version_id)}
    finally:
        await db.close()

@router.post('/documents/{document_id}/shares')
async def share(document_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await document_access(db,document_id,user,True)
        token = secrets.token_urlsafe(32)
        cur = await db.execute('INSERT INTO workspace_document_shares(document_id,token_hash,created_by,expires_at) VALUES (?,?,?,?)',(document_id,digest(token),user['id'],int(time.time())+86400))
        await audit(db,user['id'],'document_share_create','document',document_id,{'share_id':cur.lastrowid,'expires_in':86400})
        await db.commit()
        from app.routers.auth import BACKEND_URL
        return {'id':cur.lastrowid,'url':f'{BACKEND_URL}/api/workspace/shared/{token}','expires_in':86400}
    finally:
        await db.close()

@router.get('/documents/{document_id}/shares')
async def shares(document_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await document_access(db,document_id,user,True)
        return [dict(row) for row in await (await db.execute('SELECT id,expires_at,revoked_at FROM workspace_document_shares WHERE document_id=?',(document_id,))).fetchall()]
    finally:
        await db.close()

@router.delete('/documents/{document_id}/shares/{share_id}')
async def revoke_share(document_id: int, share_id: int, user: dict = Depends(get_current_user)):
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        await document_access(db,document_id,user,True)
        await db.execute('UPDATE workspace_document_shares SET revoked_at=? WHERE id=? AND document_id=?',(int(time.time()),share_id,document_id))
        await audit(db,user['id'],'document_share_revoke','document',document_id,{'share_id':share_id})
        await db.commit()
        return {'ok':True}
    finally:
        await db.close()

@router.get('/shared/{token}')
async def public_share(token: str):
    db = await get_db()
    try:
        row = await (await db.execute('''SELECT s.* FROM workspace_document_shares s JOIN users u ON u.id=s.created_by
            WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.is_active=1''',(digest(token),int(time.time())))).fetchone()
        if not row:
            raise HTTPException(404,'Share expired or revoked')
        url = await download_url(db,row['document_id'],expires=max(1,min(60,row['expires_at']-int(time.time()))))
        return RedirectResponse(url,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    finally:
        await db.close()
