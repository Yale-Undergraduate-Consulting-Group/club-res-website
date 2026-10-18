"""Roster admission and verified Google identity with a fake OAuth server."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET'] = 'membership-admission-test-secret-xxxx'
from app.database import init_db, get_db
from app.routers.auth import _do_google_callback

async def run():
    with tempfile.TemporaryDirectory() as directory:
        os.environ['DATABASE_URL'] = 'sqlite:///' + directory + '/test.db'
        await init_db()
        identity = {'email': 'member@yale.edu', 'id': 'google-member', 'verified_email': True}
        def response(request):
            if request.url.path == '/token':
                return httpx.Response(200,json={'access_token':'fake-access','expires_in':3600})
            return httpx.Response(200,json=identity)
        original = httpx.AsyncClient
        def client(**kwargs):
            return original(transport=httpx.MockTransport(response),**kwargs)
        with patch('app.routers.auth.httpx.AsyncClient', client):
            result = await _do_google_callback('fake-code')
            assert 'invitation_required' in result.headers['location']
            assert 'set-cookie' not in result.headers
            db = await get_db()
            try:
                await db.execute("INSERT INTO users(email,name) VALUES ('member@yale.edu','Member')")
                await db.commit()
            finally:
                await db.close()
            identity['verified_email'] = False
            result = await _do_google_callback('fake-code')
            assert 'email_not_verified' in result.headers['location']
            identity['verified_email'] = True
            result = await _do_google_callback('fake-code')
            assert 'yucg_session=' in result.headers['set-cookie']
            db = await get_db()
            try:
                await db.execute('UPDATE users SET is_active=0')
                await db.commit()
            finally:
                await db.close()
            result = await _do_google_callback('fake-code')
            assert 'account_deactivated' in result.headers['location']
        print('membership admission: ok')

if __name__ == '__main__':
    asyncio.run(run())
