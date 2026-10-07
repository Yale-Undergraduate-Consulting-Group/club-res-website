"""An in-flight refresh must not restore Gmail after disconnect."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET'] = 'gmail-race-test-secret-not-production'

from app.database import get_db, init_db
from app.routers.auth import disconnect_gmail
from app.services import gmail_api
from app.token_crypto import encrypt_token


async def tests():
    with tempfile.TemporaryDirectory() as directory:
        os.environ['DATABASE_URL'] = 'sqlite:///' + directory + '/gmail.db'
        await init_db()
        db = await get_db()
        try:
            await db.execute(
                "INSERT INTO users(id,email,is_active,access_token,refresh_token,token_expires_at) VALUES(1,'member@yale.edu',1,?,?,0)",
                (encrypt_token('expired-access'), encrypt_token('refresh-original')),
            )
            await db.commit()
        finally:
            await db.close()

        class RefreshClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def post(self, *args, **kwargs):
                await disconnect_gmail({'id': 1})
                return SimpleNamespace(status_code=200, json=lambda: {'access_token': 'new-access', 'expires_in': 3600})

        with patch.object(gmail_api, 'GOOGLE_CLIENT_ID', 'client'), \
             patch.object(gmail_api, 'GOOGLE_CLIENT_SECRET', 'secret'), \
             patch.object(gmail_api.httpx, 'AsyncClient', RefreshClient):
            assert await gmail_api.get_valid_access_token(1) is None

        db = await get_db()
        try:
            row = await (await db.execute('SELECT access_token,refresh_token,token_expires_at FROM users WHERE id=1')).fetchone()
            assert row['access_token'] is None and row['refresh_token'] is None and row['token_expires_at'] is None
            await db.execute(
                'UPDATE users SET access_token=?,refresh_token=?,token_expires_at=0 WHERE id=1',
                (encrypt_token('expired-again'), encrypt_token('refresh-old')),
            )
            await db.commit()
        finally:
            await db.close()

        class ReauthorizationClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def post(self, *args, **kwargs):
                reauth_db = await get_db()
                try:
                    await reauth_db.execute(
                        'UPDATE users SET access_token=?,refresh_token=?,token_expires_at=? WHERE id=1',
                        (encrypt_token('newer-access'), encrypt_token('refresh-new'), 4102444800),
                    )
                    await reauth_db.commit()
                finally:
                    await reauth_db.close()
                return SimpleNamespace(status_code=200, json=lambda: {'access_token': 'stale-refreshed-access', 'expires_in': 3600})

        with patch.object(gmail_api, 'GOOGLE_CLIENT_ID', 'client'), \
             patch.object(gmail_api, 'GOOGLE_CLIENT_SECRET', 'secret'), \
             patch.object(gmail_api.httpx, 'AsyncClient', ReauthorizationClient):
            assert await gmail_api.get_valid_access_token(1) is None

        db = await get_db()
        try:
            row = await (await db.execute('SELECT access_token,refresh_token FROM users WHERE id=1')).fetchone()
            from app.token_crypto import decrypt_token
            assert decrypt_token(row['access_token']) == 'newer-access'
            assert decrypt_token(row['refresh_token']) == 'refresh-new'
        finally:
            await db.close()


if __name__ == '__main__':
    asyncio.run(tests())
    print('gmail disconnect race: ok')
