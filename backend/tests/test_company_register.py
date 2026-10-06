"""Bulk company register: SEC listed companies, SEC Form D filers (the free
startup pool), and their named officers.

The Form D fixture mirrors the real quarterly data set exactly - same table
names, same columns, same value shapes ('06b' date codes, shouty names,
'Pooled Investment Fund' groups) as the 2025Q2 file inspected on the box.
"""
import asyncio
import csv
import io
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'company-register-secret-xxxxxxxxxxxxxx')

ISSUER_COLS = ['ACCESSIONNUMBER', 'IS_PRIMARYISSUER_FLAG', 'ISSUER_SEQ_KEY', 'CIK', 'ENTITYNAME',
               'STREET1', 'STREET2', 'CITY', 'STATEORCOUNTRY', 'STATEORCOUNTRYDESCRIPTION',
               'ZIPCODE', 'ISSUERPHONENUMBER', 'JURISDICTIONOFINC', 'ENTITYTYPE',
               'YEAROFINC_TIMESPAN_CHOICE', 'YEAROFINC_VALUE_ENTERED']
OFFERING_COLS = ['ACCESSIONNUMBER', 'INDUSTRYGROUPTYPE', 'REVENUERANGE', 'SALE_DATE',
                 'TOTALOFFERINGAMOUNT', 'TOTALAMOUNTSOLD']
PERSON_COLS = ['ACCESSIONNUMBER', 'RELATEDPERSON_SEQ_KEY', 'FIRSTNAME', 'MIDDLENAME', 'LASTNAME',
               'RELATIONSHIP_1', 'RELATIONSHIP_2', 'RELATIONSHIP_3', 'RELATIONSHIPCLARIFICATION']


def _tsv(columns, rows):
    lines = ['\t'.join(columns)]
    for row in rows:
        lines.append('\t'.join(str(row.get(col, '')) for col in columns))
    return '\n'.join(lines) + '\n'


