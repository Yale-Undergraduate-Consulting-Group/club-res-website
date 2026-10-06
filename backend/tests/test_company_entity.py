"""company_entity: a company name resolves to one specific entity, and
"Barclays" means Barclays PLC in London at barclays.com, never the Indian
service centre at barclays.bank.in (they share one MX tenant, so MX cannot
tell them apart). Pinned here: the reviewed override, the Wikidata + GLEIF
path reaching the same answer on evidence, the mail-domain scoring, a target
country flipping the domain, stored patterns on an offshoot's domain not
answering for the parent, a subsidiary typed by name as its own entity,
failed sources degrading without raising, the resolve-company superset
contract, and a run carrying a size-capped entity. All network is mocked.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'company-entity-secret-xxxxxxxxxxxxxxxxxx')

CONTRACT_KEYS = {'name', 'domain', 'domain_verified', 'linkedin_url', 'source', 'alternatives'}
PROFILE_KEYS = {'legal_name', 'display_name', 'brand_words', 'hq_country', 'hq_city', 'mail_domain',
                'mail_domain_evidence', 'alt_mail_domains', 'exclude', 'target_country', 'source'}

# What Wikidata and GLEIF said for Barclays on 2026-09-22, trimmed.
WIKI_SEARCH = {'search': [
    {'id': 'Q2886011', 'label': 'Barclays Center'},
    {'id': 'Q245343', 'label': 'Barclays', 'description': 'British multinational bank'},
]}


def _item(label, **claims):
    """A wbgetentities entity: {prop: [values]}; Q-ids become item values."""
    def snak(v):
        value = {'id': v} if isinstance(v, str) and v[:1] == 'Q' and v[1:].isdigit() else v
        return {'mainsnak': {'datavalue': {'value': value}}, 'rank': 'normal'}
    return {'labels': {'en': {'value': label}} if label else {},
            'claims': {p: [snak(v) for v in vs] for p, vs in claims.items()}}


# Wikidata lists Barclays Bank PLC's LEI before Barclays PLC's; the HQ items
# are a building and London; P355 has the Indian centre and Barclays UK.
WIKI_ENTITIES = {
    'Q245343': _item('Barclays', P856=['https://home.barclays/', 'https://www.barclays.co.uk/'],
                     P159=['Q138768', 'Q84'], P17=['Q145'],
                     P1278=['G5GSEF7VJP5I7OUK5573', '213800LBQA1Y9L22JB70'], P355=['Q1', 'Q2']),
    'Q2886011': _item('Barclays Center', P159=['Q18419']),
    'Q138768': _item('1 Churchill Place', P17=['Q145']),
    'Q84': _item('London', P17=['Q145'], P1082=[8800000]),
    'Q145': _item('United Kingdom', P297=['GB']),
    'Q668': _item('India', P297=['IN']),
    'Q1': _item('Barclays Global Service Centre', P17=['Q668'], P856=['https://www.barclays.bank.in/']),
    'Q2': _item('Barclays UK', P17=['Q145'], P856=['https://www.barclays.co.uk/']),
}
GLEIF_RECORD = {'data': {'id': '213800LBQA1Y9L22JB70', 'attributes': {'entity': {
    'legalName': {'name': 'BARCLAYS PLC'}, 'category': 'GENERAL',
    'headquartersAddress': {'country': 'GB', 'city': 'LONDON'}}}}}
GLEIF_BANK = {'data': {'id': 'G5GSEF7VJP5I7OUK5573', 'attributes': {'entity': {
    'legalName': {'name': 'Barclays Bank PLC'}, 'category': 'GENERAL',
    'headquartersAddress': {'country': 'GB', 'city': 'LONDON'}}}}}


def _child(name, country, category='GENERAL'):
    return {'attributes': {'entity': {'legalName': {'name': name}, 'category': category,
                                      'headquartersAddress': {'country': country}}}}


GLEIF_CHILDREN = {'data': [
    _child('Barclays Bank PLC', 'GB'),
    _child('BARCLAYS GLOBAL SERVICE CENTRE PRIVATE LIMITED', 'IN'),
    _child('Barclays Bank Delaware', 'US'),
    _child('Barclays Nominees (Jersey) Limited', 'JE'),
    _child('Barclays Global Fund', 'LU', 'FUND'),
    _child('Joy Group Limited', 'JE'),
    _child('Electronic Data Process Mexico', 'MX'),
]}
# The citation search: Barclays' own broker page and vendor pages.
CITATIONS = [
    {'title': 'Contact us | Barclays', 'url': 'https://www.barclays.co.uk/business-banking/brokers/contact-us/',
     'content': 'Email christian.rudbeck@barclays.com for broker enquiries.'},
    {'title': 'Barclays Email Format', 'url': 'https://rocketreach.co/barclays-email-format',
     'content': 'first.last@barclays.com (ex. jane.doe@barclays.com)'},
    {'title': 'Barclays email', 'url': 'https://leadiq.com/c/barclays', 'content': 'jane.doe@barclays.com'},
    {'title': 'Barclays India', 'url': 'https://example.in', 'content': 'careers@barclays.bank.in'},
]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from fastapi.testclient import TestClient
        from app.database import get_db, init_db
        from app.jwt_utils import create_token
        from app.routers import yucgoutreach as router
        from app.services import company_email_cache as cec
        from app.services import company_entity as ce
        from app.services import company_resolve as res
        from app.services import email_verifier as ev
        from app.services import web_fetch as wf
        import main as app_main

        asyncio.run(init_db())

        calls: list[str] = []
        net_mode = ['barclays']

        async def fake_http(url, params, timeout):
            calls.append(url)
            if net_mode[0] == 'hang':
                await asyncio.sleep(5)
            if net_mode[0] == 'error':
                raise RuntimeError('503 from upstream')
            if 'w/api.php' in url and params['action'] == 'wbsearchentities':
                return WIKI_SEARCH if 'barclays' in params['search'].lower() else {'search': []}
            if 'w/api.php' in url:
                ids = params['ids'].split('|')
                assert 'Q2886011' not in ids, 'an arena named Barclays is not the bank'
                return {'entities': {i: WIKI_ENTITIES[i] for i in ids if i in WIKI_ENTITIES}}
            if url.endswith('/213800LBQA1Y9L22JB70/direct-children'):
                return GLEIF_CHILDREN
            if url.endswith('/direct-children'):
                raise AssertionError('children of the wrong LEI: ' + url)
            if url.endswith('/lei-records'):
                # GLEIF's name filter is fuzzy: what it returned live for "Barclays".
                return {'data': [
                    {'id': 'F1', **_child('BARCLAYS ALTERNATIVES - BARCLAYS QUARTERLY HEDGE', 'LU', 'FUND')},
                    {'id': 'F2', **_child('BARCLAYS FUNDS - BARCLAYS EQUITY ASIA', 'LU')},
                    {'id': '213800LBQA1Y9L22JB70', **GLEIF_RECORD['data']},
                ]}
            if url.endswith('/lei-records/213800LBQA1Y9L22JB70'):
                return GLEIF_RECORD
            if url.endswith('/lei-records/G5GSEF7VJP5I7OUK5573'):
                return GLEIF_BANK
            return None

        searches: list[tuple[str, int | None]] = []

        async def fake_search(query, max_results=8, *, user_id=None):
            searches.append((query, user_id))
            return CITATIONS if 'barclays' in query else []

        async def fake_mx(domain, cache=None):
            # One Proofpoint tenant for every Barclays mail domain; the
            # website home.barclays has no MX (its A record stands in).
            if domain == 'home.barclays':
                return True, ['home.barclays']
            if domain.startswith('barclays.') or domain.endswith('.co.uk'):
                return True, ['mxa-00146204.gslb.pphosted.com']
            return False, []

        def fresh():
            ce._cache.clear()
            res._cache.clear()
            calls.clear()
            searches.clear()

        def without_override(key):
            saved = ce.ENTITY_OVERRIDES.pop(key)
            ce._OVERRIDE_INDEX.clear()
            return saved

        def restore_override(key, saved):
            ce.ENTITY_OVERRIDES[key] = saved
            ce._OVERRIDE_INDEX.clear()

        with patch.object(ce, '_http_get_json', fake_http), \
                patch.object(wf, 'web_search', fake_search), \
                patch.object(ev, 'get_mx_cached', fake_mx):

            # --- domains ---------------------------------------------------
            assert ce.registrable_domain('https://mail.barclays.co.uk/x') == 'barclays.co.uk'
            assert ce.registrable_domain('www.barclays.bank.in') == 'barclays.bank.in'
            assert ce.registrable_domain('in.ibm.com') == 'ibm.com'
            assert ce.registrable_domain('not a host') == ''
            assert ce.domain_country('barclays.co.uk') == 'GB'
            assert ce.domain_country('barclays.bank.in') == 'IN'
            assert ce.domain_country('barclays.com') is None and ce.domain_country('acme.io') is None

            # --- the reviewed override: no network at all -----------------
            fresh()
            p = asyncio.run(ce.resolve_entity('Barclays', user_id=1))
            assert set(p) == PROFILE_KEYS, p
            assert p['legal_name'] == 'Barclays PLC' and p['hq_country'] == 'GB' and p['hq_city'] == 'London', p
            assert p['mail_domain'] == 'barclays.com' and p['source'] == 'override', p
            assert {'domain': 'barclays.co.uk', 'country': 'GB'} in p['alt_mail_domains'], p
            gsc = next(e for e in p['exclude'] if e['name'] == 'Barclays Global Service Centre')
            assert gsc == {'name': 'Barclays Global Service Centre', 'country': 'IN',
                           'domains': ['barclays.bank.in'], 'kind': 'captive'}, gsc
            assert p['target_country'] == 'GB'
            assert calls == [] and searches == [], (calls, searches)
            assert asyncio.run(ce.resolve_entity('barclays plc'))['mail_domain'] == 'barclays.com'

            # --- the same answer on evidence, with no override ------------
            fresh()
            saved = without_override('barclays')
            try:
                p = asyncio.run(ce.resolve_entity('Barclays', user_id=7))
                assert p['source'] == 'wikidata', p
                assert p['legal_name'] == 'BARCLAYS PLC' and p['hq_country'] == 'GB' and p['hq_city'] == 'London', p
                # Wikidata only lists home.barclays (no real MX) and
                # barclays.co.uk; the citations bring barclays.com and win.
                assert p['mail_domain'] == 'barclays.com' and p['mail_domain_evidence'] == 3, p
                assert {'domain': 'barclays.co.uk', 'country': 'GB'} in p['alt_mail_domains'], p
                names = {e['name']: e for e in p['exclude']}
                assert names['Barclays Global Service Centre']['domains'] == ['barclays.bank.in'], names
                assert names['Barclays Global Service Centre']['kind'] == 'captive', names
                assert 'BARCLAYS GLOBAL SERVICE CENTRE PRIVATE LIMITED' not in names, 'same centre twice'
                assert names['Barclays Bank Delaware']['country'] == 'US', names
                # Home-country children, nominees and funds are not offshoots.
                assert not any(n in names for n in ('Barclays UK', 'Barclays Bank PLC', 'Barclays Global Fund',
                                                     'Barclays Nominees (Jersey) Limited')), names
                # A child with neither the brand nor a captive's wording is noise;
                # an unbranded processing centre is still a captive.
                assert 'Joy Group Limited' not in names, names
                assert names['Electronic Data Process Mexico']['kind'] == 'captive', names
                # One citation search, on the member's quota.
                assert len(searches) == 1 and searches[0][1] == 7, searches
                # Cached for the week: the second lookup spends nothing.
                n_calls = len(calls)
                asyncio.run(ce.resolve_entity('Barclays', user_id=7))
                assert len(calls) == n_calls and len(searches) == 1
                # Without a member there is no quota to charge, so no search.
                fresh()
                p = asyncio.run(ce.resolve_entity('Barclays'))
                assert searches == [] and p['mail_domain'] == 'barclays.co.uk', p
            finally:
                restore_override('barclays', saved)

            # GLEIF by name alone takes only the exact legal name, not the
            # first fuzzy hit (a fund's umbrella, live on 2026-09-22).
            g = asyncio.run(ce._gleif([], 'Barclays', ce._Budget(5)))
            assert g['legal_name'] == 'BARCLAYS PLC' and g['hq_country'] == 'GB', g
            assert asyncio.run(ce._gleif([], 'Barclays Equity', ce._Budget(5))) is None

            # --- choose_mail_domain --------------------------------------
            cands = ['barclays.bank.in', 'barclays.co.uk', 'barclays.com']
            excl = [{'name': 'Barclays Global Service Centre', 'country': 'IN',
                     'domains': ['barclays.bank.in'], 'kind': 'captive'}]
            alt = [{'domain': 'barclays.co.uk', 'country': 'GB'}]
            cites = {'barclays.com': 2, 'barclays.bank.in': 4}
            assert ce.choose_mail_domain(cands, 'Barclays', None, hq_country='GB', exclude=excl,
                                         alt_mail_domains=alt, citations=cites) == ('barclays.com', 2)
            # Even with no exclude list, a ccTLD with more citations loses to
            # the brand .com once both have some.
            assert ce.choose_mail_domain(cands, 'Barclays', 'GB', hq_country='GB',
                                         citations={'barclays.com': 1, 'barclays.co.uk': 1})[0] == 'barclays.com'
            # An excluded offshoot's domain is never the parent's, alone or not.
            assert ce.choose_mail_domain(['barclays.bank.in'], 'Barclays', 'GB', hq_country='GB',
                                         exclude=excl) == (None, 0)
            # Targeting India flips to the Indian domain.
            assert ce.choose_mail_domain(cands, 'Barclays', 'IN', hq_country='GB', exclude=excl,
                                         alt_mail_domains=alt, citations=cites)[0] == 'barclays.bank.in'
            # A stored verified pattern is evidence too.
            # A stored verified pattern (+3) outweighs the brand .com (+2).
            assert ce.choose_mail_domain(['acme.com', 'acme.net'], 'Acme', 'US', hq_country='US',
                                         stored={'acme.net'})[0] == 'acme.net'

            fresh()
            p = asyncio.run(ce.resolve_entity('Barclays', target_country='in'))
            assert p['mail_domain'] == 'barclays.bank.in' and p['target_country'] == 'IN', p
            assert not any(e['country'] == 'IN' for e in p['exclude']), 'the target is not excluded'
            p = asyncio.run(ce.resolve_entity('Barclays', target_country='*'))
            assert p['mail_domain'] == 'barclays.com' and p['target_country'] == '*', p

            # --- a subsidiary typed by name is its own entity -------------
            p = asyncio.run(ce.resolve_entity('Barclays Global Service Centre Private Limited'))
            assert p['display_name'] == 'Barclays Global Service Centre' and p['hq_country'] == 'IN', p
            assert p['mail_domain'] == 'barclays.bank.in' and p['target_country'] == 'IN', p
            assert p['exclude'] == [] and p['source'] == 'override', p
            # A captive with no domain of its own mails from the parent's.
            assert asyncio.run(ce.resolve_entity('Goldman Sachs Services India'))['mail_domain'] == 'gs.com'

            # --- stored patterns: an offshoot's domain never answers ------
            async def seed_patterns(rows):
                db = await get_db()
                try:
                    await db.execute('DELETE FROM company_email_patterns')
                    for domain, name, verified in rows:
                        await db.execute(
                            """INSERT INTO company_email_patterns (company_domain, company_name, pattern_key,
                               pattern_template, verified_samples) VALUES (?,?,?,?,?)""",
                            (domain, name, 'first.last', '{first}.{last}', verified))
                    await db.commit()
                finally:
                    await db.close()

            asyncio.run(seed_patterns([('barclays.bank.in', 'Barclays', 40)]))
            assert asyncio.run(cec.resolve_company_domain('Barclays')) == ''
            asyncio.run(seed_patterns([('barclays.bank.in', 'Barclays', 40), ('barclays.co.uk', 'Barclays', 9),
                                       ('barclays.com', 'Barclays', 3)]))
            assert asyncio.run(cec.resolve_company_domain('Barclays')) == 'barclays.com'
            # A company the table does not know keeps its only (UK) domain.
            asyncio.run(seed_patterns([('zenwidgets.co.uk', 'Zen Widgets', 2)]))
            assert asyncio.run(cec.resolve_company_domain('zenwidgets')) == 'zenwidgets.co.uk'
            # discover_company_domain answers from the reviewed entity first.
            assert asyncio.run(cec.discover_company_domain('Barclays')) == 'barclays.com'

            # discover ranks every verified hit instead of taking the first.
            async def hits(query, max_results=5):
                return [{'url': 'https://www.zenbank.co.uk/'}, {'url': 'https://zenbank.com/about'}]

            async def all_mx(domain, cache=None):
                return True, ['mx.' + domain]

            with patch('app.services.web_contact_discovery._tavily_search', hits), \
                    patch('app.services.web_fetch.web_search_configured', lambda: True), \
                    patch.object(ev, 'get_mx_cached', all_mx):
                assert asyncio.run(cec.discover_company_domain('Zen Bank')) == 'zenbank.com'

            # --- failed sources degrade, never raise ----------------------
            for mode in ('hang', 'error'):
                fresh()
                net_mode[0] = mode
                with patch.object(ce, '_TOTAL_BUDGET_S', 0.6), patch.object(ce, '_HTTP_TIMEOUT_S', 0.2):
                    p = asyncio.run(ce.resolve_entity('Initech Holdings', user_id=1))
                    assert p['source'] == 'heuristic' and p['display_name'] == 'Initech Holdings', p
                    assert p['mail_domain'] is None and p['hq_country'] is None, p
                    # A degraded answer is not cached for the week.
                    before = len(calls)
                    asyncio.run(ce.resolve_entity('Initech Holdings', user_id=1))
                    assert len(calls) > before, mode
                    # The override needs no network, so it still answers.
                    assert asyncio.run(ce.resolve_entity('HSBC'))['mail_domain'] == 'hsbc.com'
            net_mode[0] = 'barclays'

            # --- resolve-company: superset contract ------------------------
            fresh()
            out = asyncio.run(res.resolve_company('Barclays', user_id=1))
            assert set(out) >= CONTRACT_KEYS | {'country', 'entity'}, out
            assert out['name'] == 'Barclays' and out['country'] == 'GB', out
            assert out['domain'] == out['entity']['mail_domain'] == 'barclays.com' and out['domain_verified'], out
            gsc = next(a for a in out['alternatives'] if a['name'] == 'Barclays Global Service Centre')
            assert gsc['domain'] == 'barclays.bank.in' and gsc['country'] == 'IN' and gsc['kind'] == 'captive', gsc
            assert gsc['entity']['target_country'] == 'IN' and gsc['entity']['mail_domain'] == 'barclays.bank.in', gsc
            region = next(a for a in out['alternatives'] if a.get('kind') == 'region')
            assert region['domain'] == 'barclays.co.uk' and region['country'] == 'GB', region
            assert region['entity']['mail_domain'] == 'barclays.co.uk', region
            out = asyncio.run(res.resolve_company('Barclays Global Service Centre', user_id=1))
            assert out['name'] == 'Barclays Global Service Centre' and out['country'] == 'IN', out
            assert out['domain'] == 'barclays.bank.in' == out['entity']['mail_domain'], out

            # --- createRun carries the entity, cut to size -----------------
            async def seed_user():
                db = await get_db()
                try:
                    await db.execute("INSERT INTO users (id,email,name,role,is_active) VALUES (1,'a@yale.edu','A','standard',1)")
                    await db.commit()
                finally:
                    await db.close()

            async def research_of(run_id):
                db = await get_db()
                try:
                    row = await (await db.execute(
                        'SELECT research_json FROM yucgoutreach_discovery_runs WHERE id = ?', (run_id,))).fetchone()
                    await db.execute("UPDATE yucgoutreach_discovery_runs SET status='completed' WHERE id = ?", (run_id,))
                    await db.commit()
                    return row['research_json']
                finally:
                    await db.close()

            async def no_drain():
                return None

            asyncio.run(seed_user())
            client = TestClient(app_main.app)
            client.cookies.set('yucg_session', create_token(1, 'a@yale.edu', 'A', None, 'standard'))
            with patch.object(router, '_kick_discovery_drain', no_drain):
                entity = out['entity']
                resp = client.post('/api/yucgoutreach/runs', json={
                    'company_name': 'Barclays Global Service Centre', 'title_hints': 'analyst', 'entity': entity})
                assert resp.status_code == 200, resp.text
                stored = json.loads(asyncio.run(research_of(resp.json()['id'])))
                assert stored == {'title_hints': 'analyst', 'entity': entity}, stored

                huge = {
                    'legal_name': 'X' * 5000, 'display_name': 'Barclays', 'brand_words': ['barclays'] * 50,
                    'hq_country': 'Great Britain', 'mail_domain': 'not a domain', 'mail_domain_evidence': 10 ** 9,
                    'alt_mail_domains': [{'domain': f'b{i}.co.uk', 'country': 'GB'} for i in range(40)],
                    'exclude': [{'name': f'Sub {i}', 'country': 'IN', 'domains': ['a.in'] * 20 + ['bad host'],
                                 'kind': 'evil'} for i in range(200)],
                    'target_country': '*', 'source': 'root', 'injected': {'sql': 'DROP TABLE users'},
                }
                resp = client.post('/api/yucgoutreach/runs', json={'company_name': 'Barclays', 'entity': huge})
                assert resp.status_code == 200, resp.text
                got = json.loads(asyncio.run(research_of(resp.json()['id'])))['entity']
                assert set(got) == PROFILE_KEYS, got
                assert len(got['legal_name']) == 200 and len(got['brand_words']) == 10, got
                assert got['hq_country'] is None and got['mail_domain'] is None, got
                assert got['mail_domain_evidence'] == 100 and got['source'] == 'heuristic', got
                assert len(got['alt_mail_domains']) == 10 and len(got['exclude']) == 30, got
                assert got['exclude'][0]['domains'] == ['a.in'] * 5 and got['exclude'][0]['kind'] == 'subsidiary', got
                assert got['target_country'] == '*', got

                # No entity: the run is exactly as before.
                resp = client.post('/api/yucgoutreach/runs', json={'company_name': 'Globex'})
                assert resp.status_code == 200 and asyncio.run(research_of(resp.json()['id'])) is None


if __name__ == '__main__':
    main()
    print('company entity: ok')
