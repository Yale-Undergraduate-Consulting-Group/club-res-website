"""The register as companies, not records: public names, one entry per company,
hidden clutter, the records log, and the backfill that derives all of it.

The name examples are real register rows (see docs/product/OUTREACH-PIPELINE.md).
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'register-entities-secret-xxxxxxxxxxxxxx')


def name_tests() -> None:
    from app.services.register_names import derive, hidden_reason, public_name

    # (as filed, country, public name, entity key)
    table = [
        ('Apple Inc.', 'US', 'Apple', 'US:apple'),
        ('Apple (Uk) Limited', 'GB', 'Apple (UK)', 'GB:apple uk'),
        ('Ireland Home Based Services, Llc', 'US', 'Ireland Home Based Services', 'US:ireland home based services'),
        ('Suburban Propane Partners Lp', 'US', 'Suburban Propane Partners', 'US:suburban propane partners'),
        ('Suburban Propane Partners, L.P.', 'US', 'Suburban Propane Partners', 'US:suburban propane partners'),
        ('BOY SCOUTS OF AMERICA', 'US', 'Boy Scouts of America', 'US:boy scouts of america'),
        ('Boy Scouts Of America', 'US', 'Boy Scouts of America', 'US:boy scouts of america'),
        ('The Home Depot, Inc.', 'US', 'Home Depot', 'US:home depot'),
        ('Procter & Gamble Co', 'US', 'Procter & Gamble', 'US:procter and gamble'),
        ('Procter and Gamble Company', 'US', 'Procter and Gamble', 'US:procter and gamble'),
        ('Smith & Co.', 'US', 'Smith', 'US:smith'),
        ('Foo L.L.C.', 'US', 'Foo', 'US:foo'),
        ('Foo L L C', 'US', 'Foo', 'US:foo'),
        ('Foo Corp /DE/', 'US', 'Foo', 'US:foo'),
        ('AT&T INC', 'US', 'AT&T', 'US:at and t'),
        ('Coca-Cola Company', 'US', 'Coca-Cola', 'US:coca cola'),
        ("MACY'S INC", 'US', "Macy's", 'US:macys'),
        ('Amazon.com, Inc.', 'US', 'Amazon', 'US:amazon'),
        ('IBEW LOCAL 134', 'US', 'IBEW Local 134', 'US:ibew local 134'),
        ('Ibew Local 134', 'US', 'IBEW Local 134', 'US:ibew local 134'),
        ('U.S. Steel Corp', 'US', 'U.S. Steel', 'US:us steel'),
        ('3M CO', 'US', '3M', 'US:3m'),
        ('Bank Of America Corporation', 'US', 'Bank of America', 'US:bank of america'),
        ('Habitat For Humanity International Inc', 'US', 'Habitat for Humanity International',
         'US:habitat for humanity international'),
        # A name made only of legal forms is kept, never emptied.
        ('Company Inc', 'US', 'Company', 'US:company'),
    ]
    for legal, country, public, key in table:
        got = derive(legal, country)
        assert (got.display_name, got.entity_key) == (public, key), (legal, got)
    # The same name in another country is another company.
    assert derive('Apple Limited', 'GB').entity_key != derive('Apple Inc', 'US').entity_key
    assert public_name('') == ''

    # Reviewed overrides come from company_entity's table, not a second list.
    pwc = derive('PRICEWATERHOUSECOOPERS LLP', 'US')
    assert (pwc.display_name, pwc.entity_key) == ('PwC', 'US:pwc'), pwc
    assert derive('PricewaterhouseCoopers International Limited', 'GB').display_name == 'PwC'
    assert derive('JPMorgan Chase & Co.', 'US').display_name == 'JPMorgan Chase'
    # A one-word spelling never renames: "Chase Inc" is not JPMorgan (Chase
    # Corporation is a separate listed company), so it keeps its own name and
    # entry; an override is never a reason to hide.
    assert derive('Chase Limited', 'GB').display_name == 'Chase'
    assert derive('Chase Inc', 'US').display_name == 'Chase'
    assert derive('Chase Inc', 'US').entity_key != derive('JPMorgan Chase & Co.', 'US').entity_key
    assert derive('Fidelity Inc', 'US').display_name == 'Fidelity'
    assert derive('Alphabet Inc.', 'US').display_name == 'Alphabet'
    assert derive('Barclays Bank PLC', 'GB').display_name == 'Barclays'
    # The curated legal name matches exactly, ending included: Target
    # Corporation is Target; a small "Target Inc" keeps its own (suffix-stripped)
    # name, which suffix stripping makes "Target" as well, but never borrows a
    # different curated name.
    assert derive('Target Corporation', 'US').display_name == 'Target'
    assert derive('Citigroup Inc.', 'US').display_name == 'Citi'
    assert derive('Citi Trends Inc', 'US').display_name == 'Citi Trends'
    assert derive('FMR LLC', 'US').display_name == 'Fidelity Investments'
    assert derive('Shell Lake Inc', 'US').display_name == 'Shell Lake'
    assert derive('Barclays Fund II Lp', 'GB').hidden_reason == 'investment_fund'

    # --- hide rules ---------------------------------------------------------
    hidden = {
        'PIMCO Income Strategy Fund II': 'investment_fund',
        'Robinhood Ventures Fund I': 'investment_fund',
        'Harbor Capital Fund III LP': 'investment_fund',
        'Oak Fund 4 LLC': 'investment_fund',
        'Apple Credit Fund Ltd': 'investment_fund',
        'Nuveen Real Estate Income Fund': 'investment_fund',
        'Foo Opportunity Feeder Fund': 'investment_fund',
        'Meridian Hedge Fund, L.P.': 'investment_fund',
        'WNC Housing Tax Credit Fund VI': 'investment_fund',
        'Apple Bidco 1 Limited': 'acquisition_vehicle',
        'Acme Holdco Ltd': 'acquisition_vehicle',
        'Acme Topco Limited': 'acquisition_vehicle',
        'Acme Midco 2 Limited': 'acquisition_vehicle',
        'Acme Finco Plc': 'acquisition_vehicle',
        'Acme Propco Ltd': 'acquisition_vehicle',
        'Acme Newco Inc': 'acquisition_vehicle',
        'Acme SPV 7 LLC': 'acquisition_vehicle',
        'Kensington Capital Acquisition Corp V': 'acquisition_vehicle',
        'Acme Merger Sub Inc': 'acquisition_vehicle',
        'Capita Nominees Limited': 'nominee_or_trustee',
        'Capita Trustees Limited': 'nominee_or_trustee',
        'Pension Trustee Company Ltd': 'nominee_or_trustee',
        'Credit Unions Chartered In The State Of Michigan': 'group_return',
        'Credit Unions In The State Of Ohio': 'group_return',
        'Affiliated Organizations In The State Of Texas': 'group_return',
        'Church Group Exemption': 'group_return',
    }
    for legal, reason in hidden.items():
        assert hidden_reason(legal) == reason, (legal, hidden_reason(legal))
    # Real operating organisations and look-alikes stay visible: a bare "Fund",
    # "Trustees", "Credit Union" or "Holdings" is never enough.
    visible = [
        'Fund for the Public Interest', 'Trustees of Boston College', 'The Trustees Of Dartmouth College',
        "Children's Defense Fund", 'World Wildlife Fund', 'United Negro College Fund', 'Opportunity Fund',
        'Rockefeller Brothers Fund Inc', 'Apple Ridge Holdings, LLC', 'Credit Union League of Michigan',
        'Council Of Churches In The State Of Ohio', 'Habitat For Humanity International Inc',
        'Boy Scouts Of America', 'Newcomb Foods Inc', 'Topcoat Painting Llc', 'Holdcombe Partners',
        'Fundamental Research Corp', 'Index Medical Limited', 'Trustee Bank Of Boston',
        'Acquisition Strategies Group', 'Apple Inc.',
    ]
    for legal in visible:
        assert hidden_reason(legal) is None, (legal, hidden_reason(legal))


def _row(source: str, key: str, tier: str, name: str, country: str = 'US', **extra) -> dict:
    return {'source': source, 'source_key': key, 'tier': tier, 'country': country, 'company_name': name, **extra}


async def _seed(cr) -> None:
    await cr.upsert_companies([
        # One company in three tiers, and a UK namesake that is another company.
        _row('sec_public', '0000320193', 'us_public', 'Apple Inc.', prominence_rank=3,
             sector_label='Electronic computers', metadata={'ticker': 'AAPL'}),
        _row('dol_5500', '942404110', 'us_employer', 'APPLE INC', employees=90000, region='CA',
             employees_source='form_5500_active_participants',
             last_event_at='2025-01-01', last_event_kind='benefit_plan_filed'),
        _row('sec_form_d', '0000999', 'us_private', 'Apple, Inc.', region='California',
             last_event_at='2026-02-01', last_event_amount=15_000_000, last_event_kind='reg_d_offering',
             metadata={'accession': '0000999-26-000001'}),
        _row('companies_house', '00000001', 'uk', 'Apple (Uk) Limited', country='GB', region='London',
             metadata={'account_category': 'FULL'}),
        # A chapter filed in many states under the parent's name.
        *[_row('irs_990', f'1000{i}', 'us_nonprofit', 'Boy Scouts Of America', region=state,
               employees=10 + i, last_event_at=f'2024-0{i + 1}-01', last_event_kind='form_990_filed',
               metadata={'revenue_range': '$6.0M revenue'})
          for i, state in enumerate(['CT', 'NY', 'TX'])],
        _row('irs_990', '20001', 'us_nonprofit', 'BOY SCOUTS OF AMERICA', region='OH'),
        # PwC: the legal name differs from the public one.
        _row('dol_5500', '133', 'us_employer', 'PRICEWATERHOUSECOOPERS LLP', employees=40000, region='NY'),
        _row('companies_house', '00000002', 'uk', 'Plain Words Limited', country='GB', region='Leeds',
             metadata={'account_category': 'MEDIUM'}),
        # Clutter: funds, vehicles, group returns.
        _row('sec_public', '5', 'us_public', 'PIMCO Income Strategy Fund II', prominence_rank=1),
        _row('companies_house', '00000003', 'uk', 'Apple Bidco 1 Limited', country='GB'),
        _row('irs_990', '30001', 'us_nonprofit', 'Credit Unions Chartered In The State Of Michigan', region='MI'),
        # ...and operating organisations that merely resemble them.
        _row('irs_990', '30002', 'us_nonprofit', 'Fund For The Public Interest', region='NY'),
        _row('irs_990', '30003', 'us_nonprofit', 'The Trustees Of Boston College', region='MA'),
        _row('club_sheet', 'c1', 'club_targets', 'Plain Words Ltd.', country='GB'),
    ])


def list_tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import init_db
        from app.routers import yucgoutreach as router
        from app.services import company_register as cr

        asyncio.run(init_db())
        asyncio.run(_seed(cr))
        user = {'id': 1}

        def browse(**kw):
            kw.setdefault('limit', 50)
            return asyncio.run(cr.browse_register(**kw))

        page = browse()
        names = [i['display_name'] for i in page['items']]
        # One row per company, hidden ones absent, and every record counted.
        assert len(names) == len(set(names)), names
        assert page['total'] == len(names) == 7, names
        assert set(names) == {'Apple', 'Apple (UK)', 'Boy Scouts of America', 'PwC', 'Plain Words',
                              'Fund for the Public Interest', 'Trustees of Boston College'}, names
        by = {i['display_name']: i for i in page['items']}
        apple = by['Apple']
        assert apple['entity_key'] == 'US:apple' and apple['record_count'] == 3
        assert apple['kinds'] == ['us_public', 'us_private', 'us_employer']
        assert apple['locations'] == ['CA']      # 'CA' and 'California' are one place
        assert apple['legal_name'] == 'Apple Inc.'   # the listed row speaks for the company
        scouts = by['Boy Scouts of America']
        assert scouts['record_count'] == 4 and scouts['kinds'] == ['us_nonprofit']
        assert scouts['locations'] == ['CT', 'NY', 'OH', 'TX']
        # Value order: the SEC-ranked company first, then by headcount.
        assert names[0] == 'Apple' and names[1] == 'PwC', names
        # Same name, another country, another company; and a UK company that
        # is also on the club sheet is one entry holding both kinds.
        assert by['Plain Words']['kinds'] == ['uk', 'club_targets'] and by['Plain Words']['country'] == 'GB'

        # A tier filter lists a company when ANY of its records is that tier,
        # and the entry still shows all of its kinds.
        listed = browse(tier='us_employer')
        assert {i['display_name'] for i in listed['items']} == {'Apple', 'PwC'}
        assert next(i for i in listed['items'] if i['display_name'] == 'Apple')['kinds'] == \
            ['us_public', 'us_private', 'us_employer']
        assert listed['total'] == 2
        assert {i['display_name'] for i in browse(tier='uk')['items']} == {'Apple (UK)', 'Plain Words'}
        assert {i['display_name'] for i in browse(tier='club_targets')['items']} == {'Plain Words'}
        assert browse(tier='us_nonprofit')['total'] == 3

        # Hidden entries stay in the data and come back on request.
        everything = browse(include_hidden=True)
        assert everything['total'] == 10
        shown = {i['display_name']: i['hidden_reason'] for i in everything['items'] if i['hidden_reason']}
        assert shown == {
            'PIMCO Income Strategy Fund II': 'investment_fund',
            'Apple Bidco 1': 'acquisition_vehicle',
            'Credit Unions Chartered in the State of Michigan': 'group_return',
        }, shown
        assert browse(q='pimco')['total'] == 0
        assert browse(q='pimco', include_hidden=True)['total'] == 1

        # Pagination walks the same order without repeats.
        walked = []
        for offset in range(0, 7, 3):
            walked += [i['display_name'] for i in browse(limit=3, offset=offset)['items']]
        assert walked == names, (walked, names)

        # Search reads the public name as well as the legal one: "pwc" is not in
        # "PRICEWATERHOUSECOOPERS LLP", and "apple" ranks the company over its UK arm.
        assert [i['display_name'] for i in browse(q='pwc')['items']] == ['PwC']
        assert [i['display_name'] for i in browse(q='apple')['items']] == ['Apple', 'Apple (UK)']
        assert browse(q='AAPL')['items'][0]['display_name'] == 'Apple'
        assert [i['display_name'] for i in browse(q='boy scouts')['items']] == ['Boy Scouts of America']
        assert browse(q='100%')['items'] == []
        assert browse(country='gb')['total'] == 2 + 0   # Apple (UK), Plain Words
        assert browse(sector='electronic')['total'] == 1

        # The newest filing first for the feed.
        recent = [i['display_name'] for i in browse(sort='recent')['items']]
        assert recent[0] == 'Apple', recent

        # People are counted across every record of a company, once each.
        asyncio.run(cr._attach_people({
            ('sec_form_d', '0000999'): [{'full_name': 'Ada Lovelace', 'relationship': 'Executive Officer'}],
            ('dol_5500', '942404110'): [{'full_name': 'Ada Lovelace', 'relationship': 'Signed the plan filing'},
                                        {'full_name': 'Grace Hopper', 'relationship': 'CFO'}],
        }))
        apple = next(i for i in browse()['items'] if i['display_name'] == 'Apple')
        assert apple['officer_count'] == 3 and apple['lookup_id'] is not None   # the listed row has none yet
        assert [i['display_name'] for i in browse(with_officers=True)['items']] == ['Apple']
        people = asyncio.run(router.register_company_people(key='US:apple', user=user))
        assert sorted(p['full_name'] for p in people) == ['Ada Lovelace', 'Grace Hopper']

        # The routes hand the same shapes to the browser.
        routed = asyncio.run(router.browse_register(
            q=None, tier='us_public', country=None, sector=None, min_amount=None, with_officers=False,
            include_hidden=False, limit=10, offset=0, sort='value', user=user))
        assert [i['display_name'] for i in routed['items']] == ['Apple']

        # The summary counts companies, not records, and leaves hidden ones out.
        summary = asyncio.run(router.register_stats(user=user))
        counts = {t['tier']: t['n'] for t in summary['tiers']}
        assert counts == {'us_public': 1, 'us_employer': 2, 'us_private': 1, 'uk': 2,
                          'us_nonprofit': 3, 'club_targets': 1}, counts
        assert summary['companies'] == 7 and summary['with_people'] == 1 and summary['pending'] == 0
        assert {s['sector']: s['n'] for s in summary['sectors']} == {'Electronic computers': 1}


def records_log_tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import init_db
        from app.routers import yucgoutreach as router
        from app.services import company_register as cr

        asyncio.run(init_db())
        asyncio.run(_seed(cr))
        user = {'id': 1}

        log = asyncio.run(router.register_company_records(key='US:apple', user=user))
        assert log['display_name'] == 'Apple' and log['total'] == 3
        lines = log['records']
        # Newest first, one line per source row, the legal name exactly as filed.
        assert [r['source_label'] for r in lines] == ['Reg D filing', 'Benefit plan filing', 'SEC listing']
        assert [r['legal_name'] for r in lines] == ['Apple, Inc.', 'APPLE INC', 'Apple Inc.']
        reg_d = lines[0]
        assert reg_d['region'] == 'California' and reg_d['date'] == '2026-02-01'
        assert reg_d['detail'] == ['Raised $15M']
        assert reg_d['url'] == 'https://www.sec.gov/Archives/edgar/data/999/000099926000001/'
        assert lines[1]['detail'] == ['90,000 plan participants'] and lines[1]['url'] is None
        assert lines[2]['url'].startswith('https://www.sec.gov/cgi-bin/browse-edgar?') and 'CIK=0000320193' in lines[2]['url']

        scouts = asyncio.run(router.register_company_records(key='US:boy scouts of america', user=user))
        assert {r['source_label'] for r in scouts['records']} == {'IRS nonprofit return'}
        assert sorted(r['region'] for r in scouts['records']) == ['CT', 'NY', 'OH', 'TX']
        assert all('$6.0M revenue' in r['detail'] for r in scouts['records'] if r['region'] != 'OH')

        uk = asyncio.run(router.register_company_records(key='GB:plain words', user=user))
        assert sorted(r['source_label'] for r in uk['records']) == ['Club target list', 'Companies House']
        ch = next(r for r in uk['records'] if r['source'] == 'companies_house')
        assert ch['url'] == 'https://find-and-update.company-information.service.gov.uk/company/00000002'

        # An unknown key is an empty log, never an error.
        gone = asyncio.run(router.register_company_records(key='US:nobody', user=user))
        assert gone['records'] == [] and gone['total'] == 0 and gone['display_name'] is None
        # Hidden entries keep their log, with the reason.
        fund = asyncio.run(router.register_company_records(key='US:pimco income strategy fund ii', user=user))
        assert fund['hidden_reason'] == 'investment_fund' and len(fund['records']) == 1


def other_callers_tests() -> None:
    """Flows that start from a register company keep finding it under either name."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.routers import campaigns, releases
        from app.services import company_register as cr

        asyncio.run(init_db())
        asyncio.run(cr.upsert_companies([
            _row('sec_form_d', '0000111', 'us_private', 'Gilgamesh Pharma Inc.', sector_label='Pharmaceuticals',
                 metadata={'why_attractive': 'Series B, no ops team'}),
        ]))

        async def check() -> None:
            db = await get_db()
            try:
                ids = [int(r['id']) for r in await (await db.execute('SELECT id FROM company_register')).fetchall()]
                by_public = await campaigns._company_notes(db, ['gilgamesh pharma'])
                by_legal = await campaigns._company_notes(db, ['Gilgamesh Pharma Inc.'])
            finally:
                await db.close()
            assert 'Series B' in by_public['gilgamesh pharma'], by_public
            assert 'Series B' in by_legal['gilgamesh pharma inc.'], by_legal
            targets = await releases._register_targets(ids)
            # The target carries the public name; the register id is what ties it back.
            assert [t['company'] for t in targets] == ['Gilgamesh Pharma'] and targets[0]['contact_type'] == 'us_private'

        asyncio.run(check())