def _fixture_zip(recent: str, stale: str) -> bytes:
    issuers = [
        {'ACCESSIONNUMBER': 'A1', 'IS_PRIMARYISSUER_FLAG': 'YES', 'CIK': '0001111111',
         'ENTITYNAME': 'NOVA ROBOTICS INC', 'CITY': 'BOSTON', 'STATEORCOUNTRYDESCRIPTION': 'MASSACHUSETTS',
         'ENTITYTYPE': 'Corporation', 'YEAROFINC_VALUE_ENTERED': '2021'},
        {'ACCESSIONNUMBER': 'A2', 'IS_PRIMARYISSUER_FLAG': 'YES', 'CIK': '0002222222',
         'ENTITYNAME': 'TINY SEED LLC', 'CITY': 'AUSTIN', 'STATEORCOUNTRYDESCRIPTION': 'TEXAS'},
        {'ACCESSIONNUMBER': 'A3', 'IS_PRIMARYISSUER_FLAG': 'YES', 'CIK': '0003333333',
         'ENTITYNAME': 'HARBOR CAPITAL FUND II LP', 'CITY': 'NEW YORK', 'STATEORCOUNTRYDESCRIPTION': 'NEW YORK'},
        {'ACCESSIONNUMBER': 'A5', 'IS_PRIMARYISSUER_FLAG': 'YES', 'CIK': '0006666666',
         'ENTITYNAME': 'BIG TOWER REIT INC', 'CITY': 'DALLAS', 'STATEORCOUNTRYDESCRIPTION': 'TEXAS'},
        {'ACCESSIONNUMBER': 'A4', 'IS_PRIMARYISSUER_FLAG': 'YES', 'CIK': '0004444444',
         'ENTITYNAME': 'OLD MONEY HEALTH INC', 'CITY': 'CHICAGO', 'STATEORCOUNTRYDESCRIPTION': 'ILLINOIS'},
        # A co-issuer row on an accepted filing must not become its own company.
        {'ACCESSIONNUMBER': 'A1', 'IS_PRIMARYISSUER_FLAG': 'NO', 'CIK': '0005555555',
         'ENTITYNAME': 'NOVA ROBOTICS SPV', 'CITY': 'BOSTON'},
    ]
    offerings = [
        {'ACCESSIONNUMBER': 'A1', 'INDUSTRYGROUPTYPE': 'Technology', 'REVENUERANGE': '$1,000,000 - $4,999,999',
         'SALE_DATE': recent, 'TOTALOFFERINGAMOUNT': '9000000', 'TOTALAMOUNTSOLD': '9000000'},
        {'ACCESSIONNUMBER': 'A2', 'INDUSTRYGROUPTYPE': 'Technology', 'SALE_DATE': recent,
         'TOTALOFFERINGAMOUNT': '150000', 'TOTALAMOUNTSOLD': '150000'},          # below the size bar
        {'ACCESSIONNUMBER': 'A3', 'INDUSTRYGROUPTYPE': 'Pooled Investment Fund', 'SALE_DATE': recent,
         'TOTALOFFERINGAMOUNT': '50000000', 'TOTALAMOUNTSOLD': '50000000'},      # a fund, not an operating company
        # The live file writes 'and', not '&'; an ampersand skip-list let REITs through.
        {'ACCESSIONNUMBER': 'A5', 'INDUSTRYGROUPTYPE': 'REITS and Finance', 'SALE_DATE': recent,
         'TOTALOFFERINGAMOUNT': '40000000', 'TOTALAMOUNTSOLD': '40000000'},
        {'ACCESSIONNUMBER': 'A4', 'INDUSTRYGROUPTYPE': 'Health Care', 'SALE_DATE': stale,
         'TOTALOFFERINGAMOUNT': '8000000', 'TOTALAMOUNTSOLD': '8000000'},        # too old
    ]
    persons = [
        {'ACCESSIONNUMBER': 'A1', 'FIRSTNAME': 'Ada', 'LASTNAME': 'Lovelace',
         'RELATIONSHIP_1': 'Executive Officer', 'RELATIONSHIP_2': 'Director', 'RELATIONSHIPCLARIFICATION': 'CEO'},
        {'ACCESSIONNUMBER': 'A1', 'FIRSTNAME': 'Grace', 'MIDDLENAME': 'B', 'LASTNAME': 'Hopper',
         'RELATIONSHIP_1': 'Director'},
        {'ACCESSIONNUMBER': 'A1', 'FIRSTNAME': 'Promoter', 'LASTNAME': 'Only', 'RELATIONSHIP_1': 'Promoter'},
        {'ACCESSIONNUMBER': 'A1', 'FIRSTNAME': 'Mononym', 'RELATIONSHIP_1': 'Executive Officer'},
        {'ACCESSIONNUMBER': 'A3', 'FIRSTNAME': 'Fund', 'LASTNAME': 'Manager', 'RELATIONSHIP_1': 'Executive Officer'},
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        zf.writestr('2026Q1_d/ISSUERS.tsv', _tsv(ISSUER_COLS, issuers))
        zf.writestr('2026Q1_d/OFFERING.tsv', _tsv(OFFERING_COLS, offerings))
        zf.writestr('2026Q1_d/RELATEDPERSONS.tsv', _tsv(PERSON_COLS, persons))
    return buffer.getvalue()


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.services import company_register as cr

        asyncio.run(init_db())

        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=45)).strftime('%d-%b-%Y').upper()
        stale = (now - timedelta(days=365 * 6)).strftime('%d-%b-%Y').upper()
        payload = _fixture_zip(recent, stale)

        companies, people = cr.parse_form_d_zip(payload)
        by_name = {c['company_name']: c for c in companies}
        assert set(by_name) == {'Nova Robotics Inc'}, by_name  # size, industry, recency, primary-issuer filters
        nova = by_name['Nova Robotics Inc']
        assert nova['tier'] == 'us_private' and nova['country'] == 'US'
        assert nova['last_event_amount'] == 9000000.0 and nova['last_event_kind'] == 'reg_d_offering'
        assert nova['last_event_at'] == (now - timedelta(days=45)).strftime('%Y-%m-%d')
        assert nova['sector_label'] == 'Technology' and nova['region'] == 'Massachusetts'
        assert nova['metadata']['revenue_range'] == '$1,000,000 - $4,999,999'

        officers = people[('sec_form_d', '0001111111')]
        names = sorted(p['full_name'] for p in officers)
        assert names == ['Ada Lovelace', 'Grace B Hopper']  # promoter dropped, single-token name dropped
        assert officers[0]['relationship'] == 'Executive Officer'
        assert officers[0]['source_url'].startswith('https://www.sec.gov/Archives/edgar/data/1111111/')
        assert ('sec_form_d', '0003333333') not in people  # fund never entered the register

        # --- ingest through the real code path with the network mocked ---
        async def fake_get(url, timeout=120.0):
            if 'company_tickers' in url:
                return b'{"0":{"cik_str":320193,"ticker":"AAPL","title":"Apple Inc."},' \
                       b'"1":{"cik_str":789019,"ticker":"MSFT","title":"MICROSOFT CORP"}}'
            return payload

        with patch.object(cr, '_http_get', fake_get):
            public = asyncio.run(cr.ingest_sec_public())
            first = asyncio.run(cr.ingest_form_d('2026q1'))
            # Re-running a quarter is a no-op, not a duplicate.
            again = asyncio.run(cr.ingest_form_d('2026q1'))
        assert public['written'] == 2 and first['written'] == 1 and first['officers'] == 2
        assert again.get('skipped')

        page = asyncio.run(cr.search_register(limit=50))
        assert page['total'] == 3
        names = {item['company_name'] for item in page['items']}
        assert names == {'Apple Inc.', 'Microsoft Corp', 'Nova Robotics Inc'}  # shouty name title-cased
        assert page['items'][0]['company_name'] == 'Nova Robotics Inc'  # newest raise ranks first
        # Neither listed company has a filing date or dollar amount to rank
        # by - that used to mean alphabetical, so "Apple" beat "Microsoft"
        # only because of the letter A. SEC serves company_tickers.json in
        # market-cap order (Apple at key "0", Microsoft at "1" here); keeping
        # that order is what lets a big company outrank an obscure one with
        # nothing else to go on.
        assert [i['company_name'] for i in page['items'][1:]] == ['Apple Inc.', 'Microsoft Corp']
        assert page['items'][1]['prominence_rank'] == 0
        assert page['items'][2]['prominence_rank'] == 1
        assert page['items'][0]['officer_count'] == 2

        startups = asyncio.run(cr.search_register(tier='us_private'))
        assert [i['company_name'] for i in startups['items']] == ['Nova Robotics Inc']
        assert asyncio.run(cr.search_register(tier='us_public'))['total'] == 2
        assert asyncio.run(cr.search_register(with_officers=True))['total'] == 1
        assert asyncio.run(cr.search_register(min_amount=20_000_000))['total'] == 0
        assert asyncio.run(cr.search_register(q='robot'))['total'] == 1
        assert asyncio.run(cr.search_register(sector='technology'))['total'] == 1

        # US rows carry no invented headcount; the raise is the honest signal.
        assert all(item['employees'] is None for item in page['items'])

        # --- sector backfill fills listed companies without one ---
        async def fake_submissions(url, timeout=30.0):
            return (b'{"sic":"3571","sicDescription":"Electronic Computers",'
                    b'"stateOfIncorporationDescription":"CALIFORNIA","website":""}')

        with patch.object(cr, '_http_get', fake_submissions):
            filled = asyncio.run(cr.backfill_sec_sectors(limit=5))
        assert filled['filled'] == 2 and filled['remaining'] == 0
        tech = asyncio.run(cr.search_register(sector='Electronic Computers'))
        assert tech['total'] == 2 and tech['items'][0]['sector_code'] == '3571'

        summary = asyncio.run(cr.register_summary())
        tiers = {row['tier']: row['n'] for row in summary['tiers']}
        assert tiers == {'us_public': 2, 'us_private': 1}
        assert any(row['source'] == 'sec_form_d' for row in summary['recent_ingests'])

        # --- the quarter in progress is never requested: SEC publishes a
        # data set only after the quarter closes, so listing it would 404
        # every pass and block the published quarters behind it ---
        current = f"{now.year}q{(now.month - 1) // 3 + 1}"
        quarters = cr.recent_form_d_quarters()
        assert current not in quarters and len(quarters) >= 4
        assert quarters == sorted(quarters, reverse=True)

        # --- a failed download records the failure and retries next pass ---
        async def boom(url, timeout=120.0):
            raise RuntimeError('SEC unavailable')

        with patch.object(cr, '_http_get', boom):
            failed = asyncio.run(cr.ingest_form_d('2026q2'))
        assert failed['ok'] is False
        assert not asyncio.run(cr._already_ingested('sec_form_d', '2026q2'))

        # An unpublished quarter is flagged as such and does not stop the
        # drain from reaching an older quarter in the same pass.
        async def not_found(url, timeout=120.0):
            if 'company_tickers' in url:
                return b'{}'
            if '2099q4' in url:
                raise RuntimeError("Client error '404 Not Found' for url")
            return payload

        with patch.object(cr, '_http_get', not_found):
            unpublished = asyncio.run(cr.ingest_form_d('2099q4'))
            assert unpublished['unpublished'] is True
            with patch.object(cr, 'recent_form_d_quarters', lambda count=None: ['2099q4', '2026q3']):
                drained = asyncio.run(cr.drain_company_register())
        assert drained['form_d']['quarter'] == '2026q3' and drained['form_d']['ok'] is True

        async def check_people() -> None:
            db = await get_db()
            try:
                rows = await (await db.execute(
                    """SELECT p.full_name, p.relationship FROM company_register_people p
                       JOIN company_register c ON c.id = p.register_id
                       WHERE c.source_key = '0001111111' ORDER BY p.full_name"""
                )).fetchall()
                assert [r['full_name'] for r in rows] == ['Ada Lovelace', 'Grace B Hopper']
            finally:
                await db.close()

        asyncio.run(check_people())


