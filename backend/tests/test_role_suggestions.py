"""Company-specific role suggestions: grounded in titles the system has
observed (SEC roster, prior runs, shared contacts), filled from one
LinkedIn search when thin, and an equivalence mapping that can only pick
from observed titles - never invent one.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('JWT_SECRET', 'role-suggestions-secret-xxxxxxxxxxxxxxxxx')


def tests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['DATABASE_URL'] = f'sqlite:///{Path(tmp) / "t.db"}'
        os.environ['LLM_PROVIDER'] = 'bedrock'
        for mod in [m for m in list(sys.modules) if m.startswith('app.')]:
            del sys.modules[mod]

        from app.database import get_db, init_db
        from app.services import role_suggestions as rs

        asyncio.run(init_db())

        # --- pure title normalisation from LinkedIn result headlines ---
        assert rs.title_from_search_result('Jane Doe - Product Lead at Anthropic | LinkedIn') == 'Product Lead'
        assert rs.title_from_search_result('John Q. Public – Member of Technical Staff – OpenAI') == 'Member of Technical Staff'
        assert rs.title_from_search_result('Ada Lovelace - VP, Healthcare Partnerships - OpenAI | LinkedIn') == 'VP, Healthcare Partnerships'
        assert rs.title_from_search_result('OpenAI | LinkedIn') == ''          # company page, no person
        assert rs.title_from_search_result('Someone - 2019 - LinkedIn') == ''   # no letters in the title slot
        assert rs.normalize_title('VP Product at Acme') == 'VP Product'
        assert rs.normalize_title('  ') == ''
        # Stored prospect titles are often raw headlines / non-titles seen live.
        assert rs.normalize_title('Taylor Gordon - Member of Technical Staff', 'OpenAI') == 'Member of Technical Staff'
        assert rs.normalize_title('OpenAI', 'OpenAI') == ''                    # the company itself
        assert rs.normalize_title('YouTube', 'OpenAI') == ''
        assert rs.normalize_title('Leadership vs. Management - What it means ...', 'OpenAI') == ''
        assert rs.normalize_title('The Net Zero Asset Managers initiative', 'Anthropic') == ''
        # Job postings are the company's own vocabulary.
        assert rs.title_from_job_posting('Product Manager, Business Technology at Anthropic - LinkedIn', 'Anthropic') == 'Product Manager, Business Technology'
        assert rs.title_from_job_posting('Web Product Manager at Anthropic — New York, NY - Jobs - LinkedIn', 'Anthropic') == 'Web Product Manager'
        assert rs.title_from_job_posting('Research Product Manager, Model Behaviors - LinkedIn', 'Anthropic') == 'Research Product Manager, Model Behaviors'
        assert rs.title_from_job_posting('Anthropic: Jobs - LinkedIn', 'Anthropic') == ''
        assert rs.title_from_job_posting('Anthropic hiring Product Management, Research in San Francisco, CA - LinkedIn', 'Anthropic') == 'Product Management, Research'
        # A headline cut mid-title keeps what came before the cut instead of
        # being thrown away; the truncated word and a dangling connector go.
        assert rs.title_from_search_result('Jane Doe - Senior Director of Global Partnerships and Gro…', 'OpenAI') == 'Senior Director of Global Partnerships'
        assert rs.title_from_search_result('Jane Doe - Head of Gro...', 'OpenAI') == ''  # "Head" alone is no title
        # The company's own name is stripped generically (no per-company list).
        assert rs.normalize_title('VP Content, The Walt Disney Company', 'The Walt Disney Company') == 'VP Content'
        # Snippets: the current role at THIS company, never a past employer,
        # education, or first-person prose.
        assert rs.title_from_snippet('Product Lead at OpenAI · Experience: OpenAI · Education: Yale', 'OpenAI') == 'Product Lead'
        assert rs.title_from_snippet('Experience: Head of Health AI at OpenAI · Location: SF', 'OpenAI') == 'Head of Health AI'
        assert rs.title_from_snippet('Director of Sales at Google · Experience: Google', 'OpenAI') == ''
        assert rs.title_from_snippet('I lead product at OpenAI and love it', 'OpenAI') == ''
        assert rs.title_from_snippet('Former VP Marketing at OpenAI', 'OpenAI') == ''

        async def seed() -> None:
            db = await get_db()
            try:
                await db.execute("INSERT INTO users (id, email, name, is_active) VALUES (1, 'a@yale.edu', 'A', 1)")
                # SEC roster: two officers.
                await db.execute(
                    "INSERT INTO company_rosters (company_key, company_name, company_domain, next_verify_at, created_at, updated_at) "
                    "VALUES ('openai', 'OpenAI', 'openai.com', '2026-01-01', '2026-01-01', '2026-01-01')")
                for name, title in [('Sam A', 'Chief Executive Officer'), ('Brad L', 'Chief Operating Officer')]:
                    await db.execute(
                        "INSERT INTO company_roster_people (roster_id, normalized_name, full_name, title, source, first_seen_at, last_seen_at) "
                        "VALUES (1, ?, ?, ?, 'sec', '2026-01-01', '2026-01-01')", (name.lower(), name, title))
                # A prior Find people run with three prospects (one title repeated).
                cur = await db.execute(
                    "INSERT INTO yucgoutreach_discovery_runs (user_id, company_name, company_domain, max_prospects, worker_concurrency, status) "
                    "VALUES (1, 'OpenAI', 'openai.com', 50, 4, 'completed')")
                run_id = cur.lastrowid
                for title in ['Member of Technical Staff', 'Member of Technical Staff', 'Head of Health AI']:
                    await db.execute(
                        "INSERT INTO yucgoutreach_prospects (run_id, first_name, last_name, email, title) VALUES (?, 'X', 'Y', ?, ?)",
                        (run_id, f'{title.replace(" ", "").lower()}@openai.com', title))
                # Shared catalog contact at the company.
                await db.execute(
                    "INSERT INTO contacts (name, email, title, company, company_domain) VALUES ('Z', 'z@openai.com', 'Product Lead', 'OpenAI', 'openai.com')")
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())

        observed = asyncio.run(rs.observed_titles('OpenAI', 'openai.com'))
        by_title = {o['title']: o for o in observed}
        assert by_title['Member of Technical Staff']['count'] == 2 and by_title['Member of Technical Staff']['source'] == 'run'
        assert by_title['Chief Executive Officer']['source'] == 'roster'
        assert by_title['Product Lead']['source'] == 'catalog'
        assert observed[0]['title'] == 'Member of Technical Staff'  # most common first

        # --- search fill happens when observed is thin or hints are given;
        # LLM equivalents are constrained to observed titles ---
        search_calls: list[str] = []
        page_sizes: list[int] = []

        async def fake_search(query, max_results=8, user_id=None):
            search_calls.append(query)
            page_sizes.append(max_results)
            return [
                {'title': 'Pat K - Product Lead at OpenAI | LinkedIn', 'url': 'https://www.linkedin.com/in/patk', 'content': ''},
                {'title': 'Lee M - Product Lead - OpenAI', 'url': 'https://www.linkedin.com/in/leem', 'content': ''},
                {'title': 'Ana R - Healthcare Go-To-Market Lead at OpenAI', 'url': 'https://www.linkedin.com/in/anar', 'content': ''},
                {'title': 'OpenAI | LinkedIn', 'url': 'https://www.linkedin.com/company/openai', 'content': ''},
                {'title': 'Product Manager, Health at OpenAI - LinkedIn', 'url': 'https://www.linkedin.com/jobs/view/pm-health-123', 'content': ''},
                {'title': "Someone's Post - Product Lead, Consumer - LinkedIn", 'url': 'https://www.linkedin.com/posts/someone_x', 'content': ''},
            ]

        llm_prompts: list[str] = []

        def fake_complete_json(prompt, model_id=None, system=None):
            llm_prompts.append(prompt)
            return {'equivalents': [
                {'asked': 'healthcare PMs', 'at_company': ['Product Lead', 'Healthcare Go-To-Market Lead', 'Chief Product Officer'],
                 'note': 'OpenAI does not use the PM title; product roles are Product Lead.'},
                {'asked': 'VPs', 'at_company': [], 'note': 'No VP titles observed; senior leaders are C-level.'},
            ]}

        with patch('app.services.web_fetch.web_search', fake_search), \
             patch('app.services.web_fetch.web_search_configured', lambda: True), \
             patch('app.services.llm.complete_json', fake_complete_json):
            result = asyncio.run(rs.suggest_roles(user_id=1, company='OpenAI', domain='openai.com', hints='healthcare PMs, VPs'))

        # Each hint term is its own plain-words query (the joined string
        # matched nobody live), then the generic query; all are merged.
        assert search_calls == ['OpenAI healthcare PMs site:linkedin.com/in',
                                'OpenAI VPs site:linkedin.com/in',
                                'OpenAI leadership director manager lead site:linkedin.com/in'], search_calls
        # One results page per query: the quota charges per page.
        assert page_sizes and all(n <= 8 for n in page_sizes), page_sizes
        titles = [r['title'] for r in result['roles']]
        assert 'Product Lead' in titles and 'Healthcare Go-To-Market Lead' in titles
        assert 'OpenAI' not in titles  # company page headline never becomes a role
        assert 'Product Manager, Health' in titles and next(r for r in result['roles'] if r['title'] == 'Product Manager, Health')['source'] == 'jobs'
        assert 'Product Lead, Consumer' not in titles  # /posts/ are not evidence of a role held
        # Product Lead: 1 catalog + 2 search = 3 - each profile counts once
        # even though all three queries returned it.
        assert next(r for r in result['roles'] if r['title'] == 'Product Lead')['count'] == 3
        assert next(r for r in result['roles'] if r['title'] == 'Product Lead')['source'] == 'catalog'

        eq = {e['asked']: e for e in result['equivalents']}
        # 'Chief Product Officer' was never observed - the model's suggestion of it is dropped.
        assert eq['healthcare PMs']['at_company'] == ['Product Lead', 'Healthcare Go-To-Market Lead']
        assert 'Product Lead' in eq['healthcare PMs']['note']
        assert eq['VPs']['at_company'] == []
        assert 'Member of Technical Staff' in llm_prompts[0] and 'choose ONLY from these' in llm_prompts[0]
        # distinct titles per source; 'by_band' counts the escalation, unused here
        assert result['sources'] == {'run': 2, 'roster': 2, 'catalog': 1, 'search': 2, 'jobs': 1, 'by_band': 0}

        # --- no hints and enough observed titles: no search, no model call ---
        search_calls.clear()
        llm_prompts.clear()
        with patch('app.services.web_fetch.web_search', fake_search), \
             patch('app.services.web_fetch.web_search_configured', lambda: True), \
             patch('app.services.llm.complete_json', fake_complete_json):
            quiet = asyncio.run(rs.suggest_roles(user_id=1, company='OpenAI', domain='openai.com', hints=None))
        # The generic query ran a moment ago: served from the cache, no quota spent.
        assert search_calls == [], search_calls
        assert llm_prompts == []
        assert quiet['equivalents'] == []
        assert 'Healthcare Go-To-Market Lead' in [r['title'] for r in quiet['roles']]
        # Cold cache: 5 distinct observed titles (< threshold 6) -> one fill
        # search, which lifts it to 7 distinct, so no seniority bands.
        rs._search_cache.clear()
        with patch('app.services.web_fetch.web_search', fake_search), \
             patch('app.services.web_fetch.web_search_configured', lambda: True), \
             patch('app.services.llm.complete_json', fake_complete_json):
            asyncio.run(rs.suggest_roles(user_id=1, company='OpenAI', domain='openai.com', hints=None))
        assert len(search_calls) == 1, search_calls

        # --- unknown company, search unavailable: empty but well-formed ---
        with patch('app.services.web_fetch.web_search_configured', lambda: False):
            empty = asyncio.run(rs.suggest_roles(user_id=1, company='Nonexistent Widgets', domain=None, hints=None))
        assert empty['roles'] == [] and empty['equivalents'] == []
        assert 'not configured' in empty['note']

        # --- quota exhausted mid-request: honest note, observed titles still returned ---
        from fastapi import HTTPException

        async def quota_search(query, max_results=8, user_id=None):
            raise HTTPException(429, 'Web search limit reached. Please try again later.')

        rs._search_cache.clear()
        with patch('app.services.web_fetch.web_search', quota_search), \
             patch('app.services.web_fetch.web_search_configured', lambda: True):
            limited = asyncio.run(rs.suggest_roles(user_id=1, company='OpenAI', domain='openai.com', hints=None))
        assert limited['note'].startswith('Web search limit reached')
        assert any(r['title'] == 'Member of Technical Staff' for r in limited['roles'])

        # --- quota hit part-way: what the earlier queries found is kept ---
        rs._search_cache.clear()
        partial_calls: list[str] = []

        async def first_then_quota(query, max_results=8, user_id=None):
            partial_calls.append(query)
            if len(partial_calls) > 1:
                raise HTTPException(429, 'Web search limit reached. Please try again later.')
            return [{'title': 'Kim P - Head of Clinical Partnerships - OpenAI', 'url': 'https://www.linkedin.com/in/kimp', 'content': ''}]

        with patch('app.services.web_fetch.web_search', first_then_quota), \
             patch('app.services.web_fetch.web_search_configured', lambda: True), \
             patch('app.services.llm.complete_json', fake_complete_json):
            part = asyncio.run(rs.suggest_roles(user_id=1, company='OpenAI', domain='openai.com', hints='clinical leads, sales'))
        assert 'Head of Clinical Partnerships' in [r['title'] for r in part['roles']], part['roles']
        assert part['note'].startswith('Web search limit reached')
        assert len(partial_calls) == 2, partial_calls  # stops asking after the first 429


def unknown_company_escalates_within_linkedin_not_to_its_website() -> None:
    """When one broad LinkedIn query finds nobody, ask LinkedIn again per
    seniority band. A company's own team page is not the answer: it lists a
    handful of executives and nobody below them, and the people who reply to a
    cold email are VPs, heads and managers who own a budget."""
    import asyncio as _asyncio
    from unittest.mock import patch

    from app.services import role_suggestions as rs

    asked: list[str] = []

    async def no_observations(company, domain=None):
        return []

    async def search_finds_nothing(company, hints, *, user_id=None):
        return [], None

    async def per_band(query, max_results=10, user_id=None):
        asked.append(query)
        if query.startswith('Cheekwood Botanical Garden VP '):
            return [{'url': 'https://www.linkedin.com/in/jane-roe',
                     'title': 'Jane Roe - VP Partnerships - Cheekwood Botanical Garden | LinkedIn'}]
        if 'Head of' in query:
            return [{'url': 'https://www.linkedin.com/in/ada',
                     'title': 'Ada Lovelace - Head of Insight - Cheekwood Botanical Garden | LinkedIn'}]
        return []

    with patch.object(rs, 'observed_titles', no_observations), \
         patch.object(rs, 'search_titles', search_finds_nothing), \
         patch('app.services.web_fetch.web_search', per_band):
        out = _asyncio.run(rs.suggest_roles(
            user_id=1, company='Cheekwood Botanical Garden', domain='cheekwood.org', hints=None))

    # Every escalation stays on LinkedIn profiles, one band per query.
    assert asked, 'no escalation happened'
    assert all(q.endswith('site:linkedin.com/in') for q in asked), asked
    assert any(' VP ' in q for q in asked) and any('Head of' in q for q in asked), asked
    # Never the company website.
    assert not any('cheekwood.org' in q for q in asked), asked

    titles = [r['title'] for r in out['roles']]
    assert 'VP Partnerships' in titles and 'Head of Insight' in titles, titles
    assert all(r['source'] == 'band' for r in out['roles']), out['roles']
    assert out['sources']['by_band'] == len(titles)
    assert 'seniority band' in (out['note'] or ''), out['note']

    # When even that finds nobody, say so rather than inventing roles.
    async def nothing(query, max_results=10, user_id=None):
        return []

    with patch.object(rs, 'observed_titles', no_observations), \
         patch.object(rs, 'search_titles', search_finds_nothing), \
         patch('app.services.web_fetch.web_search', nothing):
        blank = _asyncio.run(rs.suggest_roles(
            user_id=1, company='Invisible Ltd', domain='invisible.example', hints=None))
    assert blank['roles'] == []
    assert 'returns nothing for this company' in (blank['note'] or ''), blank['note']


def few_titles_top_up_by_band_within_the_page_budget() -> None:
    """One or two titles found is not enough to choose from: the seniority
    bands top the list up, but a cold load never spends more than
    _PAGE_BUDGET one-page searches, and a reload spends none."""
    import asyncio as _asyncio
    from unittest.mock import patch

    from app.services import role_suggestions as rs

    rs._search_cache.clear()
    asked: list[tuple[str, int]] = []

    async def no_observations(company, domain=None):
        return []

    async def thin(query, max_results=8, user_id=None):
        asked.append((query, max_results))
        if 'leadership director' in query:
            # A bare-name headline: the role is only in the snippet.
            return [{'url': 'https://www.linkedin.com/in/sam', 'title': 'Sam Poe | LinkedIn',
                     'content': 'Head of Merchandising at Garmin · Experience: Garmin · Education: Kansas State'}]
        band = query.replace('Garmin ', '').replace(' site:linkedin.com/in', '')
        if band in rs._SENIORITY_BANDS:
            slug = band.lower().replace(' ', '-')
            return [{'url': f'https://www.linkedin.com/in/{slug}-{i}', 'title': f'P{i} - {band} Product Line {i} - Garmin'}
                    for i in range(2)]
        return []

    with patch.object(rs, 'observed_titles', no_observations), \
         patch('app.services.web_fetch.web_search_configured', lambda: True), \
         patch('app.services.web_fetch.web_search', thin), \
         patch('app.services.llm.complete_json', lambda *a, **k: {'equivalents': []}):
        out = _asyncio.run(rs.suggest_roles(user_id=1, company='Garmin', domain='garmin.com',
                                            hints='buyers; merchandisers / category managers, planners and analysts'))
    queries = [q for q, _ in asked]
    # 3 hint terms (of 5 typed) + generic + bands, never more than the budget.
    assert len(asked) <= rs._PAGE_BUDGET, queries
    assert all(n <= 8 for _, n in asked), asked
    assert sum(1 for q in queries if any(q == f'Garmin {b} site:linkedin.com/in' for b in rs._SENIORITY_BANDS)) >= 1, queries
    titles = [r['title'] for r in out['roles']]
    assert 'Head of Merchandising' in titles, titles  # from the snippet
    assert len(titles) >= rs._SEARCH_FILL_THRESHOLD, titles  # topped up past one or two chips
    # Bands stop once the list is useful: 1 + 2 per band -> 3 bands reach 6+.
    assert out['sources']['by_band'] >= 5, out['sources']

    # Reload: every query is cached, no quota spent.
    asked.clear()
    with patch.object(rs, 'observed_titles', no_observations), \
         patch('app.services.web_fetch.web_search_configured', lambda: True), \
         patch('app.services.web_fetch.web_search', thin), \
         patch('app.services.llm.complete_json', lambda *a, **k: {'equivalents': []}):
        again = _asyncio.run(rs.suggest_roles(user_id=1, company='Garmin', domain='garmin.com',
                                              hints='buyers; merchandisers / category managers, planners and analysts'))
    assert asked == [], asked
    assert [r['title'] for r in again['roles']] == titles


if __name__ == '__main__':
    tests()
    unknown_company_escalates_within_linkedin_not_to_its_website()
    few_titles_top_up_by_band_within_the_page_budget()
    print('role suggestions: ok')
