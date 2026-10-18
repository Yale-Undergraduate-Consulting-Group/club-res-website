"""Browser binding, expiry, replay and invited identity acceptance without network."""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse, parse_qs
import httpx
from fastapi import FastAPI
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET']='oauth-browser-challenge-test-secret'
from app.database import init_db,get_db
from app.routers import auth
from app.routers.invitations import create_invitation,InvitationCreate

async def run():
    with tempfile.TemporaryDirectory() as directory:
        os.environ['DATABASE_URL']='sqlite:///'+directory+'/test.db'
        await init_db()
        db=await get_db()
        try:
            await db.execute("INSERT INTO users(id,email,role) VALUES (1,'admin@yale.edu','admin')")
            await db.commit()
        finally: await db.close()
        app=FastAPI();app.include_router(auth.router,prefix='/api/auth')
        with patch.object(auth,'GOOGLE_CLIENT_ID','fake-client'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as browser:
                start=await browser.get('/api/auth/google')
                query=parse_qs(urlparse(start.headers['location']).query)
                assert query['scope']==['openid email profile']
                state=query['state'][0]
                callback=AsyncMock(return_value=auth.RedirectResponse('/'))
                with patch.object(auth,'_do_google_callback',callback):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as attacker:
                        bad=await attacker.get('/api/auth/google/callback',params={'code':'fake','state':state})
                        assert 'invalid_callback' in bad.headers['location']
                    callback.assert_not_awaited()
                    good=await browser.get('/api/auth/google/callback',params={'code':'fake','state':state})
                    assert good.headers['location']=='/'
                    callback.assert_awaited_once()
                    replay=await browser.get('/api/auth/google/callback',params={'code':'fake','state':state})
                    assert 'invalid_callback' in replay.headers['location']
                start=await browser.get('/api/auth/google')
                state=parse_qs(urlparse(start.headers['location']).query)['state'][0]
                db=await get_db()
                try:
                    await db.execute('UPDATE oauth_challenges SET expires_at=?',(int(time.time())-1,));await db.commit()
                finally: await db.close()
                expired=await browser.get('/api/auth/google/callback',params={'code':'fake','state':state})
                assert 'invalid_callback' in expired.headers['location']
        invite=await create_invitation(InvitationCreate(email='new@yale.edu'),{'id':1})
        identity={'email':'wrong@yale.edu','verified_email':True,'id':'google-new'}
        token_data={'access_token':'fake','expires_in':3600}
        original=httpx.AsyncClient
        def response(request):
            return httpx.Response(200,json=token_data) if request.url.path=='/token' else httpx.Response(200,json=identity)
        def client(**kwargs): return original(transport=httpx.MockTransport(response),**kwargs)
        with patch('app.routers.auth.httpx.AsyncClient',client):
            result=await auth._do_google_callback('fake',{'purpose':'identity','invitation_id':invite['id']})
            assert 'invitation_invalid' in result.headers['location']
            identity['email']='new@yale.edu'
            result=await auth._do_google_callback('fake',{'purpose':'identity','invitation_id':invite['id']})
            assert 'yucg_session=' in result.headers['set-cookie']
            result=await auth._do_google_callback('fake',{'purpose':'identity','invitation_id':invite['id']})
            assert 'invitation_invalid' in result.headers['location']
            result=await auth._do_google_callback('fake',{'purpose':'gmail','user_id':1})
            assert 'gmail_account_mismatch' in result.headers['location']
            result=await auth._do_google_callback('fake',{'purpose':'gmail','user_id':2})
            assert 'gmail_scopes_required' in result.headers['location']
        db=await get_db()
        try:
            row=await (await db.execute("SELECT access_token FROM users WHERE email='new@yale.edu'")).fetchone()
            assert row['access_token'] is None, 'Identity token accidentally became Gmail credential'
        finally: await db.close()
        token_data['scope']='https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly'
        with patch('app.routers.auth.httpx.AsyncClient',client):
            result=await auth._do_google_callback('fake',{'purpose':'gmail','user_id':2})
            assert '/profile?tab=integrations' in result.headers['location']
            assert 'set-cookie' not in result.headers, 'Gmail connect must not replace login session'
        print('oauth browser binding and invitation acceptance: ok')

if __name__=='__main__': asyncio.run(run())