UK_COLS = ['CompanyName', ' CompanyNumber', 'RegAddress.AddressLine1', 'RegAddress.PostTown',
           'CompanyCategory', 'CompanyStatus', 'DissolutionDate', 'IncorporationDate',
           'Accounts.LastMadeUpDate', 'Accounts.AccountCategory', 'SICCode.SicText_1', 'URI']


def _uk_csv(rows):
    import csv as _csv
    buf = io.StringIO()
    writer = _csv.DictWriter(buf, fieldnames=UK_COLS)
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col.strip(), '') for col in UK_COLS})
    return buf.getvalue().encode('utf-8')


def uk_tests() -> None:
    """Companies House bulk: only active companies above the small-company
    accounts thresholds that are operating businesses. Column names and value
    shapes match the live 2026-09 file (' CompanyNumber' really does carry a
    leading space; dates are dd/mm/yyyy)."""
    import zipfile as _zip
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services import company_register as cr

    rows = [
        {'CompanyName': 'DELIVEROO PLC', 'CompanyNumber': '13227665', 'RegAddress.PostTown': 'LONDON',
         'CompanyStatus': 'Active', 'Accounts.AccountCategory': 'GROUP', 'IncorporationDate': '21/11/2020',
         'Accounts.LastMadeUpDate': '31/12/2025', 'SICCode.SicText_1': '56102 - Unlicensed restaurants and cafes',
         'CompanyCategory': 'Public Limited Company', 'URI': 'http://business.data.gov.uk/id/company/13227665'},
        {'CompanyName': 'MIDSIZE MAKER LTD', 'CompanyNumber': '00000002', 'RegAddress.PostTown': 'LEEDS',
         'CompanyStatus': 'Active', 'Accounts.AccountCategory': 'MEDIUM',
         'SICCode.SicText_1': '25620 - Machining'},
        # Below the thresholds: a one-person consultancy.
        {'CompanyName': 'TINY CONSULTANCY LTD', 'CompanyNumber': '00000003', 'CompanyStatus': 'Active',
         'Accounts.AccountCategory': 'MICRO ENTITY', 'SICCode.SicText_1': '70229 - Management consultancy'},
        # Right size, wrong kind: a holding vehicle, like Form D pooled funds.
        {'CompanyName': 'BIGCO HOLDINGS LIMITED', 'CompanyNumber': '00000004', 'CompanyStatus': 'Active',
         'Accounts.AccountCategory': 'FULL', 'SICCode.SicText_1': '64209 - Activities of other holding companies'},
        {'CompanyName': 'PROPERTY VEHICLE LTD', 'CompanyNumber': '00000005', 'CompanyStatus': 'Active',
         'Accounts.AccountCategory': 'FULL', 'SICCode.SicText_1': '68209 - Other letting of own real estate'},
        # Right size and kind, but no longer trading.
        {'CompanyName': 'GONE LTD', 'CompanyNumber': '00000006', 'CompanyStatus': 'Liquidation',
         'Accounts.AccountCategory': 'FULL', 'SICCode.SicText_1': '25620 - Machining'},
    ]
    buffer = io.BytesIO()
    with _zip.ZipFile(buffer, 'w') as zf:
        zf.writestr('BasicCompanyDataAsOneFile-2026-09-01.csv', _uk_csv(rows))
    with _zip.ZipFile(io.BytesIO(buffer.getvalue())) as zf:
        with zf.open(zf.namelist()[0]) as handle:
            kept = list(cr.parse_uk_bulk(handle))

    names = [c['company_name'] for c in kept]
    assert names == ['Deliveroo Plc', 'Midsize Maker Ltd'], names
    first = kept[0]
    assert first['tier'] == 'uk' and first['country'] == 'GB' and first['source_key'] == '13227665'
    assert first['sector_code'] == '56102' and first['sector_label'] == 'Unlicensed restaurants and cafes'
    assert first['region'] == 'London'
    assert first['last_event_at'] == '2025-12-31' and first['last_event_kind'] == 'accounts_filed'
    assert first['metadata']['size_band'] == 'group (consolidated accounts)'
    assert first['metadata']['incorporated'] == '2020-11-21'
    # No invented headcount: the band is the evidence, and its source is named.
    assert first.get('employees') is None
    assert first['employees_source'] == 'companies_house_account_category'
    assert kept[1]['metadata']['size_band'].startswith('medium')
    assert cr._uk_date('') is None and cr._uk_date('31/12/2025') == '2025-12-31'


