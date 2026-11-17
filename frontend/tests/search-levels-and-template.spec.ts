import { expect, test, type Page } from '@playwright/test';

/**
 * The Find contacts screen's search choices, with every API mocked: who to
 * look for is a set of levels, searches run a few at a time, a company is
 * searched again from its own heading in the sheet, and every member sends
 * the club's one template.
 */

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

const PEOPLE = [
  { id: 1, name: 'Jean Bartik', email: 'jean@a24films.com', title: 'Director of Operations', company: 'A24', person_level: 'working' },
  { id: 2, name: 'Lena Marks', email: 'lena@a24films.com', title: 'Vice President, Marketing', company: 'A24', person_level: 'executive' },
  { id: 3, name: 'Tomas Board', email: 'tomas@a24films.com', title: 'Trustee', company: 'A24', person_level: 'board' },
  { id: 4, name: 'Sam Ortiz', email: 'sam@a24films.com', title: '', company: 'A24', person_level: 'unknown' },
];

const INTRO = { id: 11, name: 'Intro', subject: 'Hello {first}', body: 'Hi {first}, we are YUCG.', use_case: 'intro', industry: null, scope: 'club', mine: false, editable: false };

type Run = { id: number; company: string; finished: boolean };

async function mock(page: Page, opts: { people?: typeof PEOPLE; searched?: string[]; maxActive?: number } = {}) {
  const runsCreated: Record<string, unknown>[] = [];
  const runs: Run[] = (opts.searched ?? []).map((company, i) => ({ id: 100 + i, company, finished: true }));
  const builds: Record<string, unknown>[] = [];
  const maxActive = opts.maxActive ?? 3;
  const runBody = (r: Run) => ({
    id: r.id, company_name: r.company, status: r.finished ? 'completed' : 'running',
    progress_pct: r.finished ? 100 : 30, progress_message: r.finished ? 'Done' : 'Searching the web',
  });

  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const p = url.pathname;
    const method = request.method();
    let body: unknown = {};
    let status = 200;
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') body = { items: opts.people ?? [], total: (opts.people ?? []).length, limit: 2000, offset: 0 };
    else if (p === '/api/outreach/templates') body = [INTRO];
    else if (p === '/api/campaigns/build') {
      const payload = request.postDataJSON();
      builds.push(payload);
      body = { recipients: (payload.contact_ids || []).length, ready: (payload.contact_ids || []).length, held: [], sample: null };
    } else if (p === '/api/yucgoutreach/resolve-company') {
      const q = url.searchParams.get('q') || '';
      body = { name: q, domain: null, domain_verified: false, linkedin_url: null, source: 'typed', alternatives: [] };
    } else if (p === '/api/yucgoutreach/runs' && method === 'POST') {
      // The server's cap: a few active searches per member, then 409.
      if (runs.filter((r) => !r.finished).length >= maxActive) {
        status = 409;
        body = { detail: 'You already have 3 company searches queued or running' };
      } else {
        const payload = request.postDataJSON();
        runsCreated.push(payload);
        const id = Math.max(0, ...runs.map((r) => r.id)) + 1;
        runs.push({ id, company: payload.company_name, finished: false });
        body = { id, status: 'queued' };
      }
    } else if (p === '/api/yucgoutreach/runs') body = runs.map(runBody);
    else if (/^\/api\/yucgoutreach\/runs\/\d+$/.test(p)) {
      const run = runs.find((r) => r.id === Number(p.split('/').pop()));
      body = run ? runBody(run) : {};
    } else if (/^\/api\/yucgoutreach\/runs\/\d+\/prospects$/.test(p)) body = [];
    else if (p === '/api/contacts/companies/summary' || p === '/api/outreach/flows') body = [];
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    await route.fulfill({ status, json: body });
  });
  return { runsCreated, runs, builds };
}

test('who to look for is a set: the counts follow it and the last choice cannot be switched off', async ({ page }) => {
  await mock(page, { people: PEOPLE, searched: ['A24'] });
  await page.goto('/scraper?view=company&companies=A24');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const who = rail.getByRole('group', { name: 'Who to look for' });
  const level = (name: string) => who.getByRole('button', { name, exact: true });
  const header = pipeline.getByTestId('selected-count');
  const status = pipeline.locator('[data-company="A24"]').getByTestId('company-status');

  // Team leads and Senior leaders: Jean and Lena. The header, the lane and the
  // Send button say the same number.
  await expect(header).toHaveText('2 can be emailed');
  await expect(rail.getByTestId('send-button')).toHaveText('Send to 2');
  await expect(pipeline.locator('[data-lane="A24"]').getByTestId('lane-state')).toHaveText('2 can be emailed');
  await expect(status).toContainText('2 can be emailed · 4 people found · 2 for Team leads and Senior leaders');

  // Someone with no title is ticked only when "No title shown" is chosen.
  await level('No title shown').click();
  await expect(header).toHaveText('3 can be emailed');
  await expect(pipeline.getByRole('checkbox', { name: 'Write to Sam Ortiz' })).toBeChecked();
  await level('Board').click();
  await expect(header).toHaveText('4 can be emailed');
  await expect(rail.getByTestId('send-button')).toHaveText('Send to 4');
  await expect(status).toHaveText('4 can be emailed · 4 people found');

  // Switching a level off unticks the people only it brought in.
  await level('Team leads').click();
  await level('Senior leaders').click();
  await expect(header).toHaveText('2 can be emailed');
  await expect(pipeline.getByRole('checkbox', { name: 'Write to Jean Bartik' })).not.toBeChecked();
  await expect(pipeline.getByRole('checkbox', { name: 'Write to Tomas Board' })).toBeChecked();

  // The last one stays on.
  await level('No title shown').click();
  await expect(level('Board')).toHaveAttribute('aria-pressed', 'true');
  await level('Board').click({ force: true });
  await expect(level('Board')).toHaveAttribute('aria-pressed', 'true');
  await expect(level('Board')).toHaveAttribute('aria-disabled', 'true');
  await expect(header).toHaveText('1 can be emailed');
});

