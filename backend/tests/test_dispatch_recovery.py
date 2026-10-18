"""Recovery refuses inconclusive evidence and rechecks authorization after Gmail I/O."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET']='dispatch-recovery-test-secret-xxxxxxxx'
from fastapi import HTTPException
from app.database import get_db, init_db
from app.services.dispatch_recovery import recover_dispatch


async def denied(coro,status):
    try:
        await coro
        raise AssertionError('Expected refusal')
    except HTTPException as exc:
        assert exc.status_code==status, exc


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL']='sqlite:///'+tmp+'/recovery.db'
        await init_db()
        db=await get_db()
        await db.execute("INSERT INTO users(id,email) VALUES (1,'one@yale.edu')")
        await db.execute("INSERT INTO contacts(id,email) VALUES (1,'recipient@example.org')")
        await db.execute("INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id) VALUES (1,'Test','needs_attention',1,1)")
        await db.execute("INSERT INTO campaign_contacts(id,campaign_id,contact_id,status,sent_by_user_id) VALUES (1,1,1,'failed',1)")
        await db.execute("""INSERT INTO outreach_dispatches(dispatch_key,campaign_contact_id,sender_user_id,recipient,subject,body,state)
            VALUES ('initial:1',1,1,'recipient@example.org','Subject','Body','ready')""")
        await db.commit()
        await denied(recover_dispatch(1,'missing',1),404)
        await denied(recover_dispatch(1,'initial:1',1),409)
        await db.execute("UPDATE outreach_dispatches SET state='ambiguous'")
        await db.commit()
        assert 'No durable RFC' in (await recover_dispatch(1,'initial:1',1))['reason']
        await db.execute("""INSERT INTO outreach_messages(campaign_contact_id,sender_id,recipient,tracking_token,rfc_message_id,dispatch_key)
            VALUES (1,1,'recipient@example.org','token','<recovery@yale.edu>','initial:1')""")
        await db.commit()
        with patch('app.services.gmail_api.get_valid_access_token',AsyncMock(return_value=None)):
            await denied(recover_dispatch(1,'initial:1',1),409)

        mode='duplicate'
        async def gmail(request):
            if request.url.path.endswith('/messages'):
                if mode=='duplicate':
                    return httpx.Response(200,json={'messages':[{'id':'a'},{'id':'b'}]})
                if mode=='paginated':
                    return httpx.Response(200,json={'messages':[{'id':'a'}],'nextPageToken':'more'})
                return httpx.Response(200,json={'messages':[{'id':'a'}]})
            headers=[{'name':'Message-ID','value':'<recovery@yale.edu>'},
                     {'name':'To','value':'recipient@example.org'},
                     {'name':'From','value':'one@yale.edu'}]
            labels=['SENT']
            if mode=='wrong_from':
                headers[-1]['value']='another@yale.edu'
            elif mode=='cc':
                headers.append({'name':'Cc','value':'extra@example.org'})
            elif mode=='not_sent':
                labels=[]
            if mode in ('concurrent_sent','concurrent_ready'):
                await db.execute('UPDATE outreach_dispatches SET state=?',('sent' if mode=='concurrent_sent' else 'ready',))
                await db.commit()
            if mode=='disabled':
                await db.execute('UPDATE users SET is_active=0 WHERE id=1')
                await db.commit()
            return httpx.Response(200,json={'id':'a','threadId':'t','labelIds':labels,'payload':{'headers':headers},
                'internalDate':'invalid' if mode=='bad_time' else '1750000000000'})
        real_client=httpx.AsyncClient
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(gmail),**kwargs)
        with patch('app.services.gmail_api.get_valid_access_token',AsyncMock(return_value=('fake','one@yale.edu'))),patch('app.services.dispatch_recovery.httpx.AsyncClient',client):
            for mode in ('duplicate','paginated','bad_time','wrong_from','cc','not_sent','concurrent_ready'):
                await denied(recover_dispatch(1,'initial:1',1),409)
                await db.execute("UPDATE outreach_dispatches SET state='ambiguous'")
                await db.commit()
            mode='concurrent_sent'
            assert not (await recover_dispatch(1,'initial:1',1))['reconciled']
            await db.execute("UPDATE outreach_dispatches SET state='ambiguous'")
            await db.commit()
            mode='disabled'
            await denied(recover_dispatch(1,'initial:1',1),403)
            await db.execute('UPDATE users SET is_active=1 WHERE id=1')
            await db.commit()
            mode='match'
            with patch('app.services.dispatch_recovery.audit',AsyncMock(side_effect=RuntimeError('audit unavailable'))):
                try:
                    await recover_dispatch(1,'initial:1',1)
                    raise AssertionError('Audit failure ignored')
                except RuntimeError:
                    pass
            state=await (await db.execute('SELECT state FROM outreach_dispatches')).fetchone()
            assert state['state']=='ambiguous', 'Recovery must roll back on audit failure'
            # Follow-up reconciliation advances only that sequence step, not unrelated history.
            await db.execute("UPDATE outreach_dispatches SET dispatch_key='followup:1:0'")
            await db.execute("UPDATE outreach_messages SET dispatch_key='followup:1:0'")
            await db.execute("UPDATE campaign_contacts SET status='replied',replied_at='2026-01-01' WHERE id=1")
            await db.commit()
            assert (await recover_dispatch(1,'followup:1:0',1))['reconciled']
            cc=await (await db.execute('SELECT status,sequence_step_sent FROM campaign_contacts WHERE id=1')).fetchone()
            assert cc['status']=='replied' and cc['sequence_step_sent']==1
        await db.close()


if __name__=='__main__':
    asyncio.run(run())
    print('dispatch recovery: ok')
