"""Campaign recipient edits must travel in a JSON body, not the URL.

Subject and body were declared as bare scalars, so FastAPI bound them to query
parameters. A full HTML email then had to be URL-encoded into the request line,
which exceeds CloudFront's 8 KB limit in production and writes message content
into access logs. This test pins the JSON contract and a realistic body size.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'campaign-contact-update-secret-xxxxxxxx')


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from fastapi.testclient import TestClient
        from app.database import get_db, init_db
        from app.jwt_utils import create_token
        import main

        asyncio.run(init_db())

        async def seed() -> tuple[int, int]:
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO users (id,email,name,role,is_active) VALUES (1,'a@yale.edu','A','admin',1)"
                )
                cur = await db.execute(
                    "INSERT INTO contacts (name,email,company) VALUES ('C','c@example.com','Ex')"
                )
                contact_id = int(cur.lastrowid)
                cur = await db.execute(
                    "INSERT INTO campaigns (name,status,owner_user_id,sender_user_id) VALUES ('K','draft',1,1)"
                )
                campaign_id = int(cur.lastrowid)
                cur = await db.execute(
                    "INSERT INTO campaign_contacts (campaign_id,contact_id,status) VALUES (?,?,'pending')",
                    (campaign_id, contact_id),
                )
                cc_id = int(cur.lastrowid)
                await db.commit()
                return campaign_id, cc_id
            finally:
                await db.close()

        campaign_id, cc_id = asyncio.run(seed())
        client = TestClient(main.app)
        client.cookies.set('yucg_session', create_token(1, 'a@yale.edu', 'A', None, 'admin'))
        url = f'/api/campaigns/{campaign_id}/contact/{cc_id}'

        # A realistic HTML email, far past what belongs in a URL.
        big_body = '<p>' + ('Lorem ipsum dolor sit amet. ' * 400) + '</p>'
        assert len(big_body) > 8192, 'fixture must exceed the CloudFront URL limit'

        resp = client.patch(url, json={'subject': 'Intro', 'body': big_body})
        assert resp.status_code == 200, (resp.status_code, resp.text[:200])

        async def stored() -> tuple[str, str]:
            db = await get_db()
            try:
                row = await (await db.execute(
                    'SELECT email_subject, email_body FROM campaign_contacts WHERE id=?', (cc_id,)
                )).fetchone()
                return row['email_subject'], row['email_body']
            finally:
                await db.close()

        subject, body = asyncio.run(stored())
        assert subject == 'Intro', subject
        assert body == big_body, 'full body must round-trip through the JSON contract'

        # An empty payload is still rejected rather than silently writing nothing.
        assert client.patch(url, json={}).status_code == 400


if __name__ == '__main__':
    tests()
    print('campaign contact update: ok')
