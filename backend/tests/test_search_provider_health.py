"""A search provider that cannot run is never "nobody found".

Monid answers 402 when its wallet is empty, 401/403 for a refused key and 429
when rate limited; a timeout is its own case. Each must come back from
web_search as a failure of that kind - not as an empty success - reach the
run's search_errors with one plain member sentence, and (for credit, key and
rate limits) hold further calls back for a while instead of repeating them.
A search that ran and found nothing stays empty and successful.

Mock Monid over httpx.MockTransport, no network.
From backend/: python tests/test_search_provider_health.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'search-provider-health-secret-0123456789')

KEY = 'monid_live_secret_key_value'
WALLET = {'errorCode': 'WALLET_INSUFFICIENT_FUNDS', 'message': 'Not enough wallet balance to run this'}


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        os.environ['MONID_API_KEY'] = KEY
        os.environ.pop('FIRECRAWL_URL', None)
        os.environ.pop('WEB_SEARCH_CACHE_TTL_HOURS', None)
        os.environ['MONID_SEARCHES_PER_MINUTE'] = '1000'

        from app.database import init_db
        from app.services import web_contact_discovery as W
        from app.services import web_fetch as F
        asyncio.run(init_db())

        calls: list[str] = []
        reply: dict = {}
        real_client = httpx.AsyncClient

        def monid(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if reply.get('timeout'):
                raise httpx.ReadTimeout('slow', request=request)
            return httpx.Response(reply['status'], json=reply['body'])

        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(monid), **kwargs)

        async def search(query: str):
            results = await F.web_search(query, max_results=1)
            return results, F.last_search_failure(), F.last_search_failure_kind()

        def ask(query: str):
            with patch('app.services.web_fetch.httpx.AsyncClient', client):
                return asyncio.run(search(query))

        ok = {'status': 200, 'body': {'runId': 'R', 'status': 'COMPLETED',
                                     'output': {'results': [{'title': 't', 'url': 'https://e.com/1', 'snippet': 's'}]}}}
        empty = {'status': 200, 'body': {'runId': 'R', 'status': 'COMPLETED', 'output': {'results': []}}}

        # --- Never used: nothing is claimed about the provider. ---
        F.reset_monid_health()
        assert F.monid_health()['state'] == 'unknown'

        # --- A search that ran and found nobody is empty AND successful. ---
        reply.update(empty)
        results, failure, kind = ask('nobody at acme one')
        assert results == [] and failure is None and kind is None, (results, failure, kind)
        health = F.monid_health()
        assert health['state'] == 'working' and health['message'] is None and health['last_ok_at'], health

        # --- Each provider failure is a failure of its kind, not an empty answer. ---
        cases = [
            (402, WALLET, F.OUT_OF_CREDIT),
            (401, {'code': 401, 'message': 'Invalid API key'}, F.AUTH),
            (403, {'message': 'forbidden'}, F.AUTH),
            (429, {'message': 'slow down'}, F.RATE_LIMITED),
            (500, {'message': 'upstream down'}, F.FAILING),
        ]
        for status, body, expected in cases:
            F.reset_monid_health()
            reply.clear()
            reply.update(status=status, body=body)
            results, failure, kind = ask(f'failing {status}')
            assert results == [] and failure and kind == expected, (status, results, failure, kind)
            assert F.monid_health()['state'] == expected, (status, F.monid_health())
        F.reset_monid_health()
        reply.clear()
        reply['timeout'] = True
        results, failure, kind = ask('failing timeout')
        assert results == [] and failure and kind == F.TIMEOUT, (results, failure, kind)
        assert F.monid_backoff_kind() is None, 'a timeout is not worth holding calls back for'

        # A working fallback clears a failed primary provider, even when the
        # fallback searched successfully and found zero matches.
        F.reset_monid_health()
        reply.clear()
        reply.update(status=402, body=WALLET)
        os.environ['FIRECRAWL_URL'] = 'https://firecrawl.invalid'
        with patch('app.services.web_fetch.httpx.AsyncClient', client), \
             patch.object(F, '_firecrawl_web_search', AsyncMock(return_value=[])):
            results, failure, kind = asyncio.run(search('fallback found nobody'))
        assert results == [] and failure is None and kind is None, (results, failure, kind)
        os.environ.pop('FIRECRAWL_URL', None)

        # --- 402 holds calls back: a burst of lanes sends one request, not many. ---
        F.reset_monid_health()
        reply.clear()
        reply.update(status=402, body=WALLET)
        calls.clear()
        _, _, kind = ask('lane one')
        assert kind == F.OUT_OF_CREDIT and len(calls) == 1, (kind, calls)
        assert F.monid_backoff_kind() == F.OUT_OF_CREDIT
        for i in range(5):
            results, failure, kind = ask(f'lane later {i}')
            assert results == [] and failure and kind == F.OUT_OF_CREDIT, (results, failure, kind)
        assert len(calls) == 1, f'back-off still sent {len(calls) - 1} more calls'
        assert F.monid_health()['paused_for_s'] > 0

        # Fetches are held back by the same wallet.
        with patch('app.services.web_fetch.httpx.AsyncClient', client):
            assert asyncio.run(F._tinyfish_fetch_page('https://example.com')) is None
        assert len(calls) == 1

        # --- The back-off ends: the next call goes through and decides afresh. ---
        F._monid['blocked_until'] = 0.0
        assert F.monid_backoff_kind() is None
        reply.clear()
        reply.update(ok)
        results, failure, kind = ask('after the wallet is topped up')
        assert results and failure is None and kind is None, (results, failure, kind)
        assert len(calls) == 2 and F.monid_health()['state'] == 'working', (calls, F.monid_health())

        # --- Only a state change is worth a warning: a burst logs once. ---
        F.reset_monid_health()
        reply.clear()
        reply.update(status=402, body=WALLET)
        with patch.object(F.logger, 'warning') as warn:
            for i in range(4):
                F._monid['blocked_until'] = 0.0  # let each call reach Monid
                ask(f'burst {i}')
        assert warn.call_count == 1, warn.call_args_list

        # --- A run of failed searches says so, in one plain sentence. ---
        F.reset_monid_health()
        reply.clear()
        reply.update(status=402, body=WALLET)

        async def run_of_searches():
            run, token = W.begin_search_run()
            try:
                await asyncio.gather(*(W._tavily_search(f'Acme {i} director') for i in range(3)))
            finally:
                W.end_search_run(token)
            return run

        with patch('app.services.web_fetch.httpx.AsyncClient', client):
            run = asyncio.run(run_of_searches())
        summary = W.search_errors_summary(run)
        assert summary and summary['all_failed'] and summary['kind'] == F.OUT_OF_CREDIT, summary
        assert 'out of credit' in summary['message'], summary
        for secret in (KEY, 'WALLET', 'wallet balance'):
            assert secret not in summary['message'], 'the member sentence leaked provider text'

        # A run where some searches answered is not "unavailable".
        assert W.search_errors_summary({'queries': 4, 'failed': 1, 'kind': F.TIMEOUT, 'limit': False})['all_failed'] is False
        assert W.search_errors_summary({'queries': 4, 'failed': 0}) is None

        # The worst failure of a run is the one reported.
        run, token = W.begin_search_run()
        try:
            W._record_search_error('slow', limit=False, kind=F.TIMEOUT)
            W._record_search_error('empty wallet', limit=True, kind=F.OUT_OF_CREDIT)
            W._record_search_error('500', limit=False, kind=F.FAILING)
        finally:
            W.end_search_run(token)
        run['queries'] = 3
        assert W.search_errors_summary(run)['kind'] == F.OUT_OF_CREDIT

        # --- What a run says and what the API passes on. ---
        from app.routers.yucgoutreach import _member_research_json
        from app.services.yucgoutreach_discovery import _search_unavailable
        assert _search_unavailable(summary) == summary['message']
        assert _search_unavailable(None) == '' and _search_unavailable({'all_failed': False}) == ''
        import json
        stored = json.dumps({'search_errors': {**summary, 'first_message': 'tinyfish/search HTTP 402: ' + WALLET['message']}})
        served = json.loads(_member_research_json(stored))['search_errors']
        assert 'first_message' not in served and served['kind'] == F.OUT_OF_CREDIT and served['message'] == summary['message']

        # --- One person found twice is one person, with the better source kept. ---
        from app.services.contact_merge import merge_contacts
        company, domain = 'Acme Corp', 'acme.com'
        site = [{'name': 'Jean Bartik', 'title': 'Director of Operations', 'email': 'jean.bartik@acme.com',
                 'contact_source': 'domain_scrape', 'source_url': 'https://acme.com/team'}]
        web = [{'name': 'Jean Bartik', 'title': 'Director of Operations', 'email': 'jbartik@acme.com',
                'linkedin_url': 'https://www.linkedin.com/in/jean-bartik', 'contact_source': 'web_discovery'}]
        merged = merge_contacts(site, company, domain, None, web_contacts=web)
        assert len(merged) == 1, merged
        assert merged[0]['contact_source'] == 'domain_scrape' and merged[0]['email'] == 'jean.bartik@acme.com', merged

        # --- The admin card reads the state without calling Monid. ---
        calls.clear()
        F.reset_monid_health()
        assert F.monid_health()['state'] == 'unknown'
        os.environ.pop('MONID_API_KEY')
        assert F.monid_health()['state'] == 'unconfigured' and F.monid_health()['configured'] is False
        assert not calls

        os.environ.pop('MONID_SEARCHES_PER_MINUTE', None)


if __name__ == '__main__':
    tests()
    print('search provider health: ok')
