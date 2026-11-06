"""The search layer every caller shares: one pacer for Monid's free-tier
limit (30 searches/min), one club-wide cache of answers, one usage count the
builder budgets against.

A burst waits for a slot instead of failing; a repeated query costs nothing,
even after a restart; "nothing found" is re-asked sooner than a hit; a
failure is never remembered. Mock Monid over httpx.MockTransport, no network.

From backend/: python tests/test_search_layer.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'search-layer-test-secret-0123456789')

WINDOW_S = 1.0


def _fresh_web_fetch():
    """Drop every app module, as a container restart would."""
    for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
        del sys.modules[mod]
    from app.services import web_fetch
    web_fetch._SEARCH_PACER_WINDOW_S = WINDOW_S
    return web_fetch


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        os.environ['MONID_API_KEY'] = 'monid_live_test'
        os.environ.pop('FIRECRAWL_URL', None)
        os.environ.pop('WEB_SEARCH_CACHE_TTL_HOURS', None)
        os.environ['MONID_SEARCHES_PER_MINUTE'] = '30'

        web_fetch = _fresh_web_fetch()
        from app.database import get_db, init_db
        asyncio.run(init_db())

        calls: list[tuple[float, str]] = []

        def monid(request: httpx.Request) -> httpx.Response:
            query = json.loads(request.content)['input']['queryParams']['query']
            calls.append((time.monotonic(), query))
            if query.startswith('boom'):
                return httpx.Response(500, json={'message': 'upstream down'})
            results = [] if query.startswith('nothing') else [
                {'title': query, 'url': f'https://example.com/{len(calls)}', 'snippet': 'x'}]
            return httpx.Response(200, json={'runId': 'R', 'status': 'COMPLETED', 'output': {'results': results}})

        real_client = httpx.AsyncClient

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(monid), **kwargs)

        async def search(query: str):
            results = await web_fetch.web_search(query, max_results=1)
            return results, web_fetch.last_search_failure()

        with patch('app.services.web_fetch.httpx.AsyncClient', client):
            # --- 35 at once against 30 per window: all finish, none fail,
            # and no window ever holds more than 30. ---
            async def burst():
                return await asyncio.gather(*(search(f'Company {i} CEO') for i in range(35)))

            started = time.monotonic()
            outcomes = asyncio.run(burst())
            elapsed = time.monotonic() - started
            assert all(len(results) == 1 and failure is None for results, failure in outcomes), outcomes
            assert len(calls) == 35, len(calls)
            assert elapsed >= WINDOW_S * 0.9, f'35 searches fit one {WINDOW_S}s window: {elapsed:.2f}s'
            stamps = sorted(t for t, _ in calls)
            # Request stamps trail slot stamps by a little; allow that slack.
            busiest = max(sum(1 for t in stamps if s <= t < s + WINDOW_S - 0.05) for s in stamps)
            assert busiest <= 30, f'{busiest} searches inside one window'
            assert asyncio.run(web_fetch.search_usage_last_hour()) == 35

            # --- A repeat is served from the cache: no upstream call, no
            # usage, even with spacing/case differences. ---
            before = len(calls)
            again, failure = asyncio.run(search('  company 0   ceo '))
            assert again == outcomes[0][0] and failure is None
            assert len(calls) == before

            # ...and after in-process state is gone (restart).
            web_fetch = _fresh_web_fetch()
            assert not web_fetch._search_slots
            again, _ = asyncio.run(search('Company 0 CEO'))
            assert again == outcomes[0][0]
            assert len(calls) == before
            assert asyncio.run(web_fetch.search_usage_last_hour()) == 35

            # --- A genuine empty answer is cached, but only for the short TTL. ---
            web_fetch._EMPTY_SEARCH_CACHE_TTL_S = 0.3
            assert asyncio.run(search('nothing here')) == ([], None)
            assert asyncio.run(search('nothing here')) == ([], None)
            assert len(calls) == before + 1, 'An empty answer was not cached'
            time.sleep(0.4)
            assert asyncio.run(search('nothing here')) == ([], None)
            assert len(calls) == before + 2, 'An expired empty answer was still served'

            # --- A failure is never cached: the next ask goes upstream again. ---
            results, failure = asyncio.run(search('boom query'))
            assert results == [] and failure
            results, failure = asyncio.run(search('boom query'))
            assert results == [] and failure
            assert len(calls) == before + 4, 'A failed search was served from the cache'

        # --- Usage counts real upstream calls only, inside the window; rows
        # past two days are pruned when the next call is recorded. ---
        async def usage_checks() -> None:
            db = await get_db()
            try:
                now = time.time()
                await db.execute('INSERT INTO web_search_usage (at) VALUES (?), (?)',
                                 (now - 7200, now - 3 * 86400))
                await db.commit()
            finally:
                await db.close()
            upstream = len(calls)
            assert await web_fetch.search_usage_last_hour() == upstream
            assert await web_fetch.search_usage_last_hour(86400) == upstream + 1
            await web_fetch._record_search_usage()
            db = await get_db()
            try:
                cur = await db.execute('SELECT COUNT(*) FROM web_search_usage WHERE at < ?',
                                       (time.time() - 2 * 86400,))
                assert (await cur.fetchone())[0] == 0, 'Usage older than two days was kept'
            finally:
                await db.close()

        asyncio.run(usage_checks())

        for key in ('MONID_API_KEY', 'MONID_SEARCHES_PER_MINUTE'):
            os.environ.pop(key, None)


if __name__ == '__main__':
    tests()
    print('search layer: ok')
