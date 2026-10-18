"""Firecrawl adapter: dark-launched by default, quota-gated, never blocks a
caller on an unreachable box.

The tailnet join that would make FIRECRAWL_URL reachable in production is
infrastructure, not code — see docs/FIRECRAWL-SEARCH-EXPANSION-PLAN.md
section 1.3. These tests use httpx.MockTransport, matching the convention in
test_dispatch_recovery.py, and never touch a network.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'web-fetch-adapter-secret-xxxxxxxxxxxxxxxx')


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.services import web_fetch

        asyncio.run(init_db())
        async def seed_user() -> None:
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO users (id,email,name,role,is_active) VALUES (1,'a@yale.edu','A','standard',1)"
                )
                await db.execute(
                    "INSERT INTO users (id,email,name,role,is_active) VALUES (2,'b@yale.edu','B','standard',1)"
                )
                await db.execute(
                    "INSERT INTO users (id,email,name,role,is_active) VALUES (3,'c@yale.edu','C','standard',1)"
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed_user())

        # Unconfigured: never raises, never calls the network, no quota spent.
        os.environ.pop('FIRECRAWL_URL', None)
        assert not web_fetch.firecrawl_configured()
        assert asyncio.run(web_fetch.fetch_page('https://acme.com', user_id=1)) is None

        os.environ['FIRECRAWL_URL'] = 'http://100.84.7.57:3002'
        assert web_fetch.firecrawl_configured()

        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            body = request.read()
            import json
            payload = json.loads(body)
            if payload['url'] == 'https://error.example.com':
                return httpx.Response(500, json={'error': 'boom'})
            if payload['url'] == 'https://empty.example.com':
                return httpx.Response(200, json={'data': {'markdown': ''}})
            return httpx.Response(200, json={
                'data': {
                    'markdown': '# Acme\nWe make things.',
                    'links': ['https://acme.com/about', 'https://acme.com/team'],
                }
            })

        real_client = httpx.AsyncClient

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        with patch('app.services.web_fetch.httpx.AsyncClient', client):
            page = asyncio.run(web_fetch.fetch_page('https://acme.com', user_id=1))
            assert page is not None
            assert page.content == '# Acme\nWe make things.'
            assert page.links == ['https://acme.com/about', 'https://acme.com/team']
            assert calls == ['http://100.84.7.57:3002/v1/scrape']

            # A 500 from the box degrades to None, not an exception a caller
            # must specially handle.
            assert asyncio.run(web_fetch.fetch_page('https://error.example.com', user_id=1)) is None

            # Empty content is treated the same as no page, not a blank result
            # a caller would mistake for "the page said nothing."
            assert asyncio.run(web_fetch.fetch_page('https://empty.example.com', user_id=1)) is None

            # Quota: same 15/hr/member, 120/hr club shape as the assistant.
            # A fresh member id isolates this from the three calls already
            # reserved above.
            os.environ['FIRECRAWL_CALLS_PER_MEMBER_PER_HOUR'] = '2'
            for _ in range(2):
                assert asyncio.run(web_fetch.fetch_page('https://acme.com', user_id=2)) is not None
            from fastapi import HTTPException
            try:
                asyncio.run(web_fetch.fetch_page('https://acme.com', user_id=2))
                raise AssertionError('Per-member Firecrawl quota did not stop the third call')
            except HTTPException as exc:
                assert exc.status_code == 429

        # No URL given: no request attempted, no quota spent, matches the
        # unconfigured case rather than raising.
        with patch('app.services.web_fetch.httpx.AsyncClient', client):
            assert asyncio.run(web_fetch.fetch_page('', user_id=1)) is None

        # web_search: unconfigured returns [], never calls the network.
        os.environ.pop('FIRECRAWL_URL', None)
        assert asyncio.run(web_fetch.web_search('Acme CEO', user_id=1)) == []

        os.environ['FIRECRAWL_URL'] = 'http://100.84.7.57:3002'
        search_calls: list[str] = []

        def search_handler(request: httpx.Request) -> httpx.Response:
            search_calls.append(str(request.url))
            import json
            payload = json.loads(request.read())
            if payload['query'] == 'error query':
                return httpx.Response(500, json={'error': 'boom'})
            return httpx.Response(200, json={
                'data': [
                    {'url': 'https://acme.com/about', 'title': 'Acme - About', 'description': 'Acme makes things.'},
                    {'url': 'https://acme.com/team', 'title': 'Acme - Team'},
                ],
            })

        def search_client(**kwargs):
            return real_client(transport=httpx.MockTransport(search_handler), **kwargs)

        with patch('app.services.web_fetch.httpx.AsyncClient', search_client):
            results = asyncio.run(web_fetch.web_search('Acme CEO', user_id=3))
            assert results == [
                {'title': 'Acme - About', 'url': 'https://acme.com/about', 'content': 'Acme makes things.'},
                {'title': 'Acme - Team', 'url': 'https://acme.com/team', 'content': ''},
            ]
            assert search_calls == ['http://100.84.7.57:3002/v1/search']

            # A 500 degrades to an empty list, matching fetch_page's degrade
            # behavior - callers never special-case a Firecrawl outage.
            assert asyncio.run(web_fetch.web_search('error query', user_id=3)) == []

            # No query given: no request attempted.
            assert asyncio.run(web_fetch.web_search('', user_id=3)) == []


if __name__ == '__main__':
    tests()
    print('web fetch adapter: ok')