def officer_backfill_tests() -> None:
    """Listed and UK companies arrive with no people, because neither bulk
    product ships them. Both registers publish them per company, keyed by the
    identifier already stored, and the two fetchers describe a person with
    title/role_type while the register stores relationship - so the mapping is
    the part that has to be right."""
    import asyncio as _asyncio
    from unittest.mock import patch
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.database import get_db, init_db
    from app.services import company_register as cr

    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = 'sqlite:///' + tmp + '/officers.db'

        async def scenario() -> None:
            await init_db()
            await cr.upsert_companies([
                {'source': 'sec_public', 'source_key': '0000320193', 'tier': 'us_public',
                 'country': 'US', 'company_name': 'Apple Inc.'},
                {'source': 'companies_house', 'source_key': '13227665', 'tier': 'uk',
                 'country': 'GB', 'company_name': 'Deliveroo Plc'},
                {'source': 'sec_form_d', 'source_key': 'X1', 'tier': 'us_private',
                 'country': 'US', 'company_name': 'Private Co'},
            ])
            db = await get_db()
            rows = {r['company_name']: int(r['id']) for r in await (await db.execute(
                'SELECT id, company_name FROM company_register')).fetchall()}
            await db.close()

            # SEC's submissions file is keyed by the padded CIK. Stripping the
            # padding 404s, which is how this first failed against the live API.
            seen_ciks: list[str] = []

            async def fake_form4(cik, **kwargs):
                seen_ciks.append(cik)
                return [
                    {'full_name': 'Kevan Parekh', 'title': 'Senior Vice President, CFO',
                     'role_type': 'officer', 'source_url': 'https://www.sec.gov/x'},
                    {'full_name': 'Arthur D. Levinson', 'title': '', 'role_type': 'director'},
                    {'full_name': 'Gone Person', 'title': 'Former', 'role_type': 'officer',
                     'employment': 'left'},
                ]

            with patch('app.services.roster_watch.fetch_form4_people', fake_form4):
                out = await cr.fetch_officers_for(rows['Apple Inc.'])
            assert seen_ciks == ['0000320193'], seen_ciks
            assert out['ok'] and out['officer_count'] == 2, out

            db = await get_db()
            stored = {r['full_name']: r['relationship'] for r in await (await db.execute(
                'SELECT full_name, relationship FROM company_register_people WHERE register_id=?',
                (rows['Apple Inc.'],))).fetchall()}
            count = int((await (await db.execute(
                'SELECT officer_count FROM company_register WHERE id=?', (rows['Apple Inc.'],))).fetchone())['officer_count'])
            await db.close()
            # A title becomes the relationship; without one, the role does.
            assert stored == {'Kevan Parekh': 'Senior Vice President, CFO',
                              'Arthur D. Levinson': 'director'}, stored
            # A resigned officer is evidence of departure, not a contact.
            assert 'Gone Person' not in stored
            assert count == 2

            # Asking again never spends another request.
            async def explode(*a, **k):
                raise AssertionError('must not refetch a company that has officers')

            with patch('app.services.roster_watch.fetch_form4_people', explode):
                again = await cr.fetch_officers_for(rows['Apple Inc.'])
            assert again['cached'] is True and again['officer_count'] == 2

            # Form D rows already carry their officers from the bulk file, so
            # there is no per-company register to call.
            private = await cr.fetch_officers_for(rows['Private Co'])
            assert private['ok'] is False and 'no officer list' in private['error']

            # Companies House without a key makes no call at all.
            os.environ.pop('COMPANIES_HOUSE_API_KEY', None)
            uk = await cr.fetch_officers_for(rows['Deliveroo Plc'])
            assert uk['ok'] is False and 'key' in uk['error'].lower(), uk

        _asyncio.run(scenario())


F5500_COLS = ['SPONSOR_DFE_NAME', 'SPONS_DFE_EIN', 'TYPE_PLAN_ENTITY_CD', 'TOT_ACTIVE_PARTCP_CNT',
              'BUSINESS_CODE', 'SPONS_DFE_MAIL_US_STATE', 'SPONS_DFE_MAIL_US_CITY',
              'FORM_PLAN_YEAR_BEGIN_DATE', 'PLAN_NAME', 'SPONS_SIGNED_NAME', 'SPONS_SIGNED_DATE']


