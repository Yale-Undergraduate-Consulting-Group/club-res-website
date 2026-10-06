"""Private/project/club boundaries and delivery claims, using fake S3/Gmail only."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET'] = 'workspace-boundary-test-secret-xxxxxxxx'
os.environ['DOCUMENTS_BUCKET'] = 'test-private-bucket'
from fastapi import HTTPException
from app.database import init_db, get_db
from app.routers import workspace as w, invitations as i


async def denied(coro, status):
    try:
        await coro
        raise AssertionError('Operation unexpectedly allowed')
    except HTTPException as exc:
        assert exc.status_code == status, exc


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = 'sqlite:///' + tmp + '/test.db'
        await init_db()
        db = await get_db()
        await db.execute("INSERT INTO users(id,email,role) VALUES (1,'one@yale.edu','standard'),(2,'two@yale.edu','standard'),(3,'admin@yale.edu','admin')")
        await db.execute("INSERT INTO projects(id,name) VALUES (1,'Project')")
        await db.execute("INSERT INTO user_project_assignments(user_id,project_id) VALUES (1,1),(2,1)")
        await db.commit()
        a,b,admin = {'id':1},{'id':2},{'id':3}
        await denied(w.create_document(w.DocumentCreate(title='   '),a),422)
        private = (await w.create_document(w.DocumentCreate(title='Private'), a))['id']
        project = (await w.create_document(w.DocumentCreate(title='Project',visibility='project',project_id=1), a))['id']
        club = (await w.create_document(w.DocumentCreate(title='Club',visibility='club'), a))['id']
        assert {d['id'] for d in await w.documents(user=b)} == {project,club}
        assert {d['id'] for d in await w.documents(user=admin)} == {club}, 'Admin is not implicit private-data owner'
        await denied(w.download(private,user=b),404)
        await denied(w.versions(private,user=b),404)
        await denied(w.share(project,user=b),404)
        await denied(w.start_upload(project,w.UploadRequest(filename='x',byte_size=3,content_type='text/plain'),b),404)
        await denied(w.create_document(w.DocumentCreate(title='Wrong project',project_id=1),admin),403)

        s3 = AsyncMock(return_value={'url':'https://s3.example/upload','fields':{}})
        with patch.object(w,'s3_call',s3):
            upload = await w.start_upload(private,w.UploadRequest(filename='x',byte_size=3,content_type='text/plain'),a)
            assert s3.call_args.kwargs['Params']['ContentLength'] == 3
            assert s3.call_args.kwargs['Params']['IfNoneMatch'] == '*'
            assert upload['upload']['headers']['If-None-Match'] == '*'
            os.environ['DOCUMENTS_BUCKET'] = 'replacement-private-bucket'
            s3.return_value = {'ContentLength':4,'ContentType':'text/plain','VersionId':'v1'}
            await denied(w.finish_upload(private,upload['version_id'],a),409)
            assert s3.call_args.kwargs['Bucket']=='test-private-bucket'
            s3.return_value = {'ContentLength':3,'ContentType':'text/plain','VersionId':'null'}
            await denied(w.finish_upload(private,upload['version_id'],a),409)
            s3.return_value = {'ContentLength':3,'ContentType':'text/plain','VersionId':'v1'}
            await w.finish_upload(private,upload['version_id'],a)
            await denied(w.finish_upload(project,upload['version_id'],a),404)
            s3.return_value = 'https://s3.example/approved-download'
            await w.download(private,user=a)
            assert s3.call_args.kwargs['Params']['VersionId'] == 'v1'
            assert s3.call_args.kwargs['Params']['Bucket'] == 'test-private-bucket'
            await db.execute('UPDATE workspace_document_versions SET storage_bucket=NULL WHERE id=?',(upload['version_id'],))
            await db.commit()
            calls = s3.await_count
            await denied(w.download(private,user=a),409)
            assert s3.await_count==calls, 'Legacy bucket must not default to the current bucket'
            await db.execute('UPDATE workspace_document_versions SET storage_bucket=? WHERE id=?',('test-private-bucket',upload['version_id']))
            await db.commit()
            share = await w.share(private,a)
            token = share['url'].rsplit('/',1)[1]
            assert (await w.public_share(token)).status_code == 307
            await w.revoke_share(private,share['id'],a)
            await denied(w.public_share(token),404)
            os.environ['DOCUMENTS_BUCKET'] = 'test-private-bucket'
        # Revision conflict rejects stale concurrent edits.
        await w.update_document(club,w.DocumentUpdate(title='Changed',revision=1),a)
        await denied(w.update_document(club,w.DocumentUpdate(title='Stale',revision=1),a),409)

        invite = await i.create_invitation(i.InvitationCreate(email='new@yale.edu',project_ids=[1]),admin)
        await denied(i.create_invitation(i.InvitationCreate(email='new@yale.edu'),admin),409)
        await denied(i.send_invitation(invite['id'],i.InvitationSend(token=invite['token']),a),403)
        sender = AsyncMock(side_effect=TimeoutError('uncertain delivery'))
        with patch('app.services.gmail_api.send_via_gmail_api',sender):
            await denied(i.send_invitation(invite['id'],i.InvitationSend(token=invite['token']),admin),502)
            await denied(i.send_invitation(invite['id'],i.InvitationSend(token=invite['token']),admin),409)
            assert sender.await_count == 1
            assert sender.call_args.kwargs['user_id'] == 3
            assert sender.call_args.kwargs['to_email'] == 'new@yale.edu'
        await i.revoke_invitation(invite['id'],admin)
        assert (await i.list_invitations(admin))[0]['state'] == 'revoked'
        reloaded = await i.create_invitation(i.InvitationCreate(email='reloaded@yale.edu'),admin)
        delivered = AsyncMock(return_value={'ok':True})
        with patch('app.services.gmail_api.send_via_gmail_api',delivered):
            await i.send_invitation(reloaded['id'],i.InvitationSend(),admin)
        rotated = await (await db.execute('SELECT token_hash,delivery_state FROM membership_invitations WHERE id=?',(reloaded['id'],))).fetchone()
        assert rotated['token_hash'] != i.digest(reloaded['token'])
        assert rotated['delivery_state']=='sent'
        assert reloaded['token'] not in delivered.call_args.kwargs['body']
        # Quota reservations serialize across requests and count unfinished uploads.
        await w.set_storage_quota(1,w.StorageQuota(quota_bytes=8),admin)
        request = w.UploadRequest(filename='reserved',byte_size=4,content_type='text/plain')
        with patch.object(w,'s3_call',AsyncMock(return_value='https://example.test/put')):
            results = await asyncio.gather(w.start_upload(private,request,a),w.start_upload(private,request,a),return_exceptions=True)
        assert sum(isinstance(r,dict) for r in results) == 1
        assert sum(isinstance(r,HTTPException) and r.status_code==409 for r in results) == 1
        assert (await w.get_storage_quota(a))['reserved_bytes'] == 7
        pending = next(row for row in await w.versions(private,a) if row['state']=='pending')
        await denied(w.abandon_upload(private,pending['id'],b),404)
        await denied(w.abandon_upload(private,pending['id'],a),409)
        await db.execute('UPDATE workspace_document_versions SET presign_expires_at=1 WHERE id=?',(pending['id'],))
        await db.commit()
        with patch.object(w,'s3_call',AsyncMock(return_value={'ContentLength':4,'VersionId':'actual-file'})):
            await denied(w.abandon_upload(private,pending['id'],a),409)
        from botocore.exceptions import ClientError
        missing = ClientError({'Error':{'Code':'404'}},'HeadObject')
        forbidden = ClientError({'Error':{'Code':'AccessDenied'}},'HeadObject')
        with patch.object(w,'s3_call',AsyncMock(side_effect=forbidden)):
            await denied(w.abandon_upload(private,pending['id'],a),502)
        collision = ClientError({'Error':{'Code':'PreconditionFailed'}},'PutObject')
        with patch.object(w,'s3_call',AsyncMock(side_effect=[missing,collision])):
            await denied(w.abandon_upload(private,pending['id'],a),409)
        assert (await w.get_storage_quota(a))['reserved_bytes']==7
        cleanup = AsyncMock(side_effect=[missing,{'VersionId':'empty-marker'}])
        os.environ['DOCUMENTS_BUCKET'] = 'replacement-private-bucket'
        with patch.object(w,'s3_call',cleanup):
            assert (await w.abandon_upload(private,pending['id'],a))['state']=='abandoned'
            assert cleanup.call_args.kwargs['IfNoneMatch']=='*'
            assert cleanup.call_args.kwargs['Body']==b''
            assert cleanup.call_args.kwargs['Bucket']=='test-private-bucket'
            await w.abandon_upload(private,pending['id'],a)
            assert cleanup.await_count==2
        os.environ['DOCUMENTS_BUCKET'] = 'test-private-bucket'
        assert (await w.get_storage_quota(a))['reserved_bytes']==3
        await denied(w.finish_upload(private,pending['id'],a),409)
        with patch.object(w,'s3_call',AsyncMock(return_value='https://example.test/put')):
            await w.start_upload(project,request,a)
        assert any(row['state']=='pending' for row in await w.versions(project,a))
        assert await w.versions(project,b)==[]
        await w.set_storage_quota(1,w.StorageQuota(quota_bytes=100),admin)
        with patch.object(w,'s3_call',AsyncMock(side_effect=RuntimeError('signing unavailable'))):
            try:
                await w.start_upload(private,request,a)
                raise AssertionError('Signing failure ignored')
            except RuntimeError:
                pass
        assert (await w.get_storage_quota(a))['reserved_bytes'] == 7
        # Audit failure rolls back the document mutation and quota reservation.
        before = len(await w.documents(user=a))
        with patch.object(w,'audit',AsyncMock(side_effect=RuntimeError('audit unavailable'))):
            try:
                await w.create_document(w.DocumentCreate(title='Must roll back'),a)
                raise AssertionError('Audit failure ignored')
            except RuntimeError:
                pass
        assert len(await w.documents(user=a)) == before
        logs = await (await db.execute('SELECT action,details FROM audit_log')).fetchall()
        actions = {row['action'] for row in logs}
        assert {'document_create','document_update','document_upload_reserve','document_upload_complete',
                'document_share_create','document_share_revoke','invitation_create','invitation_send_claim',
                'invitation_send_ambiguous','invitation_revoke','storage_quota_update'} <= actions
        assert all(invite['token'] not in row['details'] for row in logs)
        # Other members can read release summaries but cannot mutate owned work.
        from app.routers import releases
        await db.execute("INSERT INTO outreach_releases(id,name,created_by) VALUES (1,'Owned release',1)")
        await db.commit()
        await denied(releases.mint_person(1,1,releases.MintBody(full_name='Someone'),b),403)
        await denied(releases.keep_or_drop_person(1,1,releases.KeepBody(keep=True),b),403)
        await denied(releases.rebuild_pack(1,b),403)
        assert (await releases.list_releases(b))[0]['created_by'] == 1
        await db.close()


if __name__ == '__main__':
    asyncio.run(run())
    print('workspace boundaries: ok')
