"""Tracking regressions; all Gmail calls use a fake transport, no mail is sent."""
import asyncio
import base64
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'tracking-tests-secret-not-a-default-xx')
from app.database import init_db, get_db
from app.routers.track import get_tracking_pixel_url, track_message_open
from app.services.message_tracking import delivery_reports, classify_reply, record_event
from app.services.gmail_reply_sync import _thread_has_inbound_reply_from_contact


def message(sender, ms, mid='reply', thread='thread', extra=None):
    return {'id': mid, 'threadId': thread, 'internalDate': str(ms), 'payload': {'headers': [
        {'name': k, 'value': v} for k, v in {'From': sender, **(extra or {})}.items()]}}


def dsn(action='failed', status='5.1.1', recipient='person@example.org'):
    return f'''MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="boundary"

--boundary
Content-Type: text/plain

Delivery notification
--boundary
Content-Type: message/delivery-status

Reporting-MTA: dns; example.org

Final-Recipient: rfc822; {recipient}
Action: {action}
Status: {status}
Diagnostic-Code: smtp; delivery result

--boundary
Content-Type: message/rfc822

Message-ID: <initial@example.org>
To: person@example.org
Subject: Original

Hello
--boundary--
'''.encode()


def test_classification():
    outgoing = [dict(id=1, recipient='person@example.org', sent_at=1000, rfc_message_id='<initial@example.org>', gmail_thread_id='thread'),
                dict(id=2, recipient='person@example.org', sent_at=3000, rfc_message_id='<followup@example.org>', gmail_thread_id='thread')]
    reply = message('person@example.org', 2000000, extra={'In-Reply-To': '<initial@example.org>'})
    assert classify_reply(reply, outgoing)[0]['id'] == 1
    auto = message('person@example.org', 2000000, extra={'Auto-Submitted': 'auto-replied'})
    assert classify_reply(auto, outgoing)[1] == 'auto_reply'
    assert classify_reply(message('unrelated@example.org', 2000000), outgoing) is None
    thread = {'messages': [message('owner@example.org', 1000000, 'initial'), reply, message('owner@example.org', 3000000, 'reminder')]}
    assert _thread_has_inbound_reply_from_contact(thread, 'person@example.org', 'owner@example.org', 'initial', 1000000)
    assert delivery_reports(dsn())[0]['kind'] == 'bounced'
    assert delivery_reports(dsn('delayed', '4.2.0'))[0]['kind'] == 'delayed'
    assert delivery_reports(dsn())[0]['original_id'] == '<initial@example.org>'
    assert delivery_reports(b'From: postmaster@example.org\nSubject: delivery failure\n\nNormal email') == []
    with patch.dict(os.environ, {'BACKEND_URL': 'https://club.example.org', 'API_BASE_URL': ''}):
        assert get_tracking_pixel_url('opaque') == 'https://club.example.org/api/track/message/opaque'


