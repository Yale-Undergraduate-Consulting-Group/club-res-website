"""TinyFish (via Monid) adapter and the TinyFish-first, Firecrawl-fallback
waterfall.

TinyFish's Search and Fetch capabilities are reached through Monid
(https://api.monid.ai), a hosted API broker - MONID_API_KEY authenticates
against Monid's API, not a TinyFish-issued key. A single POST /v1/run call
runs synchronously and returns {"status": "COMPLETED", "output": {...}} -
confirmed live against real production traffic (see web_fetch.py's module
docstring). TinyFish itself is layered ahead of self-hosted Firecrawl
because it is a healthy, working search backend where Firecrawl's
self-hosted /v1/search is bot-blocked upstream (see test_web_fetch.py's
docstring and docs/FIRECRAWL-SEARCH-EXPANSION-PLAN.md section 1.6). These
tests use httpx.MockTransport, matching the convention in test_web_fetch.py,
and never touch a network.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'tinyfish-adapter-secret-xxxxxxxxxxxxxxxx')


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.services import web_fetch
        from fastapi import HTTPException

        asyncio.run(init_db())

        async def seed() -> None:
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO users (id, email, name) VALUES (1, 'a@yale.edu', 'A'), (2, 'b@yale.edu', 'B')"
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())

        real_client = httpx.AsyncClient

        def client_for(handler):
            return lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)

        # --- Unconfigured: never raises, never calls the network ---
        os.environ.pop('MONID_API_KEY', None)
        os.environ.pop('FIRECRAWL_URL', None)
        assert not web_fetch.tinyfish_configured()
        assert not web_fetch.web_search_configured()
        assert asyncio.run(web_fetch.fetch_page('https://acme.com', user_id=1)) is None
        assert asyncio.run(web_fetch.web_search('Acme CEO', user_id=1)) == []

        # web_search_configured() is true with either backend alone.
        os.environ['MONID_API_KEY'] = 'monid_live_test'
        assert web_fetch.tinyfish_configured()
        assert web_fetch.web_search_configured()
        os.environ.pop('MONID_API_KEY')
        os.environ['FIRECRAWL_URL'] = 'http://100.84.7.57:3002'
        assert web_fetch.web_search_configured()
        os.environ.pop('FIRECRAWL_URL')
        os.environ['MONID_API_KEY'] = 'monid_live_test'

        # --- TinyFish fetch via Monid: success shape mapping, synchronous
        # COMPLETED on the first POST (the common case observed live). ---
        def fetch_handler(request: httpx.Request) -> httpx.Response:
            assert request.headers['Authorization'] == 'Bearer monid_live_test'
            assert str(request.url) == 'https://api.monid.ai/v1/run'
            import json
            body = json.loads(request.content)
            assert body == {"provider": "tinyfish", "endpoint": "/fetch",
                             "input": {"body": {"urls": ["https://acme.com"], "links": True}}}
            return httpx.Response(200, json={
                "runId": "01TEST", "provider": "tinyfish", "endpoint": "/fetch", "status": "COMPLETED",
                "output": {"results": [{"url": "https://acme.com", "final_url": "https://acme.com",
                                         "title": "Acme", "text": "# Acme\nWe make things.",
                                         "links": ["https://acme.com/about"]}], "errors": []},
            })

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(fetch_handler)):
            page = asyncio.run(web_fetch.fetch_page('https://acme.com', user_id=1))
            assert page is not None
            assert page.content == '# Acme\nWe make things.'
            assert page.links == ['https://acme.com/about']

        # --- TinyFish fetch: no results / errors degrades to None ---
        def fetch_empty_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "runId": "01TEST", "status": "COMPLETED",
                "output": {"results": [], "errors": [{"url": "https://acme.com", "error": "timeout"}]},
            })

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(fetch_empty_handler)):
            assert asyncio.run(web_fetch.fetch_page('https://acme.com', user_id=1)) is None

        # --- A run that never completes (FAILED) also degrades to None ---
        def fetch_failed_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"runId": "01TEST", "status": "FAILED", "error": {"message": "boom"}})

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(fetch_failed_handler)):
            assert asyncio.run(web_fetch.fetch_page('https://acme.com', user_id=1)) is None

        # --- A non-terminal status is polled via GET /v1/runs/{id} until
        # terminal, matching Monid's async run model for slower jobs. ---
        poll_calls = {'n': 0}

        def search_poll_dispatch(request: httpx.Request) -> httpx.Response:
            if request.method == 'POST':
                return httpx.Response(200, json={"runId": "01POLL", "status": "RUNNING"})
            poll_calls['n'] += 1
            if poll_calls['n'] < 2:
                return httpx.Response(200, json={"runId": "01POLL", "status": "RUNNING"})
            return httpx.Response(200, json={
                "runId": "01POLL", "status": "COMPLETED",
                "output": {"query": "Acme CEO", "results": [
                    {"position": 1, "title": "Acme", "url": "https://acme.com", "snippet": "Acme is a company."},
                ]},
            })

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(search_poll_dispatch)), \
             patch('app.services.web_fetch.asyncio.sleep', return_value=None):
            results = asyncio.run(web_fetch.web_search('Acme CEO', user_id=1))
            assert results == [{"title": "Acme", "url": "https://acme.com", "content": "Acme is a company."}]
            assert poll_calls['n'] == 2, 'Expected exactly one poll before the run completed'

        # --- TinyFish search: success shape mapping (snippet -> content),
        # truncated to max_results since the API has no count parameter. ---
        def search_handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            assert body["input"]["queryParams"] == {"query": "Acme CEO"}
            return httpx.Response(200, json={
                "runId": "01TEST", "status": "COMPLETED",
                "output": {"query": "Acme CEO", "results": [
                    {"position": 1, "title": "Acme - Wikipedia", "url": "https://en.wikipedia.org/wiki/Acme", "snippet": "Acme is a company."},
                    {"position": 2, "title": "Acme Careers", "url": "https://acme.com/careers", "snippet": "Join Acme."},
                ], "total_results": 2},
            })

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(search_handler)):
            results = asyncio.run(web_fetch.web_search('Acme CEO', max_results=1, user_id=1))
            assert results == [{"title": "Acme - Wikipedia", "url": "https://en.wikipedia.org/wiki/Acme", "content": "Acme is a company."}]

        # --- Requesting more results than one page holds (~8 observed live)
        # fetches additional pages, deduped by URL, rather than silently
        # capping every search at one page regardless of what was asked
        # for. ---
        page_calls: list[int] = []

        def paginated_handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            page = body["input"]["queryParams"].get("page", 0)
            page_calls.append(page)
            if page == 0:
                assert "page" not in body["input"]["queryParams"]
                results = [{"title": f"Result {i}", "url": f"https://acme.com/{i}", "snippet": "x"} for i in range(8)]
            elif page == 1:
                results = [{"title": f"Result {i}", "url": f"https://acme.com/{i}", "snippet": "x"} for i in range(8, 15)]
            else:
                results = []
            return httpx.Response(200, json={"runId": "01TEST", "status": "COMPLETED", "output": {"results": results}})

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(paginated_handler)):
            results = asyncio.run(web_fetch.web_search('Acme leadership', max_results=15, user_id=1))
            assert len(results) == 15, f'Expected 15 results across two pages, got {len(results)}'
            assert page_calls == [0, 1], f'Expected exactly two page fetches, got {page_calls}'

        # --- site:/-site: operators (how every discovery query in this
        # codebase restricts to a domain, e.g. site:linkedin.com/in) are
        # translated to TinyFish's include_domains/exclude_domains params
        # rather than sent as literal query text - live testing showed
        # TinyFish silently ignores the literal operator despite its docs
        # claiming to "honour" it, while include_domains returns real
        # results for the identical intent. ---
        def site_operator_handler(request: httpx.Request) -> httpx.Response:
            import json
            body = json.loads(request.content)
            assert body["input"]["queryParams"] == {
                "query": "Acme leadership OR executives",
                "include_domains": "linkedin.com",
                "exclude_domains": "pinterest.com",
            }
            return httpx.Response(200, json={"runId": "01TEST", "status": "COMPLETED", "output": {"results": []}})

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(site_operator_handler)):
            asyncio.run(web_fetch.web_search(
                'Acme leadership OR executives site:linkedin.com/in -site:pinterest.com', user_id=1))

        # --- A genuine empty TinyFish search result is trusted, not treated
        # as a failure - it must NOT fall through to Firecrawl. ---
        os.environ['FIRECRAWL_URL'] = 'http://100.84.7.57:3002'
        firecrawl_called = False

        def dispatch_empty(request: httpx.Request) -> httpx.Response:
            if 'monid.ai' in str(request.url):
                return httpx.Response(200, json={"runId": "01TEST", "status": "COMPLETED",
                                                  "output": {"query": "hello world", "results": []}})
            nonlocal firecrawl_called
            firecrawl_called = True
            return httpx.Response(200, json={"data": [{"title": "should not happen", "url": "x", "description": "x"}]})

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(dispatch_empty)):
            results = asyncio.run(web_fetch.web_search('hello world', user_id=1))
            assert results == []
            assert not firecrawl_called, 'A real empty TinyFish result must not fall through to Firecrawl'

        # --- TinyFish failure DOES fall through to Firecrawl ---
        def dispatch_fallback(request: httpx.Request) -> httpx.Response:
            if 'monid.ai' in str(request.url):
                return httpx.Response(401, json={"code": 401, "message": "Invalid API key format"})
            return httpx.Response(200, json={"data": [{"title": "Firecrawl result", "url": "https://acme.com", "description": "from firecrawl"}]})

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(dispatch_fallback)):
            results = asyncio.run(web_fetch.web_search('Acme CEO', user_id=1))
            assert results == [{"title": "Firecrawl result", "url": "https://acme.com", "content": "from firecrawl"}]

        # Same fallback behavior for fetch_page.
        def dispatch_fetch_fallback(request: httpx.Request) -> httpx.Response:
            if 'monid.ai' in str(request.url):
                return httpx.Response(429, json={"code": 429, "message": "rate limited"})
            return httpx.Response(200, json={"data": {"markdown": "# from firecrawl", "links": []}})

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(dispatch_fetch_fallback)):
            page = asyncio.run(web_fetch.fetch_page('https://acme.com', user_id=1))
            assert page is not None
            assert page.content == '# from firecrawl'

        os.environ.pop('FIRECRAWL_URL')

        # --- Quota: same shape as reserve_firecrawl_call. ---
        os.environ['TINYFISH_CALLS_PER_MEMBER_PER_HOUR'] = '2'
        os.environ['TINYFISH_CALLS_PER_CLUB_PER_HOUR'] = '100'

        def ok_search_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"runId": "01TEST", "status": "COMPLETED", "output": {"results": []}})

        with patch('app.services.web_fetch.httpx.AsyncClient', client_for(ok_search_handler)):
            for _ in range(2):
                asyncio.run(web_fetch.web_search('q', user_id=2))
            try:
                asyncio.run(web_fetch.web_search('q', user_id=2))
                raise AssertionError('Per-member TinyFish quota did not stop the third call')
            except HTTPException as exc:
                assert exc.status_code == 429

        del os.environ['TINYFISH_CALLS_PER_MEMBER_PER_HOUR']
        del os.environ['TINYFISH_CALLS_PER_CLUB_PER_HOUR']


if __name__ == '__main__':
    tests()
    print('tinyfish adapter: ok')
