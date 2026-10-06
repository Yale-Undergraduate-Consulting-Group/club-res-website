"""Outbound helpers reject hidden recipients/header injection before credentials or I/O."""
import asyncio
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


if __name__=='__main__':
    asyncio.run(run())
    print('mail headers: ok')
