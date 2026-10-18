"""Durable process-level claims, immutable releases, uncertain sends and ownership."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["JWT_SECRET"] = "dispatch-safety-test-secret-xxxxxxxx"
os.environ["CAMPAIGN_SEND_DELAY_SEC"] = "0"
from fastapi import HTTPException
from app.database import init_db, get_db
from app.routers.campaigns import (
    release_campaign, drain_campaign, retry_failed_campaign_contacts,
    update_campaign_contact_email, reconcile_owner, OwnershipConfirmation,
)


async def denied(coro, status):
    try:
        await coro
        raise AssertionError("Expected rejection")
    except HTTPException as exc:
        assert exc.status_code == status, exc


async def run():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = "sqlite:///" + tmp + "/dispatch.db"
        await init_db()
        db = await get_db()
        await db.execute("INSERT INTO users(id,email,role) VALUES (1,'one@yale.edu','standard'),(2,'two@yale.edu','standard'),(3,'admin@yale.edu','admin')")
        await db.execute("INSERT INTO contacts(id,email) VALUES (1,'original@example.org')")
        await db.execute("INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id) VALUES (1,'Test','draft',1,1)")
        await db.execute("INSERT INTO campaign_contacts(id,campaign_id,contact_id,status,email_subject,email_body) VALUES (1,1,1,'pending','Original','Original body')")
        await db.commit()
        await db.execute("UPDATE contacts SET email='one@example.org,two@example.org' WHERE id=1")
        await db.commit()
        await denied(release_campaign(1,{'id':1}),409)
        await db.execute("UPDATE contacts SET email='original@example.org' WHERE id=1")
        await db.commit()
        await release_campaign(1, {"id": 1})
        from app.routers.contacts import delete_contact, bulk_delete_contacts, BulkDeleteContactsRequest
        await denied(delete_contact(1,{'id':3,'role':'admin'}),409)
        await denied(bulk_delete_contacts(BulkDeleteContactsRequest(contact_ids=[1]),{'id':3,'role':'admin'}),409)
        await denied(update_campaign_contact_email(1, 1, subject="Changed", user={"id": 1}), 409)
        await db.execute("UPDATE contacts SET email='changed@example.org' WHERE id=1")
        await db.commit()
        fake = AsyncMock(side_effect=TimeoutError("Acceptance unknown"))
        with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", fake):
            await drain_campaign(1, 1)
            assert fake.call_args.kwargs["to_email"] == "original@example.org"
            assert fake.call_args.kwargs["body"] == "Original body"
            assert (await retry_failed_campaign_contacts(1, {"id": 1}))["retried"] == 0
            assert fake.await_count == 1
        row = await (await db.execute("SELECT state FROM outreach_dispatches WHERE dispatch_key='initial:1'")).fetchone()
        assert row["state"] == "ambiguous"
        await db.execute("""INSERT INTO outreach_messages(campaign_contact_id,sender_id,recipient,tracking_token,rfc_message_id,dispatch_key)
            VALUES (1,1,'original@example.org','recovery-token','<recover@yale.edu>','initial:1')""")
        await db.commit()
        from app.services.dispatch_recovery import recover_dispatch
        await denied(recover_dispatch(1,'initial:1',2),403)
        import httpx
        real_client = httpx.AsyncClient
        mode = 'missing'
        def gmail(request):
            if request.url.path.endswith('/messages'):
                assert request.url.params['q'] == 'in:sent rfc822msgid:<recover@yale.edu>'
                return httpx.Response(200,json={'messages':[] if mode=='missing' else [{'id':'recovered'}]})
            return httpx.Response(200,json={'id':'recovered','threadId':'thread','labelIds':['SENT'],
                'internalDate':'1750000000000','payload':{'headers':[
                    {'name':'Message-ID','value':'<recover@yale.edu>'},
                    {'name':'From','value':'one@yale.edu'},
                    {'name':'To','value':'wrong@example.org' if mode=='wrong' else 'original@example.org'},
                ]}})
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(gmail),**kwargs)
        with patch('app.services.gmail_api.get_valid_access_token',AsyncMock(return_value=('fake','one@yale.edu'))), patch('app.services.dispatch_recovery.httpx.AsyncClient',client):
            assert not (await recover_dispatch(1,'initial:1',1))['reconciled']
            mode = 'wrong'
            await denied(recover_dispatch(1,'initial:1',1),409)
            mode = 'match'
            assert (await recover_dispatch(1,'initial:1',1))['reconciled']
            assert not (await recover_dispatch(1,'initial:1',1))['reconciled']
        recovered = await (await db.execute('SELECT status,gmail_message_id FROM campaign_contacts WHERE id=1')).fetchone()
        assert recovered['status']=='sent' and recovered['gmail_message_id']=='recovered'

        # Independent interpreter processes contend for a single durable key.
        from app.services.dispatch_service import snapshot
        await snapshot(db, "race", 1, 1, "person@example.org", "Subject", "Body")
        await db.commit()
        worker = """
import asyncio
from app.database import get_db
from app.services.dispatch_service import begin_write, claim
async def run():
 d=await get_db()
 await begin_write(d)
 r=await claim(d,'race',1)
 await d.commit()
 await d.close()
 print('claimed' if r else 'blocked')
