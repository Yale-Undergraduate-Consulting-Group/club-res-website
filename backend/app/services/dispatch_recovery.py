"""Reconcile uncertain sends against exact evidence in the original sender's Gmail.

Absence from search is never treated as evidence of non-delivery and never enables
a retry. No endpoint in this module sends mail.
"""
from datetime import datetime, timezone
from email.utils import getaddresses
import httpx
from fastapi import HTTPException
from app.database import get_db
from app.services.dispatch_service import begin_write
from app.services.transaction_audit import audit


async def recover_dispatch(campaign_id: int, dispatch_key: str, user_id: int):
    from app.routers.campaigns import require_campaign_owner
    from app.services.gmail_api import get_valid_access_token
    db = await get_db()
    try:
        await require_campaign_owner(db,campaign_id,user_id)
        row = await (await db.execute(
            """SELECT d.*,m.id AS tracking_id,m.rfc_message_id FROM outreach_dispatches d
            JOIN campaign_contacts cc ON cc.id=d.campaign_contact_id
            LEFT JOIN outreach_messages m ON m.dispatch_key=d.dispatch_key
            WHERE d.dispatch_key=? AND d.sender_user_id=? AND cc.campaign_id=?""",
            (dispatch_key,user_id,campaign_id),
        )).fetchone()
        if not row:
            raise HTTPException(404,'Dispatch not found')
        intent = dict(row)
        if intent['state']=='sent':
            return {'ok':True,'state':'sent','reconciled':False}
        if intent['state'] not in ('claimed','ambiguous'):
            raise HTTPException(409,'Only uncertain dispatches require reconciliation')
        if not intent['rfc_message_id']:
            return {'ok':True,'state':intent['state'],'reconciled':False,'reason':'No durable RFC Message-ID evidence; manual investigation required'}
    finally:
        await db.close()
    credentials = await get_valid_access_token(user_id)
    if not credentials:
        raise HTTPException(409,'Reconnect the original sender Gmail account to inspect Sent mail')
    token, sender_email = credentials
    headers = {'Authorization':f'Bearer {token}'}
    base = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
    async with httpx.AsyncClient(timeout=20) as client:
        result = await client.get(base,headers=headers,params={'q':f"in:sent rfc822msgid:{intent['rfc_message_id']}",'labelIds':'SENT','maxResults':2})
        result.raise_for_status()
        candidates = result.json().get('messages',[])
        if not candidates:
            return {'ok':True,'state':intent['state'],'reconciled':False,'reason':'No matching Sent message; delivery remains uncertain'}
        if len(candidates)!=1 or result.json().get('nextPageToken'):
            raise HTTPException(409,'Multiple Sent messages match; manual investigation required')
        result = await client.get(f"{base}/{candidates[0]['id']}",headers=headers,params={'format':'metadata'})
        result.raise_for_status()
        message = result.json()
    metadata = {h['name'].lower():h['value'] for h in message.get('payload',{}).get('headers',[])}
    to = [addr.lower() for _,addr in getaddresses([metadata.get('to','')])]
    from_addresses = [addr.lower() for _,addr in getaddresses([metadata.get('from','')])]
    if ('SENT' not in message.get('labelIds',[]) or metadata.get('message-id','').strip()!=intent['rfc_message_id']
            or to != [intent['recipient'].lower()] or from_addresses != [sender_email.lower()]
            or metadata.get('cc') or metadata.get('bcc')):
        raise HTTPException(409,'Sent message identity or recipient does not match the immutable dispatch')
    try:
        sent_at = datetime.fromtimestamp(int(message['internalDate'])/1000,timezone.utc).isoformat()
    except (KeyError,ValueError,TypeError,OverflowError):
        raise HTTPException(409,'Sent message lacks a valid timestamp')
    db = await get_db()
    try:
        await begin_write(db)
        await require_campaign_owner(db,campaign_id,user_id)
        current = await (await db.execute('SELECT state FROM outreach_dispatches WHERE dispatch_key=?',(dispatch_key,))).fetchone()
        if current['state']=='sent':
            await db.commit()
            return {'ok':True,'state':'sent','reconciled':False}
        if current['state'] not in ('claimed','ambiguous'):
            raise HTTPException(409,'Dispatch state changed; reload before reconciling')
        await db.execute("UPDATE outreach_dispatches SET state='sent',completed_at=CURRENT_TIMESTAMP,gmail_message_id=?,last_error=NULL WHERE dispatch_key=?",(message['id'],dispatch_key))
        await db.execute('UPDATE outreach_messages SET gmail_message_id=?,gmail_thread_id=?,sent_at=? WHERE id=?',(message['id'],message.get('threadId'),sent_at,intent['tracking_id']))
        cc_id = intent['campaign_contact_id']
        if dispatch_key == f'initial:{cc_id}':
            await db.execute("""UPDATE campaign_contacts SET status=CASE WHEN status IN ('pending','sending','failed') THEN 'sent' ELSE status END,
                sent_at=COALESCE(sent_at,?),last_sequence_sent_at=COALESCE(last_sequence_sent_at,?),
                sent_by_user_id=?,gmail_message_id=?,gmail_thread_id=?,last_error=NULL WHERE id=?""",
                (sent_at,sent_at,user_id,message['id'],message.get('threadId'),cc_id))
        elif dispatch_key.startswith(f'followup:{cc_id}:'):
            step = int(dispatch_key.rsplit(':',1)[1])+1
            await db.execute('''UPDATE campaign_contacts SET sequence_step_sent=?,last_sequence_sent_at=?,
                gmail_message_id=?,gmail_thread_id=? WHERE id=? AND sequence_step_sent<?''',
                (step,sent_at,message['id'],message.get('threadId'),cc_id,step))
        await db.execute("""UPDATE campaigns SET status='sent' WHERE id=? AND status IN ('releasing','needs_attention')
            AND NOT EXISTS (SELECT 1 FROM campaign_contacts WHERE campaign_id=? AND status IN ('pending','sending','failed'))""",(campaign_id,campaign_id))
        await audit(db,user_id,'dispatch_reconciled','campaign',campaign_id,{'dispatch_key':dispatch_key,'gmail_message_id':message['id']})
        await db.commit()
        return {'ok':True,'state':'sent','reconciled':True}
    finally:
        await db.close()
