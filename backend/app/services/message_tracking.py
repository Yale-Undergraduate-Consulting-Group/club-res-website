"""Correlate Gmail receipts with individual sends; retain evidence and sync health."""
from __future__ import annotations

import asyncio
import base64
import re
import secrets
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser

import httpx

from app.database import get_db
from app.services.gmail_api import get_valid_access_token
from app.services.gmail_reply_sync import _headers_dict, _parseaddr_email, _sent_at_to_ms

_locks: dict[int, asyncio.Lock] = {}


def sync_in_progress(user_id: int) -> bool:
    lock = _locks.get(user_id)
    return bool(lock and lock.locked())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def delivery_reports(raw: bytes) -> list[dict]:
    """Read RFC 3464 recipient blocks, not words in a subject line."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    original_id = None
    for part in message.walk():
        if part.get_content_type() == 'message/rfc822':
            payload = part.get_payload()
            if isinstance(payload, list) and payload:
                original_id = payload[0].get('Message-ID')
        elif part.get_content_type() == 'text/rfc822-headers':
            original = BytesParser(policy=policy.default).parsebytes(part.get_payload(decode=True) or b'')
            original_id = original.get('Message-ID') or original_id
    reports = []
    for part in message.walk():
        if part.get_content_type() != 'message/delivery-status':
            continue
        blocks = part.get_payload()
        if not isinstance(blocks, list):
            continue
        report_id = next((b.get('Original-Message-ID') for b in blocks if b.get('Original-Message-ID')), None)
        for block in blocks:
            recipient = block.get('Final-Recipient') or block.get('Original-Recipient')
            if not recipient:
                continue
            action = str(block.get('Action', '')).strip().lower()
            status = str(block.get('Status', '')).strip()
            if action not in ('failed', 'delayed'):
                continue
            reports.append({
                'recipient': recipient.split(';', 1)[-1].strip().lower(),
                'kind': 'bounced' if action == 'failed' else 'delayed',
                'status': status,
                'detail': str(block.get('Diagnostic-Code', status))[:1000],
                'original_id': report_id or original_id,
            })
    return reports


def classify_reply(message: dict, outgoing: list[dict]) -> tuple[dict, str] | None:
    headers = _headers_dict(message)
    sender = _parseaddr_email(headers.get('from', ''))
    timestamp = int(message.get('internalDate') or 0)
    references = set(re.findall(r'<[^>]+>', headers.get('in-reply-to', '') + ' ' + headers.get('references', '')))
    candidates = [m for m in outgoing if m['recipient'].lower() == sender
                  and _sent_at_to_ms(m['sent_at']) < timestamp
                  and (m['rfc_message_id'] in references or m['gmail_thread_id'] == message.get('threadId'))]
    if not candidates or 'SENT' in message.get('labelIds', []):
        return None
    # Prefer an explicit parent over an older reference or thread membership.
    parent = headers.get('in-reply-to', '').strip()
    candidates.sort(key=lambda m: (m['rfc_message_id'] == parent, _sent_at_to_ms(m['sent_at'])), reverse=True)
    automatic = headers.get('auto-submitted', '').lower() not in ('', 'no') or bool(headers.get('x-autoreply') or headers.get('x-autorespond'))
    return candidates[0], 'auto_reply' if automatic else 'replied'


async def record_event(db, outgoing: dict, kind: str, source: str, occurred: str, detail: str = '') -> bool:
    cursor = await db.execute(
        'INSERT OR IGNORE INTO outreach_events(message_id, kind, source_id, occurred_at, detail) VALUES (?, ?, ?, ?, ?)',
        (outgoing['id'], kind, source, occurred, detail),
    )
    if not cursor.rowcount:
        return False
    cc_id = outgoing['campaign_contact_id']
    if kind == 'replied':
        await db.execute("UPDATE campaign_contacts SET replied_at = COALESCE(replied_at, ?), status = 'replied' WHERE id = ?", (occurred, cc_id))
        await db.execute("""UPDATE contacts SET pipeline_status = 'replied' WHERE id =
            (SELECT contact_id FROM campaign_contacts WHERE id = ?) AND
            (pipeline_status IS NULL OR pipeline_status NOT IN ('meeting', 'closed'))""", (cc_id,))
        await _record_mailbox(db, outgoing, 'human_reply_observed', detail or 'Human reply observed')
    elif kind == 'auto_reply':
        # Automatic replies are not mailbox proof and do not change identity.
        pass
    elif kind == 'bounced':
        await db.execute("UPDATE campaign_contacts SET status = 'bounced' WHERE id = ? AND replied_at IS NULL", (cc_id,))
        status = (detail or '')[:80]
        if status.startswith('5.'):
            await _record_mailbox(db, outgoing, 'permanent_failure_observed', detail or 'Permanent delivery failure')
    elif kind == 'delayed':
        pass
    return True


async def _record_mailbox(db, outgoing: dict, state: str, reason: str) -> None:
    from app.services.contact_intelligence import record_mailbox_event
    row = await (await db.execute(
        """SELECT e.candidate_id, c.owner_id FROM catalog_evidence e
           JOIN campaign_contacts cc ON cc.contact_id=e.contact_id
           JOIN contacts c ON c.id=e.contact_id
           WHERE cc.id=?""", (outgoing['campaign_contact_id'],))).fetchone()
    if not row:
        return
    await record_mailbox_event(db, row['candidate_id'], outgoing['sender_id'], state, reason, outgoing['id'])


async def sync_sender(user_id: int, *, auto_sort_contacted: bool = True) -> dict:
    lock = _locks.setdefault(user_id, asyncio.Lock())
    if lock.locked():
        return {'ok': True, 'in_progress': True}
    async with lock:
        return await _sync_sender(user_id, auto_sort_contacted)


async def _sync_sender(user_id: int, auto_sort: bool) -> dict:
    started = now()
    db = await get_db()
    counts = {'marked_replied': 0, 'marked_bounced': 0, 'pipeline_promoted_contacted': 0}
    try:
        await db.execute('INSERT OR IGNORE INTO gmail_sync_state(user_id) VALUES (?)', (user_id,))
        state = await (await db.execute('SELECT * FROM gmail_sync_state WHERE user_id = ?', (user_id,))).fetchone()
        await db.execute('UPDATE gmail_sync_state SET last_attempt_at = ? WHERE user_id = ?', (started, user_id))
        await db.commit()
        token_result = await get_valid_access_token(user_id)
        if not token_result:
            raise ValueError('Reconnect Gmail to resume tracking.')
        token, owner = token_result
        # Existing campaign rows remain trackable after upgrading. Their original
        # pixel route is preserved, but earlier overwritten follow-up IDs cannot be reconstructed.
        legacy = await (await db.execute('''SELECT cc.*, c.email FROM campaign_contacts cc
            JOIN contacts c ON c.id = cc.contact_id WHERE cc.sent_by_user_id = ?
            AND cc.sent_at IS NOT NULL AND cc.gmail_thread_id IS NOT NULL
            AND NOT EXISTS(SELECT 1 FROM outreach_messages m WHERE m.campaign_contact_id = cc.id)''', (user_id,))).fetchall()
        for row in legacy:
            await db.execute('''INSERT INTO outreach_messages(campaign_contact_id, sender_id, recipient,
                tracking_token, rfc_message_id, gmail_message_id, gmail_thread_id, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (row['id'], user_id, row['email'], secrets.token_urlsafe(32), f'legacy:{row["id"]}', row['gmail_message_id'], row['gmail_thread_id'], row['sent_at']))
        await db.commit()
        outgoing = [dict(r) for r in await (await db.execute(
            'SELECT * FROM outreach_messages WHERE sender_id = ? AND sent_at IS NOT NULL', (user_id,))).fetchall()]
        if outgoing:
            since = min(_sent_at_to_ms(m['sent_at']) for m in outgoing) // 1000 - 60
            if state and state['last_success_at'] and not legacy:
                since = max(since, _sent_at_to_ms(state['last_success_at']) // 1000 - 300)
            async with httpx.AsyncClient(timeout=25, headers={'Authorization': f'Bearer {token}'}) as client:
                async def get(path, params=None):
                    response = await client.get('https://gmail.googleapis.com/gmail/v1/users/me/' + path, params=params)
                    if response.status_code in (401, 403):
                        raise ValueError('Reconnect Gmail: read access is unavailable.')
                    response.raise_for_status()
                    return response.json()

                for tracked in outgoing:
                    if tracked['rfc_message_id'].startswith('legacy:') and tracked['gmail_message_id']:
                        original = await get('messages/' + tracked['gmail_message_id'], {'format': 'metadata'})
                        original_id = _headers_dict(original).get('message-id')
                        if original_id:
                            tracked['rfc_message_id'] = original_id
                            await db.execute('UPDATE outreach_messages SET rfc_message_id = ? WHERE id = ?',
                                             (original_id, tracked['id']))
                await db.commit()
                page_token = None
                while True:
                    params = {'q': f'after:{since} -in:sent -in:drafts', 'maxResults': 100, 'includeSpamTrash': 'true'}
                    if page_token:
                        params['pageToken'] = page_token
                    page = await get('messages', params)
                    for item in page.get('messages', []):
                        message = await get('messages/' + item['id'], {'format': 'metadata'})
                        headers = _headers_dict(message)
                        occurred = datetime.fromtimestamp(int(message.get('internalDate') or 0) / 1000, timezone.utc).isoformat()
                        content_type = headers.get('content-type', '').lower()
                        suspected_dsn = bool(re.search(r'report-type\s*=\s*"?delivery-status', content_type)) or any(
                            mark in headers.get('from', '').lower() for mark in ('mailer-daemon', 'postmaster', 'mail-daemon'))
                        if suspected_dsn:
                            raw_data = await get('messages/' + item['id'], {'format': 'raw'})
                            encoded = raw_data.get('raw', '')
                            raw = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
                            for report in delivery_reports(raw):
                                matches = [m for m in outgoing if m['recipient'].lower() == report['recipient']
                                    and _sent_at_to_ms(m['sent_at']) < int(message.get('internalDate') or 0)
                                    and (m['rfc_message_id'] == report['original_id'] if report['original_id']
                                         else m['gmail_thread_id'] == message.get('threadId'))]
                                # Never guess which send failed from the recipient alone.
                                if len(matches) == 1:
                                    added = await record_event(db, matches[0], report['kind'], item['id'], occurred,
                                                               report['status'] + ' ' + report['detail'])
                                    if added and report['kind'] == 'bounced':
                                        counts['marked_bounced'] += 1
                        else:
                            reply = classify_reply(message, outgoing)
                            if reply and _parseaddr_email(headers.get('from', '')) != owner.lower():
                                match, kind = reply
                                added = await record_event(db, match, kind, item['id'], occurred)
                                if added and kind == 'replied':
                                    counts['marked_replied'] += 1
                        await db.commit()
                    page_token = page.get('nextPageToken')
                    if not page_token:
                        break
        if auto_sort:
            from app.services.gmail_reply_sync import apply_contacted_auto_sort
            counts['pipeline_promoted_contacted'] = await apply_contacted_auto_sort(db, user_id)
        await db.execute('UPDATE gmail_sync_state SET last_success_at = ?, error = NULL WHERE user_id = ?', (started, user_id))
        await db.commit()
        return {'ok': True, **counts, 'last_success_at': started, 'errors': []}
    except Exception as exc:
        await db.rollback()
        error = str(exc)[:500]
        await db.execute('UPDATE gmail_sync_state SET error = ? WHERE user_id = ?', (error, user_id))
        await db.commit()
        return {'ok': False, **counts, 'error': error, 'errors': [{'error': error}]}
    finally:
        await db.close()
