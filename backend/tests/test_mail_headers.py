"""Outbound helpers reject hidden recipients/header injection before credentials or I/O."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.gmail_api import send_via_gmail_api,send_via_gmail_api_multipart,send_via_gmail_api_with_tracking
from app.services.mail_address import validate_recipient


async def run():
    credentials=AsyncMock(side_effect=AssertionError('Invalid headers reached credentials'))
    with patch('app.services.gmail_api.get_valid_access_token',credentials):
        for sender in (send_via_gmail_api,send_via_gmail_api_multipart,send_via_gmail_api_with_tracking):
            base={'user_id':1,'to_email':'one@example.org','subject':'Subject','body':'Body'}
            if sender is send_via_gmail_api_with_tracking:
                base['campaign_contact_id']=1
            for invalid in (
                {'to_email':'one@example.org,two@example.org'},
                {'to_email':'One <one@example.org>'},
                {'to_email':'one@example.org\r\nBcc:two@example.org'},
                {'subject':'Subject\r\nBcc:two@example.org'},
                {'from_name':'Name\nBcc:two@example.org'},
            ):
                try:
                    await sender(**(base|invalid))
                    raise AssertionError('Invalid headers accepted')
                except ValueError:
                    pass
        assert credentials.await_count==0
    validate_recipient('person+club@example.org')
    await from_reads_as_the_member()


async def from_reads_as_the_member():
    """Mail goes out from the member's own Gmail under their own name - the
    one their sign-off prints - never as "YUCG Outreach"."""
    import base64, email, httpx
    from app.services import gmail_api as G
    from app.services.email_body import SignOff
    sent=[]
    real_client=httpx.AsyncClient
    def gmail(request):
        raw=json.loads(request.content)['raw']
        sent.append(email.message_from_bytes(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))['From'])
        return httpx.Response(200,json={'id':'m1','threadId':'t1'})
    with patch('app.services.gmail_api.get_valid_access_token',AsyncMock(return_value=('token','andre.costa@yale.edu'))), \
         patch('app.services.gmail_api.require_delivery_enabled',lambda:None), \
         patch('app.services.settings_service.load_sign_off',AsyncMock(return_value=SignOff(name='Andre Costa'))), \
         patch('app.services.gmail_api.httpx.AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(gmail),**kw)):
        await G.send_via_gmail_api(user_id=1,to_email='client@example.org',subject='Hello',body='Hi')
        await G.send_via_gmail_api_multipart(user_id=1,to_email='client@example.org',subject='Hello',body='Hi',
                                             sign_off=SignOff(name='Andre H. Costa'))
    assert sent==['Andre Costa <andre.costa@yale.edu>','"Andre H. Costa" <andre.costa@yale.edu>'],sent
    assert all('YUCG Outreach' not in s for s in sent),sent


if __name__=='__main__':
    asyncio.run(run())
    print('mail headers: ok')
