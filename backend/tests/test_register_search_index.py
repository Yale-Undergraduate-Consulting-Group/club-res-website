"""Register name search through the trigram index: the same companies in the
same order as the LIKE scan it replaces, kept current by writes, and not used
until the backfill has indexed the existing rows."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'register-search-index-secret-xxxxxxxx')


def _row(source: str, key: str, tier: str, name: str, **extra) -> dict:
    row = {'source': source, 'source_key': key, 'tier': tier, 'country': 'GB' if tier == 'uk' else 'US',
           'company_name': name}
    row.update(extra)
    return row


ROWS = [
    _row('sec_public', '1652044', 'us_public', 'Alphabet Inc.', sector_label='Services-Computer Programming',
         metadata={'ticker': 'GOOGL'}, prominence_rank=4),
    _row('sec_public', '1437107', 'us_public', 'Warner Bros. Discovery, Inc.', sector_label='Cable & Other Pay Television',
         metadata={'ticker': 'WBD'}, prominence_rank=180),
    _row('sec_public', '732717', 'us_public', 'AT&T INC.', sector_label='Telephone Communications',
         metadata={'ticker': 'T'}, prominence_rank=20),
    _row('sec_public', '1326801', 'us_public', 'Meta Platforms Inc', sector_label='Services-Computer Programming',
         metadata={'ticker': 'META'}, prominence_rank=5),
    _row('sec_public', '80424', 'us_public', 'Procter & Gamble Co', sector_label='Soap, Detergents',
         metadata={'ticker': 'PG'}, prominence_rank=30),
    _row('sec_public', '1018724', 'us_public', 'Amazon.com, Inc.', sector_label='Retail-Catalog',
         metadata={'ticker': 'AMZN'}, prominence_rank=3),
    _row('sec_form_d', 'D1', 'us_private', 'Hbo Films Partners Lp', last_event_at='2026-05-01'),
    _row('sec_form_d', 'D2', 'us_private', 'Sunset Pharma Partners Ii Lp', sector_label='Pharmaceuticals',
         last_event_at='2026-06-01'),
    _row('companies_house', 'UK1', 'uk', 'Frelif (Loughborough) Llp', sector_label='Accounting'),
    _row('companies_house', 'UK2', 'uk', 'Metal Works Limited', sector_label='Manufacture of fabricated metal products'),
    _row('companies_house', 'UK3', 'uk', 'Mid-Metals Trading Limited', sector_label='Wholesale of metals'),
    _row('companies_house', 'UK4', 'uk', 'Acme Holdco Limited', sector_label='Fabricated metal products'),
    _row('companies_house', 'UK5', 'uk', 'PRICEWATERHOUSECOOPERS LLP'),
    _row('irs_990', 'E1', 'us_nonprofit', 'Boy Scouts Of America', sector_label='Youth development'),
    _row('irs_990', 'E2', 'us_nonprofit', 'Hope 50% Fund', sector_label='Charity'),
    _row('irs_990', 'E3', 'us_nonprofit', 'Habitat For Humanity International Inc', sector_label='Housing'),
    _row('irs_990', 'E4', 'us_nonprofit', 'Rock\'n Roll Foundation', sector_label='Music'),
]

# exact name, ticker, brand alias, word-start, multi-word, sector-only,
# punctuation, public-name-only, literal wildcard characters, and a query
# too short for the index.
QUERIES = [
    'Alphabet Inc.', 'googl', 'GOOGL', 'google', 'youtube', 'hbo', 'hbo max', 'warner bros', 'warner bros. discovery',
    'discovery, inc', 'at&t', 'AT&T', 'p&g', 'procter & gamble', 'meta', 'metal', 'fabricated metal', 'pharmaceuticals',
    'telephone', 'pwc', 'loughborough', 'bank', 'boy scouts of america', 'amazon.com', 'aws', '50%', 'hope 50',
    'rock\'n', 'a_b', 'hb', 'x', 'nonexistent company', 'humanity inter',
]


async def _both(cr, q: str, **kw) -> tuple[dict, dict]:
    return (await cr.browse_register(q=q, limit=50, **kw)), (await cr.search_register(q=q, limit=50, **kw))


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.services import company_register as cr

        asyncio.run(init_db())
        asyncio.run(cr.upsert_companies(ROWS))

        async def scalar(sql: str, *args):
            db = await get_db()
            try:
                return (await (await db.execute(sql, args)).fetchone())[0]
            finally:
                await db.close()

        async def like_results() -> dict:
            """What the LIKE scan returns, whatever state the index is in."""
            async def not_ready(_db) -> bool:
                return False
            with patch.object(cr, '_index_ready', not_ready):
                return {q: await _both(cr, q) for q in QUERIES}

        # Before the backfill has finished, search reads rows, never the index.
        async def before() -> dict:
            with patch.object(cr, '_index_match', side_effect=AssertionError('index used before it is ready')):
                return {q: await _both(cr, q) for q in QUERIES}

        scan = asyncio.run(before())
        # Sanity: the scan answers the questions the index must keep answering.
        browse = lambda q: [i['display_name'] for i in scan[q][0]['items']]  # noqa: E731
        assert browse('googl')[0] == 'Alphabet' and browse('google')[0] == 'Alphabet', browse('googl')
        assert browse('hbo')[0] == 'Warner Bros. Discovery' and 'Frelif' not in ' '.join(browse('hbo')), browse('hbo')
        assert browse('at&t')[0] == 'AT&T' and browse('p&g')[0] == 'Procter & Gamble', (browse('at&t'), browse('p&g'))
        assert browse('meta')[0] == 'Meta' and 'Acme Holdco' not in browse('meta'), browse('meta')
        assert 'Metal Works' in browse('fabricated metal') and browse('pwc') == ['PwC'], browse('fabricated metal')
        assert browse('nonexistent company') == []

        # A row renamed before the backfill reaches it is indexed once, under its new name.
        asyncio.run(cr.upsert_companies([_row('irs_990', 'E1', 'us_nonprofit', 'Scouting Alliance Of America',
                                              sector_label='Youth development')]))
        # Rows the previous release writes carry no derived columns until the derive job runs.
        async def old_release_row() -> None:
            db = await get_db()
            try:
                await db.execute("INSERT INTO company_register (source, source_key, tier, country, company_name) "
                                 "VALUES ('irs_990', 'E9', 'us_nonprofit', 'US', 'Quokka Conservation Trust')")
                await db.commit()
            finally:
                await db.close()
        asyncio.run(old_release_row())
        asyncio.run(cr.backfill_register_entities())
        scan = asyncio.run(before())
        assert [i['display_name'] for i in scan['boy scouts of america'][0]['items']] == []
        assert asyncio.run(scalar("SELECT COUNT(*) FROM settings WHERE key = 'register_fts_ready'")) == 0

        # Index the existing rows, in several batches; a second run has nothing to do.
        done = asyncio.run(cr.index_register_names(batch_size=4))
        assert done == {'ok': True, 'indexed': len(ROWS) + 1}, done
        assert asyncio.run(cr.index_register_names()) == {'ok': True, 'indexed': 0}

        # The index answers every query exactly as the scan does, in the same order.
        # (Queries of two letters or fewer cannot use a trigram index and keep the scan.)
        looked_up: dict[str, str | None] = {}
        real_match = cr._index_match

        def recording_match(raw, aliases, columns, short):
            looked_up[raw] = real_match(raw, aliases, columns, short)
            return looked_up[raw]

        with patch.object(cr, '_index_match', recording_match):
            indexed = {q: asyncio.run(_both(cr, q)) for q in QUERIES}
        assert all(looked_up[q.lower()] for q in ('hbo', 'at&t', 'googl', 'fabricated metal', 'pwc', 'p&g', '50%')), looked_up
        assert not looked_up['hb'] and not looked_up['x'], looked_up
        scan = asyncio.run(like_results())
        for q in QUERIES:
            assert indexed[q][0] == scan[q][0], ('browse', q, indexed[q][0], scan[q][0])
            assert indexed[q][1] == scan[q][1], ('search', q, indexed[q][1], scan[q][1])
        assert [i['display_name'] for i in asyncio.run(cr.browse_register(q='scouting alliance'))['items']] == \
            ['Scouting Alliance of America']
        assert asyncio.run(cr.browse_register(q='boy scouts'))['items'] == []
        assert [i['display_name'] for i in asyncio.run(cr.browse_register(q='quokka'))['items']] == ['Quokka Conservation Trust']
        # The same filters apply on top of the index.
        assert [i['display_name'] for i in asyncio.run(cr.browse_register(q='metal', country='GB'))['items']] == \
            [i['display_name'] for i in scan['metal'][0]['items'] if i['country'] == 'GB']

        # Writes after the backfill are searchable at once: insert, rename, delete.
        asyncio.run(cr.upsert_companies([_row('sec_form_d', 'D7', 'us_private', 'Zyxwvut Robotics Inc')]))
        assert [i['display_name'] for i in asyncio.run(cr.browse_register(q='zyxwvut'))['items']] == ['Zyxwvut Robotics']
        assert [i['company_name'] for i in asyncio.run(cr.search_register(q='robotics'))['items']] == ['Zyxwvut Robotics Inc']
        asyncio.run(cr.upsert_companies([_row('sec_form_d', 'D7', 'us_private', 'Quillfeather Aerospace Inc')]))
        assert asyncio.run(cr.browse_register(q='zyxwvut'))['items'] == []
        assert [i['display_name'] for i in asyncio.run(cr.browse_register(q='quillfeather'))['items']] == ['Quillfeather Aerospace']
        assert [i['display_name'] for i in asyncio.run(cr.browse_register(q='aerospace'))['items']] == ['Quillfeather Aerospace']

        async def delete_row() -> None:
            db = await get_db()
            try:
                await db.execute("DELETE FROM company_register WHERE source_key = 'D7'")
                await db.commit()
            finally:
                await db.close()
        asyncio.run(delete_row())
        assert asyncio.run(cr.search_register(q='quillfeather'))['items'] == []
        # A deleted row's id is never reused, so nothing new inherits its entry.
        asyncio.run(cr.upsert_companies([_row('sec_form_d', 'D8', 'us_private', 'Plainwood Holdings Inc')]))
        assert [i['company_name'] for i in asyncio.run(cr.search_register(q='plainwood'))['items']] == ['Plainwood Holdings Inc']
        assert asyncio.run(cr.search_register(q='quillfeather'))['items'] == []

        # A re-ingest restores E1's original name: the index follows, and the other names still match.
        asyncio.run(cr.upsert_companies(ROWS))
        assert [i['display_name'] for i in asyncio.run(cr.browse_register(q='boy scouts'))['items']] == ['Boy Scouts of America']
        assert asyncio.run(cr.browse_register(q='scouting alliance'))['items'] == []
        assert 'Metal Works' in [i['display_name'] for i in asyncio.run(cr.browse_register(q='metal'))['items']]


if __name__ == '__main__':
    tests()
    print('register_search_index: ok')