def ingest_paths_tests() -> None:
    """Every ingest path stores the derived columns: they all end in upsert_companies."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.services import company_register as cr

        asyncio.run(init_db())

        async def fake_get(url, timeout=120.0):
            return (b'{"0":{"cik_str":1,"ticker":"AAPL","title":"Apple Inc."},'
                    b'"1":{"cik_str":2,"ticker":"XX","title":"SUBURBAN PROPANE PARTNERS LP"}}')

        with patch.object(cr, '_http_get', fake_get):
            asyncio.run(cr.ingest_sec_public())
        # The off-box loader and the club sheet arrive as plain rows.
        asyncio.run(cr.upsert_companies([_row('club_sheet', 'k1', 'club_targets', 'The Walt Disney Company')]))

        async def read():
            db = await get_db()
            try:
                return {r['company_name']: dict(r) for r in await (await db.execute(
                    'SELECT company_name, display_name, entity_key, hidden_reason, derived_version FROM company_register'
                )).fetchall()}
            finally:
                await db.close()

        rows = asyncio.run(read())
        assert rows['Apple Inc.']['display_name'] == 'Apple' and rows['Apple Inc.']['entity_key'] == 'US:apple'
        assert rows['Suburban Propane Partners Lp']['display_name'] == 'Suburban Propane Partners'
        assert rows['The Walt Disney Company']['display_name'] == 'Walt Disney'
        assert all(r['derived_version'] == 1 for r in rows.values())

        # The legal name is never rewritten, and a re-ingest keeps derived columns in step.
        asyncio.run(cr.upsert_companies([_row('club_sheet', 'k1', 'club_targets', 'Walt Disney Co')]))
        assert asyncio.run(read())['Walt Disney Co']['entity_key'] == 'US:walt disney'


def backfill_tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.services import company_register as cr
        from app.services import register_names

        asyncio.run(init_db())

        async def insert_old_rows() -> None:
            # What the previous release writes: no derived columns.
            db = await get_db()
            try:
                await db.executemany(
                    "INSERT INTO company_register (source, source_key, tier, country, company_name) VALUES (?,?,?,?,?)",
                    [('irs_990', f'e{i}', 'us_nonprofit', 'US', 'Boy Scouts Of America') for i in range(5)]
                    + [('sec_public', 's1', 'us_public', 'US', 'Apple Inc.'),
                       ('companies_house', 'u1', 'uk', 'GB', 'Acme Holdco Limited')],
                )
                await db.commit()
            finally:
                await db.close()

        async def snapshot() -> list[tuple]:
            db = await get_db()
            try:
                return [tuple(r) for r in await (await db.execute(
                    'SELECT id, display_name, entity_key, hidden_reason, derived_version FROM company_register ORDER BY id'
                )).fetchall()]
            finally:
                await db.close()

        asyncio.run(insert_old_rows())
        # Until derived, rows are out of the list (and counted as pending), never grouped into one blob.
        assert asyncio.run(cr.browse_register())['total'] == 0
        assert asyncio.run(cr.register_summary())['pending'] == 7

        first = asyncio.run(cr.backfill_register_entities(batch_size=3))     # several batches
        assert first == {'ok': True, 'derived': 7}, first
        done = asyncio.run(snapshot())
        assert {r[2] for r in done} == {'US:boy scouts of america', 'US:apple', 'GB:acme holdco'}
        assert [r[3] for r in done if r[2] == 'GB:acme holdco'] == ['acquisition_vehicle']
        assert asyncio.run(cr.register_summary())['pending'] == 0
        page = asyncio.run(cr.browse_register())
        assert page['total'] == 2 and {i['display_name']: i['record_count'] for i in page['items']} == {
            'Apple': 1, 'Boy Scouts of America': 5}

        # Idempotent: nothing to do, nothing rewritten.
        again = asyncio.run(cr.backfill_register_entities())
        assert again == {'ok': True, 'derived': 0}
        assert asyncio.run(snapshot()) == done

        # A new rule version re-derives everything derived under the old one,
        # and only that.
        async def stale_one() -> None:
            db = await get_db()
            try:
                await db.execute("UPDATE company_register SET display_name='stale', derived_version=0 WHERE source_key='s1'")
                await db.commit()
            finally:
                await db.close()

        asyncio.run(stale_one())
        redone = asyncio.run(cr.backfill_register_entities())
        assert redone == {'ok': True, 'derived': 1}, redone
        assert asyncio.run(snapshot()) == done
        with patch.object(cr, 'DERIVATION_VERSION', register_names.DERIVATION_VERSION + 1):
            bumped = asyncio.run(cr.backfill_register_entities())
        assert bumped == {'ok': True, 'derived': 7}, bumped


if __name__ == '__main__':
    name_tests()
    list_tests()
    records_log_tests()
    other_callers_tests()
    ingest_paths_tests()
    backfill_tests()
    print('register entities: ok')