def _f5500_csv(rows):
    import csv as _csv
    buf = io.StringIO()
    writer = _csv.DictWriter(buf, fieldnames=F5500_COLS)
    writer.writeheader()
    for row in rows:
        writer.writerow({col: row.get(col, '') for col in F5500_COLS})
    return buf.getvalue().encode('latin-1')


def form_5500_tests() -> None:
    """Form 5500 is the widest US source available: the SEC sees only listed
    companies and Reg D filers, while every employer sponsoring a benefit plan
    files this. It also reports the one number no free US source publishes -
    the active participant count the employer filed itself."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services import company_register as cr

    rows = [
        {'SPONSOR_DFE_NAME': 'THE HOME DEPOT, INC.', 'SPONS_DFE_EIN': '95-3261426',
         'TYPE_PLAN_ENTITY_CD': '2', 'TOT_ACTIVE_PARTCP_CNT': '419778', 'BUSINESS_CODE': '444110',
         'SPONS_DFE_MAIL_US_STATE': 'ga', 'SPONS_DFE_MAIL_US_CITY': 'ATLANTA',
         'FORM_PLAN_YEAR_BEGIN_DATE': '2025-01-01', 'PLAN_NAME': 'FUTUREBUILDER',
         'SPONS_SIGNED_NAME': 'JANE ROE', 'SPONS_SIGNED_DATE': '2026-07-14'},
        # An earlier plan year for the same sponsor: one company, latest filing wins.
        {'SPONSOR_DFE_NAME': 'THE HOME DEPOT, INC.', 'SPONS_DFE_EIN': '953261426',
         'TYPE_PLAN_ENTITY_CD': '2', 'TOT_ACTIVE_PARTCP_CNT': '390000', 'BUSINESS_CODE': '444110',
         'FORM_PLAN_YEAR_BEGIN_DATE': '2022-01-01', 'PLAN_NAME': 'OLDER FILING'},
        {'SPONSOR_DFE_NAME': 'WIKOFF COLOR CORPORATION', 'SPONS_DFE_EIN': '570123456',
         'TYPE_PLAN_ENTITY_CD': '2', 'TOT_ACTIVE_PARTCP_CNT': '406', 'BUSINESS_CODE': '325910',
         'SPONS_DFE_MAIL_US_STATE': 'SC', 'FORM_PLAN_YEAR_BEGIN_DATE': '2025-01-01'},
        # Too small to host a ten-week student team.
        {'SPONSOR_DFE_NAME': 'TINY SHOP LLC', 'SPONS_DFE_EIN': '111111111',
         'TYPE_PLAN_ENTITY_CD': '2', 'TOT_ACTIVE_PARTCP_CNT': '12', 'BUSINESS_CODE': '448140'},
        # Multiemployer union trust: its participants work for many employers,
        # so the count is not one company's staff.
        {'SPONSOR_DFE_NAME': 'NATIONAL EDUCATION ASSOCIATION', 'SPONS_DFE_EIN': '222222222',
         'TYPE_PLAN_ENTITY_CD': '1', 'TOT_ACTIVE_PARTCP_CNT': '2484299', 'BUSINESS_CODE': '813930'},
        # Direct filing entity, same reasoning.
        {'SPONSOR_DFE_NAME': 'SOME BENEFITS TRUST', 'SPONS_DFE_EIN': '333333333',
         'TYPE_PLAN_ENTITY_CD': '4', 'TOT_ACTIVE_PARTCP_CNT': '477594', 'BUSINESS_CODE': '525990'},
        # Right size and a single-employer plan, wrong kind: a pooled vehicle.
        {'SPONSOR_DFE_NAME': 'POOLED VEHICLE LP', 'SPONS_DFE_EIN': '444444444',
         'TYPE_PLAN_ENTITY_CD': '2', 'TOT_ACTIVE_PARTCP_CNT': '900', 'BUSINESS_CODE': '525920'},
    ]
    companies, people = cr.parse_form_5500(io.BytesIO(_f5500_csv(rows)))
    names = sorted(c['company_name'] for c in companies)
    assert names == ['The Home Depot, Inc.', 'Wikoff Color Corporation'], names

    depot = next(c for c in companies if c['company_name'].startswith('The Home Depot'))
    # The EIN is the key, with punctuation stripped, so the two filings above
    # are one company rather than two.
    assert depot['source_key'] == '953261426'
    assert depot['tier'] == 'us_employer' and depot['country'] == 'US'
    assert depot['employees'] == 419778, 'the latest plan year must win'
    assert depot['employees_source'] == 'form_5500_active_participants'
    assert depot['sector_code'] == '444110' and depot['sector_label'] == 'Retail Trade'
    assert depot['region'] == 'GA' and depot['metadata']['city'] == 'Atlanta'
    assert depot['last_event_at'] == '2025-01-01' and depot['last_event_kind'] == 'benefit_plan_filed'

    # The person who signed the filing is a named contact from the company's
    # own document.
    signer = people[('dol_5500', '953261426')]
    assert signer[0]['full_name'] == 'Jane Roe'
    assert signer[0]['relationship'] == 'Signed the plan filing'
    assert signer[0]['observed_at'] == '2026-07-14'
    # A filing with no signature contributes no person rather than a blank one.
    assert ('dol_5500', '570123456') not in people


def nonprofit_buyer_tests() -> None:
    """Existing is not the same as being able to buy. A nonprofit qualifies
    only if it is large enough to fund a project and already pays outside
    firms for advice - both filed on its own Form 990."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services import company_register as cr

    extract_cols = ['EIN', 'totrevenue', 'feesforsrvcmgmt', 'legalfees', 'accntingfees',
                    'feesforsrvcothr', 'noemplyeesw3cnt']
    rows = [
        # Buys advice, right size: qualifies.
        {'EIN': '135562308', 'totrevenue': '18400000', 'feesforsrvcmgmt': '40000',
         'legalfees': '10000', 'accntingfees': '6000', 'noemplyeesw3cnt': '287'},
        # Large but spends nothing on outside advice: not a buyer.
        {'EIN': '222222222', 'totrevenue': '90000000', 'feesforsrvcmgmt': '0',
         'legalfees': '0', 'accntingfees': '1000', 'noemplyeesw3cnt': '500'},
        # Buys advice but is too small to fund a project.
        {'EIN': '333333333', 'totrevenue': '900000', 'feesforsrvcmgmt': '200000',
         'legalfees': '0', 'accntingfees': '0', 'noemplyeesw3cnt': '9'},
        # A health plan whose "other fees" are medical claims. Counting
        # feesforsrvcothr put organisations like this at the top of the
        # register with billions of imaginary consulting spend, so it is
        # excluded and this row fails on advice alone.
        {'EIN': '444444444', 'totrevenue': '6156000000', 'feesforsrvcmgmt': '0',
         'legalfees': '0', 'accntingfees': '0', 'feesforsrvcothr': '5789900000',
         'noemplyeesw3cnt': '1200'},
        # Advice above a quarter of revenue is a pass-through, not a client.
        {'EIN': '555555555', 'totrevenue': '10000000', 'feesforsrvcmgmt': '9000000',
         'legalfees': '0', 'accntingfees': '0', 'noemplyeesw3cnt': '5'},
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=extract_cols)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, '') for c in extract_cols})
    qualifying = cr.qualifying_990_filers(io.BytesIO(buf.getvalue().encode('latin-1')))
    assert sorted(qualifying) == ['135562308'], sorted(qualifying)
    assert qualifying['135562308']['advice'] == 56000
    assert qualifying['135562308']['employees'] == 287

    # The extract has the money and no names; the Business Master File has the
    # names. The join is on EIN and drops anything unmatched.
    bmf_cols = ['EIN', 'NAME', 'CITY', 'STATE', 'NTEE_CD', 'TAX_PERIOD']
    bmf = io.StringIO()
    writer = csv.DictWriter(bmf, fieldnames=bmf_cols)
    writer.writeheader()
    writer.writerow({'EIN': '135562308', 'NAME': 'CHEEKWOOD BOTANICAL GARDEN', 'CITY': 'NASHVILLE',
                     'STATE': 'tn', 'NTEE_CD': 'A50', 'TAX_PERIOD': '202412'})
    writer.writerow({'EIN': '999999999', 'NAME': 'NOT A QUALIFYING FILER', 'STATE': 'NY'})
    out = list(cr._bmf_rows_for(io.BytesIO(bmf.getvalue().encode('latin-1')), qualifying))
    assert len(out) == 1, out
    org = out[0]
    assert org['company_name'] == 'Cheekwood Botanical Garden'
    assert org['tier'] == 'us_nonprofit' and org['source_key'] == '135562308'
    assert org['sector_label'] == 'Arts, Culture and Humanities'
    assert org['region'] == 'TN' and org['metadata']['city'] == 'Nashville'
    assert org['employees'] == 287 and org['employees_source'] == 'form_990_w3_employee_count'
    assert org['last_event_at'] == '2024-12-01' and org['last_event_kind'] == 'form_990_filed'
    # The buying signal is the point of this tier, so it is shown, not implied.
    assert '$56k/yr' in org['metadata']['buys_outside_advice']
    assert org['metadata']['revenue_range'] == '$18.4M revenue'


