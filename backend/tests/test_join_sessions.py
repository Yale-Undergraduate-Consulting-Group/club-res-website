"""Join sessions (QR sign-up): an admin opens one, verified @yale.edu Google
accounts that sign in through it become members while it is open, and it stops
admitting once closed, expired or full. Google is faked.
From backend/: python tests/test_join_sessions.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET'] = 'join-session-test-secret-0123456789abcd'
os.environ['DATABASE_URL'] = f"sqlite:///{Path(tempfile.mkdtemp()) / 't.db'}"
os.environ['GOOGLE_CLIENT_ID'] = 'test-client'

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from app.database import get_db, init_db  # noqa: E402
from app.jwt_utils import create_token  # noqa: E402

ADMIN, MEMBER = 1, 2
identity = {'email': '', 'id': '', 'verified_email': True, 'name': 'Someone'}


def google(request: httpx.Request) -> httpx.Response:
    if request.url.path == '/token':
        return httpx.Response(200, json={'access_token': 'fake', 'expires_in': 3600})
    return httpx.Response(200, json=identity)


async def setup() -> None:
    await init_db()
    db = await get_db()
    try:
        await db.executemany("INSERT INTO users (id, email, name, role, is_active) VALUES (?, ?, ?, ?, ?)",
                             [(ADMIN, 'admin@yale.edu', 'Admin', 'admin', 1),
                              (MEMBER, 'ben@yale.edu', 'Ben', 'standard', 1),
                              (3, 'gone@yale.edu', 'Gone', 'standard', 0)])
        await db.commit()
    finally:
        await db.close()


async def sql(query: str, *args):
    db = await get_db()
    try:
        rows = await (await db.execute(query, args)).fetchall()
        await db.commit()
        return [tuple(r) for r in rows]
    finally:
        await db.close()


def as_member(user_id: int, role: str) -> TestClient:
    client = TestClient(main.app, raise_server_exceptions=False)
    client.cookies.set('yucg_session', create_token(user_id, f'{user_id}@yale.edu', None, None, role))
    return client


def scan(token: str, email: str, google_id: str) -> str:
    """A phone scanning the QR: start sign-in with the join token, then Google
    returns this identity. Returns where the browser ends up."""
    browser = TestClient(main.app, raise_server_exceptions=False)
    start = browser.get(f'/api/auth/google?join={token}', follow_redirects=False)
    location = start.headers['location']
    if 'accounts.google.com' not in location:
        return location
    state = parse_qs(urlparse(location).query)['state'][0]
    identity.update(email=email, id=google_id)
    original = httpx.AsyncClient
    with patch('app.routers.auth.httpx.AsyncClient', lambda **kw: original(transport=httpx.MockTransport(google), **kw)):
        done = browser.get(f'/api/auth/google/callback?code=c&state={state}', follow_redirects=False)
    return done.headers['location'] + (' +session' if 'yucg_session=' in done.headers.get('set-cookie', '') else '')


def tests() -> None:
    asyncio.run(setup())
    admin, member = as_member(ADMIN, 'admin'), as_member(MEMBER, 'standard')

    assert member.post('/api/admin/invitations/join-sessions', json={}).status_code == 403
    assert admin.post('/api/admin/invitations/join-sessions', json={'minutes': 600}).status_code == 422

    opened = admin.post('/api/admin/invitations/join-sessions', json={'minutes': 60, 'max_members': 2})
    assert opened.status_code == 201, opened.text
    session, token = opened.json(), opened.json()['token']
    assert session['open'] and session['members'] == []
    assert asyncio.run(sql('SELECT COUNT(*) FROM join_sessions WHERE token_hash=?', token)) == [(0,)], 'plaintext token stored'

    # A new Yale account joins and is signed in; an existing member just signs in.
    assert scan(token, 'ana@yale.edu', 'g-ana').endswith('+session')
    assert asyncio.run(sql("SELECT role, is_active FROM users WHERE email='ana@yale.edu'")) == [('standard', 1)]
    assert scan(token, 'ben@yale.edu', 'g-ben').endswith('+session')
    # A deactivated member stays out; a non-Yale account never gets in.
    assert 'account_deactivated' in scan(token, 'gone@yale.edu', 'g-gone')
    assert 'domain_not_allowed' in scan(token, 'x@gmail.com', 'g-x')

    status = admin.get(f"/api/admin/invitations/join-sessions/{session['id']}").json()
    assert [m['email'] for m in status['members']] == ['ana@yale.edu'], status

    # The cap: one more admitted, then the session is full.
    assert scan(token, 'cy@yale.edu', 'g-cy').endswith('+session')
    assert 'join_closed' in scan(token, 'di@yale.edu', 'g-di')
    assert asyncio.run(sql("SELECT COUNT(*) FROM users WHERE email='di@yale.edu'")) == [(0,)]

    # Closing stops it at once, including a sign-in already started before the close.
    second = admin.post('/api/admin/invitations/join-sessions', json={}).json()
    browser = TestClient(main.app, raise_server_exceptions=False)
    started = browser.get(f"/api/auth/google?join={second['token']}", follow_redirects=False)
    assert 'accounts.google.com' in started.headers['location']
    closed = admin.post(f"/api/admin/invitations/join-sessions/{second['id']}/close").json()
    assert closed['closed'] and not closed['open']
    assert 'join_closed' in scan(second['token'], 'ed@yale.edu', 'g-ed')
    state = parse_qs(urlparse(started.headers['location']).query)['state'][0]
    identity.update(email='fay@yale.edu', id='g-fay')
    original = httpx.AsyncClient
    with patch('app.routers.auth.httpx.AsyncClient', lambda **kw: original(transport=httpx.MockTransport(google), **kw)):
        late = browser.get(f'/api/auth/google/callback?code=c&state={state}', follow_redirects=False)
    assert 'join_closed' in late.headers['location']

    # An expired session and an unknown token admit nobody.
    third = admin.post('/api/admin/invitations/join-sessions', json={}).json()
    asyncio.run(sql('UPDATE join_sessions SET expires_at=1 WHERE id=?', third['id']))
    assert 'join_closed' in scan(third['token'], 'gu@yale.edu', 'g-gu')
    assert 'join_closed' in scan('not-a-token', 'gu@yale.edu', 'g-gu')


if __name__ == '__main__':
    tests()
    print('join sessions: ok')