asyncio.run(run())
"""
        async def contender():
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", worker,
                cwd=str(Path(__file__).resolve().parents[1]),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            assert process.returncode == 0, stderr.decode()
            return stdout.decode().strip()
        outcomes = await asyncio.gather(*(contender() for _ in range(4)))
        assert outcomes.count("claimed") == 1, outcomes
        assert await contender() == "blocked", "Restart must not recycle uncertain claims"
        from app.services.gmail_api import send_via_gmail_api_with_tracking
        with patch('app.services.gmail_api.get_valid_access_token',AsyncMock(return_value=('fake','two@yale.edu'))):
            try:
                await send_via_gmail_api_with_tracking(2,'person@example.org','Subject','Body',1,dispatch_key='race')
                raise AssertionError('Cross-account dispatch reached Gmail')
            except ValueError as exc:
                assert 'immutable dispatch' in str(exc)

        def fail_send(request):
            raise httpx.ReadTimeout('Acceptance unknown',request=request)
        def failing_client(**kwargs):
            return real_client(transport=httpx.MockTransport(fail_send),**kwargs)
        with patch('app.services.gmail_api.get_valid_access_token',AsyncMock(return_value=('fake','one@yale.edu'))), patch('app.services.gmail_api.httpx.AsyncClient',failing_client):
            try:
                await send_via_gmail_api_with_tracking(1,'person@example.org','Subject','Body',1,dispatch_key='race')
                raise AssertionError('Expected transport failure')
            except httpx.ReadTimeout:
                pass
        linked = await (await db.execute("SELECT rfc_message_id FROM outreach_messages WHERE dispatch_key='race'")).fetchone()
        assert linked and linked['rfc_message_id'], 'Recovery identity must be committed before Gmail I/O'

        await db.execute("INSERT INTO campaigns(id,name,status) VALUES (2,'Legacy','draft')")
        await db.commit()
        confirmation = OwnershipConfirmation(sender_user_id=1, confirmed=True, reason="Verified historical campaign owner")
        await denied(reconcile_owner(2, confirmation, {"id": 2}), 403)
        await denied(reconcile_owner(2, OwnershipConfirmation(sender_user_id=1, reason="Verified history"), {"id": 3}), 422)
        await reconcile_owner(2, confirmation, {"id": 3})
        await denied(reconcile_owner(2, confirmation, {"id": 3}), 409)
        audit = await (await db.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action='campaign_owner_reconcile'")).fetchone()
        assert audit["n"] == 1

        # Shared sequence creator never determines sender; simultaneous schedulers
        # cannot both send the same follow-up, and contact edits cannot retarget it.
        await db.execute("INSERT INTO follow_up_sequences(id,name,user_id) VALUES (1,'Shared',2)")
        await db.execute("INSERT INTO follow_up_steps(sequence_id,subject,body,days_after) VALUES (1,'Follow-up','Follow-up body',0)")
        await db.execute("INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id,sequence_id) VALUES (3,'Followups','sent',1,1,1)")
        await db.execute("INSERT INTO campaign_contacts(id,campaign_id,contact_id,status,sent_by_user_id,last_sequence_sent_at) VALUES (3,3,1,'sent',1,'2025-01-01')")
        await snapshot(db, "initial:3", 3, 1, "original@example.org", "Old", "Old")
        await db.execute("UPDATE outreach_dispatches SET state='sent' WHERE dispatch_key='initial:3'")
        await snapshot(db, "followup:3:0", 3, 1, "original@example.org", "Frozen followup", "Frozen body")
        await db.commit()
        from app.services.follow_up_job import run_follow_up_sequences
        followup = AsyncMock(return_value={"message_id": "followup-id"})
        with patch("app.services.gmail_api.send_via_gmail_api_with_tracking", followup):
            await asyncio.gather(run_follow_up_sequences(), run_follow_up_sequences())
            assert followup.await_count == 1
            assert followup.call_args.kwargs["body"] == "Frozen body"
            assert followup.call_args.kwargs["user_id"] == 1
            assert followup.call_args.kwargs["to_email"] == "original@example.org"
        # Resuming a paused release cannot append steps added to a shared template.
        await db.execute("INSERT INTO campaigns(id,name,status,owner_user_id,sender_user_id,sequence_id) VALUES (4,'Frozen sequence','draft',1,1,1)")
        await db.execute("INSERT INTO campaign_contacts(id,campaign_id,contact_id,status,email_subject,email_body) VALUES (4,4,1,'pending','Initial','Body')")
        await db.commit()
        await release_campaign(4,{'id':1})
        from app.routers.campaigns import pause_campaign
        await pause_campaign(4,{'id':1})
        await db.execute("INSERT INTO follow_up_steps(sequence_id,subject,body,days_after,step_order) VALUES (1,'New unauthorized followup','New body',2,2)")
        await db.commit()
        await release_campaign(4,{'id':1})
        steps=await (await db.execute("SELECT COUNT(*) AS n FROM outreach_dispatches WHERE dispatch_key LIKE 'followup:4:%'")).fetchone()
        assert steps['n']==1, 'Resume appended an unapproved shared-template step'
        await db.close()


if __name__ == "__main__":
    asyncio.run(run())
    print("dispatch safety: ok")