def part_vii_officer_tests() -> None:
    """Part VII names the people running the organisation, with the titles it
    gave them - the contacts that are otherwise the slowest thing to find."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services.company_register import parse_part_vii

    xml = (
        '<Return><EIN>135562308</EIN>'
        '<Form990PartVIISectionAGrp><PersonNm>JANE   ROE</PersonNm>'
        '<TitleTxt>PRESIDENT &amp; CEO</TitleTxt></Form990PartVIISectionAGrp>'
        '<Form990PartVIISectionAGrp><PersonNm>JOHN DOE</PersonNm>'
        '<TitleTxt>FORMER EXECUTIVE DIRECTOR</TitleTxt></Form990PartVIISectionAGrp>'
        '<Form990PartVIISectionAGrp><PersonNm>Jane Roe</PersonNm>'
        '<TitleTxt>TRUSTEE</TitleTxt></Form990PartVIISectionAGrp>'
        '<Form990PartVIISectionAGrp><PersonNm>Reception</PersonNm>'
        '<TitleTxt>DESK</TitleTxt></Form990PartVIISectionAGrp>'
        '</Return>'
    )
    ein, people = parse_part_vii(xml)
    assert ein == '135562308'
    assert [p['full_name'] for p in people] == ['Jane Roe'], people
    # XML escapes are not part of the title: "PRESIDENT &amp; CEO" is a string
    # nobody wrote, and it reached the database on the first live run.
    assert people[0]['relationship'] == 'PRESIDENT & CEO'
    # "FORMER" is the organisation stating the person has left, and a
    # single-word entry is a desk, not a person.
    assert all('Doe' not in p['full_name'] for p in people)
    assert parse_part_vii('<Return><Form990PartVIISectionAGrp/></Return>') == ('', [])


def person_level_tests() -> None:
    """A filing names the people the law requires, which is mostly the board.
    Measured on the live register: 58.7% of 392,190 titled people are
    directors or trustees and 7.2% hold a working-level role, and only 26.2%
    of companies with any filed officer have a single working-level person.

    The trap is the word "director". It is the most common title in the
    register by a distance (97,292), and in a Form 990 it means a board seat.
    Matching it as a job would mail trustees.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.services.company_register import classify_person_level as level

    # Bare board titles, including the one that looks like a job.
    for title in ('Director', 'Trustee', 'Board Member', 'Chair', 'Vice Chair',
                  'Member', 'Chairperson', 'Board of Directors'):
        assert level(title) == 'board', (title, level(title))

    # The same word, qualified, is a job.
    for title in ('Director of Operations', 'Director of Programs',
                  'VP Strategic Partnerships', 'Head of Acquisitions',
                  'Senior Vice President, CFO', 'Vice President',
                  'Managing Director', 'Head of School'):
        assert level(title) == 'working', (title, level(title))

    for title in ('CEO', 'Chairman & CEO', 'President', 'Treasurer',
                  'Executive Officer', 'General Counsel'):
        assert level(title) == 'executive', (title, level(title))

    # Nothing to go on is said, not guessed.
    assert level('Signed the plan filing') == 'unknown'
    assert level('') == 'unknown'
    assert level(None) == 'unknown'


