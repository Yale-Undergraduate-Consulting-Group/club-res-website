"""Read-only club outreach facts; never an execution identity source."""
from fastapi import APIRouter,Depends,Query
from app.auth_deps import get_current_user
from app.database import get_db
router=APIRouter()

@router.get('/outreach')
async def outreach_activity(days: int=Query(30,ge=1,le=365),limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0),user: dict=Depends(get_current_user)):
    from datetime import datetime,timedelta,timezone
    since=(datetime.now(timezone.utc)-timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    db=await get_db()
    try:
        base='''FROM outreach_messages m JOIN users u ON u.id=m.sender_id
            LEFT JOIN campaign_contacts cc ON cc.id=m.campaign_contact_id
            LEFT JOIN campaigns c ON c.id=cc.campaign_id WHERE m.sent_at IS NOT NULL AND datetime(m.sent_at)>=datetime(?)'''
        rows=await (await db.execute('''SELECT m.id,m.sender_id,u.name AS sender_name,u.email AS sender_email,
            m.recipient,m.sent_at,c.name AS campaign_name,
            CASE WHEN EXISTS(SELECT 1 FROM outreach_messages first WHERE first.campaign_contact_id=m.campaign_contact_id
            AND first.sent_at IS NOT NULL AND first.id<m.id) THEN 'follow_up' ELSE 'initial' END AS message_kind,
            EXISTS(SELECT 1 FROM outreach_events e WHERE e.message_id=m.id AND e.kind='reply') AS replied,
            EXISTS(SELECT 1 FROM outreach_events e WHERE e.message_id=m.id AND e.kind='open') AS opened
            '''+base+' ORDER BY m.sent_at DESC,m.id DESC LIMIT ? OFFSET ?',(since,limit,offset))).fetchall()
        totals=await (await db.execute('''SELECT m.sender_id,u.name AS sender_name,u.email AS sender_email,
            COUNT(*) AS messages,COUNT(DISTINCT lower(m.recipient)) AS unique_recipients,
            SUM(CASE WHEN EXISTS(SELECT 1 FROM outreach_messages first WHERE first.campaign_contact_id=m.campaign_contact_id
            AND first.sent_at IS NOT NULL AND first.id<m.id) THEN 1 ELSE 0 END) AS follow_ups
            '''+base+' GROUP BY m.sender_id,u.name,u.email ORDER BY messages DESC',(since,))).fetchall()
        return {'days':days,'items':[dict(row) for row in rows],'by_sender':[dict(row) for row in totals]}
    finally: await db.close()
