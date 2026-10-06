"""GET /api/yucgoutreach/resolve-company: what a member typed or pasted
becomes one company. Pinned here: LinkedIn is never fetched (one search
restricted to linkedin.com, else the slug); a numeric slug cannot name a
company; a name prefers the club's own records, then the register (brand
aliases included), then the text as typed; a domain is null unless verified;
a quota hit still returns the name; repeats are served from the cache.
The response is a superset of that contract: "country" and "entity" (the
company_entity profile), and a known entity's offshoots as alternatives.
All network is mocked.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'company-resolve-secret-xxxxxxxxxxxxxxxx')

CONTRACT_KEYS = {'name', 'domain', 'domain_verified', 'linkedin_url', 'source', 'alternatives'}
ENTITY_KEYS = {'country', 'entity'}


def slug_names() -> None:
    from app.services.company_resolve import _slug_display_name
    # Short handles are acronyms; longer ones read as words.
    assert _slug_display_name('hbo') == 'HBO'
    assert _slug_display_name('ibm') == 'IBM'
    assert _slug_display_name('meta') == 'Meta'
    assert _slug_display_name('warner-bros-discovery') == 'Warner Bros Discovery'
    assert _slug_display_name('1035') is None


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from fastapi import HTTPException
        from fastapi.testclient import TestClient
        from app.database import get_db, init_db
        from app.jwt_utils import create_token
        from app.services import company_email_cache as cec
        from app.services import company_entity as ce
        from app.services import company_register as cr
        from app.services import company_resolve as res
        from app.services import email_verifier as ev
        from app.services import web_fetch as wf
        import main

        asyncio.run(init_db())

        async def seed() -> None:
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO users (id,email,name,role,is_active) VALUES (1,'a@yale.edu','A','standard',1)"
                )
                for i, (company, domain) in enumerate([
                    ('Acme Widgets, Inc.', 'acmewidgets.com'),
                    ('ACME WIDGETS', 'acmewidgets.com'),
                    ('Acme Widgets Europe', None),
                    ('Globex', None),
                ]):
                    await db.execute(
                        'INSERT INTO contacts (name,email,company,company_domain) VALUES (?,?,?,?)',
                        (f'P{i}', f'p{i}@x.com', company, domain),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())
        asyncio.run(cr.upsert_companies([
            {'source': 'sec_public', 'source_key': '1437107', 'tier': 'us_public', 'country': 'US',
             'company_name': 'Warner Bros. Discovery, Inc.', 'prominence_rank': 180,
             'metadata': {'ticker': 'WBD'}},
            {'source': 'companies_house', 'source_key': 'U1', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Hbo Film & Television Development Limited', 'last_event_at': '2026-08-30'},
        ]))

        searches: list[tuple[str, int | None]] = []
        search_results: dict[str, list[dict]] = {}
        search_error: list[Exception] = []

        async def fake_search(query, max_results=8, *, user_id=None):
            searches.append((query, user_id))
            if search_error:
                raise search_error[0]
            for needle, results in search_results.items():
                if needle in query:
                    return results
            return []

        discovered: list[str] = []
        domain_answers: dict[str, str] = {}

        async def fake_discover(name):
            discovered.append(name)
            return domain_answers.get(name, '')

        async def never_fetch(*args, **kwargs):
            raise AssertionError('LinkedIn must never be fetched')

        async def fake_mx(domain, cache=None):
            return (domain == 'mail-ok.org', [])

        async def no_entity_network(url, params, timeout):
            # Wikidata knows none of these test companies; GLEIF has no record.
            return {'search': []} if 'wikidata.org/w/api.php' in url else None

        def run(q):
            return asyncio.run(res.resolve_company(q, user_id=1))

        with patch.object(wf, 'web_search', fake_search), \
                patch.object(wf, 'fetch_page', never_fetch), \
                patch.object(cec, 'discover_company_domain', fake_discover), \
                patch.object(ev, 'get_mx_cached', fake_mx), \
                patch.object(ce, '_http_get_json', no_entity_network):

            # --- LinkedIn URL: the indexed title names the company --------
            search_results['linkedin.com/company/hbo'] = [
                # A neighbouring page in the same results is ignored.
                {'title': 'HBO Max | LinkedIn', 'url': 'https://www.linkedin.com/showcase/hbo-max/'},
                {'title': 'HBO | LinkedIn', 'url': 'https://www.linkedin.com/company/hbo'},
            ]
            domain_answers['HBO'] = 'hbo.com'
            out = run('https://www.linkedin.com/company/hbo/')
            assert set(out) == CONTRACT_KEYS | ENTITY_KEYS, out
            assert {k: out[k] for k in CONTRACT_KEYS} == {
                'name': 'HBO', 'domain': 'hbo.com', 'domain_verified': True,
                'linkedin_url': 'https://www.linkedin.com/company/hbo',
                'source': 'linkedin', 'alternatives': []}, out
            # HBO is a reviewed entity of its own: the domain is its mail domain.
            assert out['country'] == 'US' and out['entity']['mail_domain'] == 'hbo.com', out
            # One search, restricted to linkedin.com, on this member's quota.
            assert len(searches) == 1 and searches[0] == ('site:linkedin.com linkedin.com/company/hbo', 1), searches
            # The same page pasted again (any spelling of it) is served from
            # the cache, not another search.
            assert run('linkedin.com/company/HBO?trk=feed') == out
            assert len(searches) == 1, searches

            # No search hit: a vanity slug reads as the name; query string dropped.
            out = run('linkedin.com/company/warner-bros-discovery?trk=x')
            assert out['name'] == 'Warner Bros Discovery' and out['source'] == 'linkedin', out
            assert out['linkedin_url'] == 'https://www.linkedin.com/company/warner-bros-discovery', out
            # A reviewed entity: its mail domain, and the domain equals it.
            assert out['domain'] == 'wbd.com' == out['entity']['mail_domain'], out
            out = run('linkedin.com/company/initech-labs')
            # The resolver found nothing it could verify: null, never a guess.
            assert out['domain'] is None and out['domain_verified'] is False, out
            assert out['entity']['source'] == 'heuristic' and out['country'] is None, out

            # Showcase pages keep their own path.
            out = run('www.linkedin.com/showcase/hbo-max/about/')
            assert out['name'] == 'Hbo Max' and out['linkedin_url'] == 'https://www.linkedin.com/showcase/hbo-max', out

            # A numeric id with no search hit cannot name the company.
            try:
                run('https://www.linkedin.com/company/1234567/')
                raise AssertionError('numeric slug must be refused')
            except HTTPException as exc:
                assert exc.status_code == 422 and 'company name' in exc.detail, exc.detail
            # A numeric id that search does know resolves normally.
            search_results['linkedin.com/company/7654321'] = [
                {'title': 'Globex Corporation: Overview | LinkedIn', 'url': 'https://www.linkedin.com/company/7654321/'},
            ]
            assert run('https://www.linkedin.com/company/7654321')['name'] == 'Globex Corporation'
            # A profile link is not a company page.
            try:
                run('https://www.linkedin.com/in/jane-doe')
                raise AssertionError('profile link must be refused')
            except HTTPException as exc:
                assert exc.status_code == 422

            # Quota hit: the slug still names the company; no domain lookup
            # (that is more metered work), and nothing is cached.
            search_error.append(HTTPException(429, 'Web search limit reached.'))
            before_discover = len(discovered)
            before_search = len(searches)
            out = run('https://linkedin.com/company/initech-software')
            assert out['name'] == 'Initech Software' and out['domain'] is None and not out['domain_verified'], out
            assert len(discovered) == before_discover
            run('https://linkedin.com/company/initech-software')
            assert len(searches) == before_search + 2, 'a quota-degraded answer must not be cached'
            search_error.clear()

            # --- Names --------------------------------------------------
            # The club's own record wins, with its stored domain.
            before_discover = len(discovered)
            out = run('acme widgets')
            assert out['source'] == 'club' and out['name'] == 'Acme Widgets, Inc.', out
            assert out['domain'] == 'acmewidgets.com' and out['domain_verified'] is True, out
            assert len(discovered) == before_discover  # stored domain, no lookup
            assert {'name': 'Acme Widgets Europe', 'domain': None} in out['alternatives'], out
            assert out['linkedin_url'] is None

            # A reviewed brand is its own entity; the register's owner of the
            # brand alias stays one click away as an alternative.
            out = run('HBO')
            assert out['source'] == 'register' and out['name'] == 'HBO', out
            assert out['domain'] == 'hbo.com' and out['domain_verified'] is True, out
            assert {'name': 'Warner Bros. Discovery, Inc.', 'domain': None} in out['alternatives'], out
            assert {'name': 'Hbo Film & Television Development Limited', 'domain': None} in out['alternatives'], out
            # The dotted name goes to the resolver without its dots, or the
            # resolver would echo "warner bros. discovery" back as a domain.
            assert asyncio.run(res._verified_domain('Warner Bros. Discovery, Inc.')) is None
            assert discovered[-1] == 'Warner Bros Discovery, Inc', discovered

            # Nothing matches: kept as typed; a non-hostname answer is refused.
            domain_answers['Zyxw Labs'] = 'zyxw labs'
            out = run('Zyxw   Labs')
            assert {k: out[k] for k in CONTRACT_KEYS} == {
                'name': 'Zyxw Labs', 'domain': None, 'domain_verified': False,
                'linkedin_url': None, 'source': 'typed', 'alternatives': []}, out
            assert out['country'] is None and out['entity']['display_name'] == 'Zyxw Labs', out
            # A dotted single word comes back from the resolver unchecked, so
            # it must accept mail before it counts.
            domain_answers['bad-mail.org'] = 'bad-mail.org'
            domain_answers['mail-ok.org'] = 'mail-ok.org'
            assert run('bad-mail.org')['domain'] is None
            assert run('mail-ok.org')['domain'] == 'mail-ok.org'

            # --- Route: auth and the exact contract ----------------------
            client = TestClient(main.app)
            assert client.get('/api/yucgoutreach/resolve-company', params={'q': 'globex'}).status_code == 401
            client.cookies.set('yucg_session', create_token(1, 'a@yale.edu', 'A', None, 'standard'))
            resp = client.get('/api/yucgoutreach/resolve-company', params={'q': 'globex'})
            assert resp.status_code == 200, resp.text[:300]
            body = resp.json()
            assert set(body) >= CONTRACT_KEYS | ENTITY_KEYS and body['source'] == 'club' and body['name'] == 'Globex', body
            resp = client.get('/api/yucgoutreach/resolve-company',
                              params={'q': 'https://www.linkedin.com/company/99999'})
            assert resp.status_code == 422 and 'company name' in resp.json()['detail'], resp.text


if __name__ == '__main__':
    slug_names()
    tests()
    print('company resolve: ok')
