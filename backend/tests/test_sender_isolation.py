"""Two-account campaign isolation; no real mail or network calls."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET'] = 'sender-isolation-regression-secret-xxxx'
os.environ['CAMPAIGN_SEND_DELAY_SEC'] = '0'
from fastapi import HTTPException
from app.database import init_db, get_db
from app.models import CampaignCreate
from app.routers.campaigns import (create_campaign, drain_campaign, release_campaign,
    pause_campaign, retry_failed_campaign_contacts, delete_campaign,
    update_campaign_contact_email)
from app.services.gmail_api import get_valid_access_token

async def run():
    with tempfile.TemporaryDirectory() as directory:
        os.environ['DATABASE_URL'] = 'sqlite:///' + directory + '/test.db'
        await init_db()
        db = await get_db()
        try:
            await db.execute("INSERT INTO users(id,email) VALUES (1,'a@yale.edu'),(2,'b@yale.edu')")
            await db.execute("INSERT INTO contacts(id,name,email) VALUES (1,'Recipient','person@example.org')")
            await db.commit()
        finally:
            await db.close()
        a, b = {'id': 1}, {'id': 2}
        from app.routers.settings import get_settings
        from app.services.settings_service import set_member_setting, get_member_setting, set_setting
        await set_member_setting(1, 'sign_off_role', 'A role')
        await set_member_setting(2, 'sign_off_role', 'B role')
        assert await get_member_setting(1, 'sign_off_role') == 'A role'
        assert await get_member_setting(2, 'sign_off_role') == 'B role'
        # The settings endpoint returns only the reader's own values, never
        # another member's and never a stored club secret.
        assert (await get_settings(a))['sign_off_role'] == 'A role'
        await set_setting('gmail_app_password', 'legacy-secret')
        await set_setting('document_quota_bytes:2', '100')
        visible = await get_settings(b)
        assert visible['sign_off_role'] == 'B role'
        assert not any(key.startswith(('member:', 'gmail_', 'document_quota_bytes')) for key in visible)
        campaign = await create_campaign(CampaignCreate(name='A outreach'), a)
        cid = campaign['id']
        assert campaign['owner_user_id'] == campaign['sender_user_id'] == 1
        db = await get_db()
        try:
            inserted = await db.execute("INSERT INTO campaign_contacts(campaign_id,contact_id,email_subject,email_body,status) VALUES (?,1,'Hi','Body','pending')", (cid,))
            cc_id = int(inserted.lastrowid)
            await db.commit()
        finally:
            await db.close()
        # Exercise router-level authentication, not only direct helper calls.
        import httpx
        from fastapi import FastAPI, Depends
        from app.auth_deps import get_current_user
        from app.jwt_utils import create_token
        from app.routers.campaigns import router
        from app.routers.outreach import router as outreach_router
        app = FastAPI()
        app.include_router(router, prefix='/api/campaigns', dependencies=[Depends(get_current_user)])
        app.include_router(outreach_router, prefix='/api/outreach', dependencies=[Depends(get_current_user)])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.post(f'/api/campaigns/{cid}/send')).status_code == 401
            token = create_token(2, 'b@yale.edu')
            for path in ['send', 'release', 'pause', 'retry-failed']:
                response = await client.post(f'/api/campaigns/{cid}/{path}', headers={'Authorization': 'Bearer ' + token})
                assert response.status_code == 403, response.text
            response = await client.post(f'/api/outreach/campaign-contacts/{cc_id}/mark-replied', headers={'Authorization': 'Bearer ' + token})
            assert response.status_code == 403, response.text
        # Unknown historical ownership is never claimed by the next viewer.
        db = await get_db()
        try:
            legacy = await db.execute("INSERT INTO campaigns(name,status) VALUES ('legacy','draft')")
            legacy_id = legacy.lastrowid
            await db.commit()
        finally:
            await db.close()
        try:
            await release_campaign(legacy_id,a)
            raise AssertionError('Unowned legacy campaign was claimed')
        except HTTPException as exc:
            assert exc.status_code == 403
        send = AsyncMock(return_value={'message_id': 'message-a', 'thread_id': 'thread-a'})
        with patch('app.services.gmail_api.send_via_gmail_api_with_tracking', send):
            for operation in [lambda: release_campaign(cid,b), lambda: drain_campaign(cid,2),
                              lambda: pause_campaign(cid,b), lambda: retry_failed_campaign_contacts(cid,b),
                              lambda: delete_campaign(cid,b),
                              lambda: update_campaign_contact_email(cid,1,body='tampered',user=b)]:
                try:
                    await operation()
                    raise AssertionError('Cross-account mutation allowed')
                except HTTPException as exc:
                    assert exc.status_code == 403
            send.assert_not_awaited()
            await release_campaign(cid,a)
            result = await drain_campaign(cid,1)
            assert result['sent'] == 1
            assert send.await_args.kwargs['user_id'] == 1
            assert send.await_args.kwargs['sign_off'].role == 'A role'
        # Shared sequence content must never select its author's mailbox.
        from app.services.follow_up_job import run_follow_up_sequences
        db = await get_db()
        try:
            seq = await db.execute("INSERT INTO follow_up_sequences(name,user_id) VALUES ('B template',2)")
            await db.execute("INSERT INTO follow_up_steps(sequence_id,step_order,days_after,subject,body) VALUES (?,1,0,'Follow up','Body')", (seq.lastrowid,))
            await db.execute("UPDATE campaigns SET sequence_id=? WHERE id=?", (seq.lastrowid,cid))
            await db.execute("UPDATE campaign_contacts SET sequence_step_sent=0,last_sequence_sent_at='2020-01-01',sent_by_user_id=1 WHERE campaign_id=?", (cid,))
            from app.services.dispatch_service import snapshot
            cc = await (await db.execute("SELECT id FROM campaign_contacts WHERE campaign_id=?", (cid,))).fetchone()
            await snapshot(db, f"followup:{cc['id']}:0", cc['id'], 1, 'person@example.org', 'Follow up', 'Body')
            await db.commit()
        finally:
            await db.close()
        send.reset_mock()
        with patch('app.services.gmail_api.send_via_gmail_api_with_tracking',send):
            result = await run_follow_up_sequences()
            assert result['sent'] == 1, result
            assert send.await_args.kwargs['user_id'] == 1
            assert send.await_args.kwargs['sign_off'].role == 'A role'
        db = await get_db()
        try:
            await db.execute("UPDATE campaigns SET status='releasing' WHERE id=?", (cid,))
            await db.execute("UPDATE campaign_contacts SET status='sending' WHERE campaign_id=?", (cid,))
            await db.commit()
        finally:
            await db.close()
        await pause_campaign(cid,a)
        db = await get_db()
        try:
            row = await (await db.execute('SELECT status FROM campaign_contacts WHERE campaign_id=?',(cid,))).fetchone()
            assert row['status'] == 'sending', 'Pause recycled in-flight work'
            await db.execute('UPDATE users SET is_active=0 WHERE id=1')
            await db.commit()
        finally:
            await db.close()
        assert await get_valid_access_token(1) is None
        print('sender isolation: ok')

if __name__ == '__main__':
    asyncio.run(run())