def prominence_rank_tests() -> None:
    """The Apple/Microsoft case above also happens to be alphabetical, which
    would let a regression back to plain `company_name` ordering slip past
    unnoticed. This uses a big company with a late-alphabet name and a small
    one with an early-alphabet name, so only real rank-based ordering passes."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import init_db
        from app.services import company_register as cr

        asyncio.run(init_db())

        async def fake_get(url, timeout=120.0):
            return (
                b'{"0":{"cik_str":1,"ticker":"ZETA","title":"ZETA GLOBAL HOLDINGS CORP"},'
                b'"1":{"cik_str":2,"ticker":"ACME","title":"ACME MICRO CAP INC"}}'
            )

        with patch.object(cr, '_http_get', fake_get):
            result = asyncio.run(cr.ingest_sec_public())
        assert result['written'] == 2

        page = asyncio.run(cr.search_register(limit=10))
        # Plain alphabetical would put Acme first. SEC's own order (Zeta at
        # key "0") is what a member actually wants: the company that exists
        # first in the source ranks first, not the one whose name starts
        # earlier in the alphabet.
        assert [i['company_name'] for i in page['items']] == ['Zeta Global Holdings Corp', 'Acme Micro Cap Inc']

        # Re-ingesting must not scramble the order: SEC's file order is
        # stable across runs, and the stored rank should be too.
        with patch.object(cr, '_http_get', fake_get):
            again = asyncio.run(cr.ingest_sec_public())
        assert again['written'] == 2
        page = asyncio.run(cr.search_register(limit=10))
        assert [i['company_name'] for i in page['items']] == ['Zeta Global Holdings Corp', 'Acme Micro Cap Inc']


def sec_fund_exclusion_tests() -> None:
    """SEC's ticker file lists every registered security, ETFs included, with
    nothing but the name to tell them from an operating company - reported
    live: searching the "us" tier surfaced "21Shares Dogecoin ETF" and five
    others like it between real companies. They are excluded by name, and
    prominence_rank is still assigned only to what is kept, so a real
    company's rank reflects its position among real companies, not among
    funds SEC happened to list ahead of it."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import init_db
        from app.services import company_register as cr

        asyncio.run(init_db())

        async def fake_get(url, timeout=120.0):
            return (
                b'{"0":{"cik_str":1,"ticker":"IDIB","title":"1stdibs.com, Inc."},'
                b'"1":{"cik_str":2,"ticker":"DOGE","title":"21Shares Dogecoin ETF"},'
                b'"2":{"cik_str":3,"ticker":"ETHS","title":"21Shares Ethereum Staking ETF"},'
                b'"3":{"cik_str":4,"ticker":"XX","title":"22nd Century Group, Inc."}}'
            )

        with patch.object(cr, '_http_get', fake_get):
            result = asyncio.run(cr.ingest_sec_public())
        assert result['written'] == 2, result

        page = asyncio.run(cr.search_register(tier='us_public', limit=10))
        names = [i['company_name'] for i in page['items']]
        assert names == ['1stdibs.com, Inc.', '22nd Century Group, Inc.'], names
        # Rank is dense among what is kept: the ETF at SEC's index 1 does not
        # leave a gap, and does not make the next real company rank 3.
        ranks = {i['company_name']: i['prominence_rank'] for i in page['items']}
        assert ranks == {'1stdibs.com, Inc.': 0, '22nd Century Group, Inc.': 1}, ranks