async def test_storage_and_sync():
    import httpx
    from app.services import message_tracking, gmail_api
    await init_db()
    db = await get_db()
    await db.execute("INSERT INTO users(id, email) VALUES(1, 'owner@example.org')")
    await db.execute("INSERT INTO contacts(id, email) VALUES(1, 'person@example.org')")
    await db.execute("INSERT INTO campaigns(id, name) VALUES(1, 'Tracking')")
    await db.execute("INSERT INTO campaign_contacts(id, campaign_id, contact_id, status, sent_at, sent_by_user_id) VALUES(1,1,1,'sent','2026-01-01 00:00:00',1)")
    for index, token in enumerate(('first', 'second'), 1):
        await db.execute('''INSERT INTO outreach_messages(id, campaign_contact_id, sender_id, recipient, tracking_token,
           rfc_message_id, gmail_message_id, gmail_thread_id, sent_at) VALUES(?,1,1,'person@example.org',?,?,?,?,'2026-01-01 00:00:00')''',
           (index, token, '<initial@example.org>' if index == 1 else '<second@example.org>', token, token))
    await db.commit()
    await track_message_open('second')
    await track_message_open('second')
    await track_message_open('unknown')
    rows = await (await db.execute('SELECT * FROM outreach_events')).fetchall()
    assert len(rows) == 1 and rows[0]['message_id'] == 2
    assert 'no-store' in (await track_message_open('second')).headers['cache-control']
    # Separate-thread DSN must match its embedded original Message-ID.
    meta = message('mailer-daemon@example.org', 1767312000000, 'bounce', 'separate-thread')
    transport_calls = []
    def handler(request):
        transport_calls.append(str(request.url))
        if request.url.path.endswith('/messages'):
            return httpx.Response(200, json={'messages': [{'id': 'bounce'}]})
        if request.url.params.get('format') == 'raw':
            return httpx.Response(200, json={'raw': base64.urlsafe_b64encode(dsn()).decode()})
        return httpx.Response(200, json=meta)
    original_client = httpx.AsyncClient
    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(handler), **kwargs)
    with patch.object(message_tracking, 'get_valid_access_token', AsyncMock(return_value=('token', 'owner@example.org'))), patch.object(message_tracking.httpx, 'AsyncClient', client):
        first = await message_tracking.sync_sender(1)
        second = await message_tracking.sync_sender(1)
    assert first['ok'] and first['marked_bounced'] == 1, first
    assert second['ok'] and second['marked_bounced'] == 0, second
    assert (await (await db.execute('SELECT status FROM campaign_contacts WHERE id=1')).fetchone())['status'] == 'bounced'
    assert (await (await db.execute('SELECT last_success_at FROM gmail_sync_state WHERE user_id=1')).fetchone())['last_success_at']
    # An authorization failure must not replace the last successful sync timestamp.
    with patch.object(message_tracking, 'get_valid_access_token', AsyncMock(return_value=None)):
        failed = await message_tracking.sync_sender(1)
    assert not failed['ok']
    state = await (await db.execute('SELECT * FROM gmail_sync_state WHERE user_id=1')).fetchone()
    assert state['last_success_at'] and state['error']
    await db.close()

    # Each actual send gets a distinct token and RFC Message-ID before transport.
    def send_handler(request):
        assert request.method == 'POST'
        return httpx.Response(200, json={'id': 'sent-id', 'threadId': 'sent-thread'})
    def send_client(**kwargs):
        return original_client(transport=httpx.MockTransport(send_handler), **kwargs)
    with patch.object(gmail_api, 'get_valid_access_token', AsyncMock(return_value=('token', 'owner@example.org'))), patch.object(gmail_api.httpx, 'AsyncClient', send_client), patch.dict(os.environ, {'BACKEND_URL': 'https://club.example.org', 'API_BASE_URL': ''}):
        await gmail_api.send_via_gmail_api_with_tracking(1, 'person@example.org', 'Hello', 'Body', 1)
        await gmail_api.send_via_gmail_api_with_tracking(1, 'person@example.org', 'Reminder', 'Body', 1)
    def uncertain_handler(_request):
        return httpx.Response(503, text='provider unavailable')
    def uncertain_client(**kwargs):
        return original_client(transport=httpx.MockTransport(uncertain_handler), **kwargs)
    with patch.object(gmail_api, 'get_valid_access_token', AsyncMock(return_value=('token', 'owner@example.org'))), patch.object(gmail_api.httpx, 'AsyncClient', uncertain_client), patch.dict(os.environ, {'BACKEND_URL': 'https://club.example.org', 'API_BASE_URL': ''}):
        try:
            await gmail_api.send_via_gmail_api_with_tracking(1, 'person@example.org', 'Uncertain', 'Body', 1)
            raise AssertionError('Expected an uncertain Gmail result')
        except RuntimeError as exc:
            assert 'uncertain' in str(exc)
    db = await get_db()
    rows = await (await db.execute('SELECT * FROM outreach_messages WHERE id > 2')).fetchall()
    assert len(rows) == 3 and rows[0]['tracking_token'] != rows[1]['tracking_token']
    assert rows[0]['rfc_message_id'] != rows[1]['rfc_message_id'] and all(r['sent_at'] for r in rows[:2])
    assert rows[2]['sent_at'] is None
    await db.close()


async def test_background_sync():
    from fastapi import BackgroundTasks
    from app.routers.outreach import sync_inbox_replies
    from app.services import gmail_reply_sync
    tasks = BackgroundTasks()
    worker = AsyncMock(return_value={'ok': True})
    with patch.object(gmail_reply_sync, 'sync_replies_for_user', worker):
        result = await sync_inbox_replies(background_tasks=tasks, user={'id': 7}, auto_sort_contacted=True)
        assert result == {'ok': True, 'in_progress': True}
        worker.assert_not_awaited()
        await tasks()
        worker.assert_awaited_once_with(7, auto_sort_contacted=True)


def test_public_http_and_restart():
    from fastapi.testclient import TestClient
    from main import app
    # Two application lifespans over the same mounted-file equivalent preserve receipts.
    for _ in range(2):
        with TestClient(app, base_url='https://club.example.org') as client:
            response = client.get('/api/track/message/second', headers={'X-Forwarded-Proto': 'https'})
            assert response.status_code == 200
            assert response.headers['content-type'] == 'image/gif'
            assert 'no-store' in response.headers['cache-control']
            assert client.get('/api/outreach/sync-status').status_code == 401
    async def verify():
        db = await get_db()
        try:
            events = await (await db.execute("SELECT * FROM outreach_events WHERE kind = 'opened'")).fetchall()
            assert len(events) == 1 and events[0]['message_id'] == 2
        finally:
            await db.close()
    asyncio.run(verify())


if __name__ == '__main__':
    test_classification()
    asyncio.run(test_background_sync())
    with tempfile.TemporaryDirectory() as directory:
        with patch.dict(os.environ, {'DATABASE_URL': f'sqlite:///{directory}/tracking.db'}):
            asyncio.run(test_storage_and_sync())
            test_public_http_and_restart()
    print('tracking: ok')
