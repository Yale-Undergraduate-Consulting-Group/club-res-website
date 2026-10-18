"""Shared reporting counts confirmed messages without changing sender attribution."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET']='activity-ledger-test-secret-xxxx'
from app.database import init_db,get_db
from app.routers.activity import outreach_activity

async def run():
    with tempfile.TemporaryDirectory() as directory:
        os.environ['DATABASE_URL']='sqlite:///'+directory+'/test.db'
        await init_db();db=await get_db()
        try:
            await db.execute("INSERT INTO users(id,email) VALUES (1,'a@yale.edu'),(2,'b@yale.edu')")
            await db.execute("INSERT INTO campaigns(id,name) VALUES (1,'A campaign'),(2,'B campaign')")
            await db.execute("INSERT INTO contacts(id,name,email) VALUES (1,'Contact','changed@example.org')")
            await db.execute('INSERT INTO campaign_contacts(id,campaign_id,contact_id) VALUES (1,1,1),(2,2,1)')
            for mid,sender,cc,sent in [(1,1,1,True),(2,1,1,True),(3,2,2,True),(4,2,2,False)]:
                await db.execute("INSERT INTO outreach_messages(id,campaign_contact_id,sender_id,recipient,tracking_token,rfc_message_id,sent_at) VALUES (?,?,?,?,?,?,CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)",
                    (mid,cc,sender,'original@example.org',str(mid),f'<{mid}@test>',sent))
            for source in ['one','two']:
                await db.execute("INSERT INTO outreach_events(message_id,kind,source_id,occurred_at) VALUES (1,'open',?,CURRENT_TIMESTAMP)",(source,))
            await db.commit()
        finally: await db.close()
        result=await outreach_activity(days=30,limit=50,offset=0,user={'id':2})
        assert len(result['items'])==3
        assert all(row['recipient']=='original@example.org' for row in result['items'])
        totals={row['sender_id']:row for row in result['by_sender']}
        assert totals[1]['messages']==2 and totals[1]['unique_recipients']==1 and totals[1]['follow_ups']==1
        assert totals[2]['messages']==1 and totals[2]['follow_ups']==0
        assert len((await outreach_activity(days=30,limit=1,offset=1,user={'id':1}))['items'])==1
        print('activity attribution: ok')

if __name__=='__main__':asyncio.run(run())