test('Search on three companies starts three searches without waiting; the fourth waits for room', async ({ page }) => {
  const server = await mock(page);
  await page.goto('/scraper?view=company&companies=Acme,Blue%20Dot,Cedar,Delta');
  await page.getByTestId('search-all').click();
  const pipeline = page.locator('[data-section="campaign-pipeline"]');

  // Three run together while none has finished; the server refuses a fourth
  // (409), which is not shown as an error.
  await expect.poll(() => server.runsCreated.length).toBe(3);
  await expect(pipeline.locator('[data-lane="Delta"]').getByTestId('lane-state')).toHaveText('Queued');
  await expect(pipeline.getByRole('status').filter({ hasText: 'Delta is queued behind' })).toBeVisible();
  await expect(pipeline.getByRole('alert')).toHaveCount(0);
  expect(server.runsCreated.map((r) => r.company_name)).toEqual(['Acme', 'Blue Dot', 'Cedar']);
  expect(server.runsCreated.every((r) => JSON.stringify(r.levels) === JSON.stringify(['working', 'executive']))).toBe(true);

  // When one finishes there is room, and the fourth starts by itself.
  server.runs[0].finished = true;
  await expect.poll(() => server.runsCreated.map((r) => r.company_name), { timeout: 15_000 }).toEqual(['Acme', 'Blue Dot', 'Cedar', 'Delta']);
});

test('a company is searched again from its own heading in the sheet, and only there', async ({ page }) => {
  const server = await mock(page, { people: PEOPLE, searched: ['A24', 'NEON'] });
  await page.goto('/scraper?view=company&companies=A24,NEON');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const picker = pipeline.getByTestId('recipient-picker');
  await expect(picker.locator('[data-company="A24"]')).toBeVisible();

  for (const company of ['A24', 'NEON']) {
    await expect(picker.locator(`[data-company="${company}"]`).getByRole('button', { name: 'Search again' })).toHaveCount(1);
  }
  // Nowhere else on the screen: not the lanes or the sheet's header,
  // and there is no separate officer lookup.
  await expect(pipeline.getByRole('button', { name: 'Search again' })).toHaveCount(2);
  await expect(pipeline.getByTestId('company-lanes').getByRole('button', { name: /search|find|officers/i })).toHaveCount(0);
  await expect(pipeline.getByTestId('sheet-header').getByRole('button', { name: /search|find|officers/i })).toHaveCount(0);
  // The rail's one Search button is for companies not yet searched: both have been.
  await expect(pipeline.locator('[data-rail]').getByRole('button', { name: /search|find|officers/i })).toHaveCount(1);
  await expect(pipeline.getByTestId('search-all')).toBeDisabled();
  await expect(pipeline.getByText(/look up officers/i)).toHaveCount(0);

  const search = picker.locator('[data-company="A24"]').getByTestId('company-search');
  await search.click();
  await expect(search).toBeDisabled();
  expect(server.runsCreated.map((r) => r.company_name)).toEqual(['A24']);
});

test('every member sends the club template: no picker, no saving, and an edit is what is sent', async ({ page }) => {
  const server = await mock(page, { people: PEOPLE, searched: ['A24'] });
  await page.goto('/scraper?view=company&companies=A24');
  const rail = page.locator('[data-section="campaign-pipeline"] [data-rail]');
  const message = rail.getByTestId('message-panel');
  const subject = message.getByLabel('Subject');
  await expect(subject).toHaveValue('Hello {first}');
  // One template for everyone: nothing to choose and nothing to save.
  await expect(message.getByLabel('Template')).toHaveCount(0);
  await expect(rail.getByRole('button', { name: /save as template/i })).toHaveCount(0);
  await expect.poll(() => server.builds.at(-1)).toMatchObject({ template_id: 11 });

  // An edited message is what is previewed and sent: the saved template no longer stands in for it.
  await subject.fill('Quick intro for {company}');
  await expect.poll(() => server.builds.some((b) => b.subject === 'Quick intro for {company}' && !('template_id' in b))).toBe(true);
});