def search_relevance_tests() -> None:
    """"meta" is a literal four-letter prefix of "metal", so every UK company
    with "metal"/"metals" in its name or industry-sector text matched the old
    substring search. Those rows carry real recent Companies House filing
    dates; a US-listed company like Meta carries none, so recency-first
    ranking buried an exact match under unrelated noise. This is the bug a
    member actually hit, reported live: searching "meta" surfaced five UK
    metal companies and no Meta."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import init_db
        from app.services import company_register as cr

        asyncio.run(init_db())
        asyncio.run(cr.upsert_companies([
            {'source': 'sec_public', 'source_key': '1326801', 'tier': 'us_public', 'country': 'US',
             'company_name': 'Meta Platforms Inc', 'prominence_rank': 5},
            {'source': 'companies_house', 'source_key': '1', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Ferro Metal And Chemical Corporation Limited',
             'sector_label': 'Non-specialised wholesale trade', 'last_event_at': '2026-08-01'},
            {'source': 'companies_house', 'source_key': '2', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Central Asia Metals Plc',
             'sector_label': 'Activities of head offices', 'last_event_at': '2026-08-15'},
            {'source': 'companies_house', 'source_key': '3', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Precision Micro Limited',
             'sector_label': 'Manufacture of other fabricated metal products n.e.c.',
             'last_event_at': '2026-08-20'},
            {'source': 'companies_house', 'source_key': '4', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Chris Allsop Metal Recycling Ltd',
             'sector_label': 'Wholesale of waste and scrap', 'last_event_at': '2026-08-25'},
        ]))

        page = asyncio.run(cr.search_register(q='meta', limit=10))
        names = [i['company_name'] for i in page['items']]
        assert names[0] == 'Meta Platforms Inc', names
        # Every "metal" name still matches (a word starting with the query is
        # a hit worth showing), just never ahead of the company the query
        # actually named. Precision Micro matched only through its sector
        # text ("fabricated metal products"); a four-letter query no longer
        # counts a sector-only hit, since it names no company.
        assert set(names) == {
            'Meta Platforms Inc', 'Ferro Metal And Chemical Corporation Limited',
            'Central Asia Metals Plc', 'Chris Allsop Metal Recycling Ltd',
        }, names

        # A query naming a whole word inside a longer name ranks that whole
        # word above a plain substring match, then by recency within the tie.
        page = asyncio.run(cr.search_register(q='metal', limit=10))
        metal_names = [i['company_name'] for i in page['items']]
        assert metal_names == [
            'Chris Allsop Metal Recycling Ltd', 'Ferro Metal And Chemical Corporation Limited',
            'Central Asia Metals Plc', 'Precision Micro Limited',
        ], metal_names

        # A literal % or _ in a query is a character to match, not a wildcard.
        page = asyncio.run(cr.search_register(q='100%', limit=10))
        assert page['items'] == []


def size_ranking_tests() -> None:
    """Reported live: typing "HBO" returned "Hbo Film & Television
    Development Limited", "Hbos Financial Services Limited", "Hbos Uk
    Limited" and "Frelif (Loughborough) Llp", and never Warner Bros.
    Discovery. Three causes, each pinned here: recency ranked every dated UK
    row above a listed company in the same match level; a short query matched
    mid-word ("Loug-hbo-rough"); and HBO is a brand with no register row of
    its own."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import init_db
        from app.services import company_register as cr

        asyncio.run(init_db())
        asyncio.run(cr.upsert_companies([
            {'source': 'sec_public', 'source_key': '1437107', 'tier': 'us_public', 'country': 'US',
             'company_name': 'Warner Bros. Discovery, Inc.', 'prominence_rank': 180,
             'metadata': {'ticker': 'WBD', 'cik': '1437107'}},
            {'source': 'sec_public', 'source_key': '1652044', 'tier': 'us_public', 'country': 'US',
             'company_name': 'Alphabet Inc.', 'prominence_rank': 2,
             'metadata': {'ticker': 'GOOGL', 'cik': '1652044'}},
            {'source': 'companies_house', 'source_key': 'U1', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Hbo Film & Television Development Limited', 'last_event_at': '2026-08-30',
             'metadata': {'account_category': 'FULL'}},
            {'source': 'companies_house', 'source_key': 'U2', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Hbos Financial Services Limited', 'last_event_at': '2026-08-29',
             'metadata': {'account_category': 'MEDIUM'}},
            {'source': 'companies_house', 'source_key': 'U3', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Hbos Uk Limited', 'last_event_at': '2026-08-28',
             'metadata': {'account_category': 'GROUP'}},
            {'source': 'companies_house', 'source_key': 'U4', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Frelif (Loughborough) Llp', 'last_event_at': '2026-09-01'},
            # Same match level ("acme" starts both names as a whole word):
            # the listed company has no filing date, the UK one a fresh one.
            {'source': 'companies_house', 'source_key': 'U5', 'tier': 'uk', 'country': 'GB',
             'company_name': 'Acme Holdings Limited', 'last_event_at': '2026-09-10'},
            {'source': 'sec_public', 'source_key': '9', 'tier': 'us_public', 'country': 'US',
             'company_name': 'Acme United Corp', 'prominence_rank': 4000},
        ]))

        names = [i['company_name'] for i in asyncio.run(cr.search_register(q='hbo', limit=10))['items']]
        assert names[0] == 'Warner Bros. Discovery, Inc.', names
        assert 'Frelif (Loughborough) Llp' not in names, names
        # The UK rows that do start with "hbo" still show, whole word first.
        assert names[1] == 'Hbo Film & Television Development Limited', names
        # Between the two plain-prefix "Hbos" rows the statutory band decides
        # (group accounts before medium), not which filed a day later.
        assert names[2:] == ['Hbos Uk Limited', 'Hbos Financial Services Limited'], names

        acme = [i['company_name'] for i in asyncio.run(cr.search_register(q='acme', limit=10))['items']]
        assert acme == ['Acme United Corp', 'Acme Holdings Limited'], acme

        # Exact ticker, any case, is the top match level even though the
        # letters appear nowhere in the registered name.
        page = asyncio.run(cr.search_register(q='googl', limit=10))
        assert [i['company_name'] for i in page['items']] == ['Alphabet Inc.'], page['items']
        assert page['items'][0]['match_level'] == 0
        assert asyncio.run(cr.search_register(q='WBD'))['items'][0]['company_name'] == 'Warner Bros. Discovery, Inc.'
        # A brand alias for a company outside the register finds nothing
        # rather than something wrong.
        assert asyncio.run(cr.search_register(q='tiktok'))['items'] == []
        assert cr.brand_alias_names('  YouTube ') == ('alphabet inc',)


if __name__ == '__main__':
    tests()
    uk_tests()
    officer_backfill_tests()
    form_5500_tests()
    nonprofit_buyer_tests()
    part_vii_officer_tests()
    person_level_tests()
    prominence_rank_tests()
    sec_fund_exclusion_tests()
    search_relevance_tests()
    size_ranking_tests()
    print('company register: ok')
