import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

const FOUND = [
  { id: 1, name: 'Jean Bartik', email: 'jean.bartik@a24films.com', title: 'Director of Operations', company: 'A24', person_level: 'working' },
  { id: 2, name: 'Klara Dan', email: 'klara.dan@a24films.com', title: 'VP Partnerships', company: 'A24', person_level: 'working' },
  { id: 3, name: 'Ada Lovelace', email: 'ada@a24films.com', title: 'Trustee', company: 'A24', person_level: 'board' },
  { id: 4, name: 'Alan Turing', email: 'alan@a24films.com', title: 'Chief Executive Officer', company: 'A24', person_level: 'executive', last_sent_at: '2026-09-02 10:00:00', last_campaign_name: 'Spring intro' },
  { id: 5, name: 'Grace Hopper', email: '', title: 'Head of Insight', company: 'A24', person_level: 'working' },
];

/** What the NEON search finds: two team leads, one of whom the import refuses,
 *  and a CEO who comes onto the list too but is not ticked. */
const PROSPECTS = [
  { id: 91, run_id: 9, first_name: 'Margaret', last_name: 'Hamilton', email: 'margaret@neon.com', title: 'Head of Partnerships', person_level: 'working', score: 90 },
  { id: 92, run_id: 9, first_name: 'Dorothy', last_name: 'Vaughan', email: 'dorothy@neon.com', title: 'Programme Lead', person_level: 'working', score: 80 },
  { id: 93, run_id: 9, first_name: 'Grace', last_name: 'Murray', email: 'grace@neon.com', title: 'Chief Executive Officer', person_level: 'executive', score: 70 },
];

/** What the club's contacts hold once the import has run. */
const IMPORTED = {
  91: { id: 6, name: 'Margaret Hamilton', email: 'margaret@neon.com', title: 'Head of Partnerships', company: 'NEON', person_level: 'working' },
  93: { id: 7, name: 'Grace Murray', email: 'grace@neon.com', title: 'Chief Executive Officer', company: 'NEON', person_level: 'executive' },
} as const;

/** A24 was searched earlier, so adding it starts nothing by itself. */
const A24_RUN = { id: 8, company_name: 'A24', status: 'completed', progress_pct: 100, progress_message: 'Done' };

async function mockPicker(page: Page, opts: { busy?: boolean; earlierRun?: boolean; builderRun?: boolean } = {}) {
  const built: Record<string, unknown>[] = [];
  const runsCreated: Record<string, unknown>[] = [];
  const imported: unknown[] = [];
  const contacts: Record<string, unknown>[] = [...FOUND];
  let runPolls = 0;
  let attempted = false;

  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const method = route.request().method();
    let body: unknown = {};
    let status = 200;
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') body = { items: contacts, total: contacts.length, limit: 2000, offset: 0 };
    else if (p === '/api/outreach/templates') {
      body = [{ id: 11, name: 'Intro', subject: 'Hello {first}', body: 'Hi {first}', use_case: 'intro', industry: null, scope: 'club', mine: false, editable: false }];
    } else if (p === '/api/yucgoutreach/runs' && method === 'POST') {
      runsCreated.push(route.request().postDataJSON());
      if (opts.busy) {
        // The server's one-search-per-member rule, met by a search this
        // page did not know about until it asked.
        attempted = true;
        status = 409;
        body = { detail: 'You already have a company search queued or running' };
      } else {
        body = { id: 9, status: 'queued' };
      }
    } else if (p === '/api/yucgoutreach/runs' && method === 'GET' && opts.earlierRun) {
      body = [A24_RUN, { id: 9, company_name: 'NEON', status: 'completed', progress_pct: 100, progress_message: 'Done' }];
    } else if (p === '/api/yucgoutreach/runs' && method === 'GET' && opts.builderRun) {
      // The catalog builder's run comes back only for a company the page
      // asked about, alongside the member's own (here none).
      body = url.searchParams.getAll('companies').includes('neon')
        ? [A24_RUN, { id: 9, company_name: 'NEON', status: 'completed', progress_pct: 100, progress_message: 'Done', origin: 'builder' }]
        : [A24_RUN];
    } else if (p === '/api/yucgoutreach/runs' && method === 'GET') {
      body = opts.busy && attempted
        ? [A24_RUN, { id: 10, company_name: 'Acme Corp', status: 'running', progress_pct: 30, progress_message: 'Website and web search…' }]
        : [A24_RUN];
    } else if (p === '/api/yucgoutreach/runs/9') {
      runPolls += 1;
      body = runPolls === 1
        ? { id: 9, company_name: 'NEON', status: 'running', progress_pct: 40, progress_message: 'Website and web search…' }
        : { id: 9, company_name: 'NEON', status: 'completed', progress_pct: 100, progress_message: 'Done — 2 verified prospects saved' };
    } else if (p === '/api/yucgoutreach/runs/9/prospects') {
      body = PROSPECTS;
    } else if (/^\/api\/yucgoutreach\/runs\/\d+\/prospects$/.test(p)) {
      body = [];
    } else if (p === '/api/yucgoutreach/runs/9/import-contacts') {
      const payload = route.request().postDataJSON();
      imported.push(payload);
      const ids: number[] = payload.prospect_ids;
      const results = ids.map((id) => {
        const person = IMPORTED[id as keyof typeof IMPORTED];
        if (!person) return { prospect_id: id, contact_id: null, outcome: 'skipped', reason: 'already worked by Alice' };
        contacts.push(person);
        return { prospect_id: id, contact_id: person.id, outcome: 'created' };
      });
      body = {
        created: results.filter((r) => r.outcome === 'created').length, updated: 0,
        skipped: results.filter((r) => r.outcome === 'skipped').length, results,
      };
    } else if (p === '/api/campaigns/build') {
      const payload = route.request().postDataJSON();
      if (!payload.preview_only) built.push(payload);
      body = payload.preview_only
        ? { recipients: (payload.contact_ids || []).length, ready: (payload.contact_ids || []).length, held: [], sample: null }
        : { campaign_id: 42, created: (payload.contact_ids || []).length, name: 'A24', held: [] };
    }
    else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/outreach/flows') body = [];
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    await route.fulfill({ status, json: body });
  });
  return { built, runsCreated, imported };
}

test('the email is written to the people who were ticked, not to everyone found', async ({ page }) => {
  const { built } = await mockPicker(page);
  await page.goto('/scraper?view=company&companies=A24');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const picker = pipeline.getByTestId('recipient-picker');
  await expect(picker).toBeVisible();

  // Four people have an address. The two who work there are ticked; the board
  // seat and the person already written to are not, because writing to either
  // is a decision, not a default. Nobody is folded away: everyone is on the
  // list, and the header says why each is out.
  await expect(pipeline.getByTestId('selected-count')).toHaveText('2 can be emailed');
  await expect(pipeline.getByTestId('send-reasons')).toHaveText('1 no address · 1 already written to · 1 not ticked');
  await expect(picker.getByRole('checkbox', { name: 'Write to Jean Bartik' })).toBeChecked();
  await expect(picker.getByRole('checkbox', { name: 'Write to Ada Lovelace' })).not.toBeChecked();
  await expect(picker.getByRole('checkbox', { name: 'Write to Alan Turing' })).not.toBeChecked();

  // Someone with no address cannot be ticked at all, rather than being ticked
  // and failing at send time.
  await expect(picker.getByRole('checkbox', { name: 'Write to Grace Hopper' })).toBeDisabled();

  // Untick one, add the board seat deliberately.
  await picker.getByRole('checkbox', { name: 'Write to Klara Dan' }).uncheck();
  await picker.getByRole('checkbox', { name: 'Write to Ada Lovelace' }).check();
  await expect(pipeline.getByTestId('selected-count')).toHaveText('2 can be emailed');
  await expect(rail.getByTestId('send-button')).toHaveText('Send to 2');

  // Exactly the ticks, and nothing the member turned off, are sent to.
  await rail.getByTestId('send-button').click();
  await page.getByRole('dialog').getByRole('button', { name: 'Send 2' }).click();
  await expect(rail.getByTestId('sent-summary')).toContainText('Queued 2 emails');
  expect(built).toHaveLength(1);
  expect(built[0]).toMatchObject({ contact_ids: [1, 3], template_id: 11 });
});

test('Search looks for a company with nobody on file, and the people it finds are on the list ticked, with no Add step', async ({ page }) => {
  const { runsCreated, imported } = await mockPicker(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const picker = pipeline.getByTestId('recipient-picker');
  const neon = picker.locator('[data-company="NEON"]');
  const neonLane = pipeline.locator('[data-lane="NEON"]');
  // Wait for the people already on file, so the row states are settled.
  await expect(picker.getByText('Jean Bartik')).toBeVisible();

  // NEON has never been searched, so Search looks for it - and only it.
  await expect(neonLane).toHaveAttribute('data-state', 'idle');
  await expect(pipeline.getByTestId('search-all')).toHaveText('Search 1 company');
  await pipeline.getByTestId('search-all').click();
  await expect(neonLane).toHaveAttribute('data-state', 'searching');
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0].company_name).toBe('NEON');
  // No goal typed: the server sizes it to the company.
  expect(runsCreated[0].max_prospects).toBeUndefined();

  // Everyone it finds goes onto the list as soon as it finishes: the default
  // tick chooses the team leads, and the CEO is there but not ticked.
  await expect(neonLane).toHaveAttribute('data-state', 'done', { timeout: 15000 });
  await expect.poll(() => imported.length).toBe(1);
  expect(imported[0]).toEqual({ prospect_ids: [91, 92, 93] });
  await expect(picker.getByRole('checkbox', { name: 'Write to Margaret Hamilton' })).toBeChecked();
  await expect(picker.getByRole('checkbox', { name: 'Write to Grace Murray' })).not.toBeChecked();
  await expect(neon.locator('[data-found]')).toHaveCount(1);
  await expect(pipeline.getByRole('button', { name: /^Add (Margaret|Dorothy|Grace)/ })).toHaveCount(0);

  // A person the server refuses stays listed with the reason, and is counted
  // as held rather than dropped.
  await expect(neon.getByText('held: already worked by Alice')).toBeVisible();
  await expect(pipeline.getByTestId('selected-count')).toHaveText('3 can be emailed');
  await expect(pipeline.getByTestId('send-reasons')).toContainText('1 held');
  await expect(neonLane.getByTestId('lane-state')).toHaveText('1 can be emailed');
});

test('a second search while one is running waits its turn, and a 409 is never shown as an error', async ({ page }) => {
  await mockPicker(page, { busy: true });
  await page.goto('/scraper?view=company&companies=NEON');
  await page.getByTestId('search-all').click();

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const neonLane = pipeline.locator('[data-lane="NEON"]');

  await expect(neonLane).toHaveAttribute('data-state', 'queued');
  await expect(pipeline.getByRole('status').filter({ hasText: 'Already searching Acme Corp' })).toBeVisible();
  await expect(pipeline.getByRole('alert')).toHaveCount(0);
});

test('seniority narrows who is shown and who select-all reaches', async ({ page }) => {
  await mockPicker(page);
  await page.goto('/scraper?view=company&companies=A24');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  await pipeline.getByLabel('Seniority').selectOption('board');
  await pipeline.getByLabel(/Hide the \d+ already written to/).uncheck();

  // The filter is what "select all shown" means, so a member cannot widen the
  // send by accident while looking at a narrowed list.
  await expect(pipeline.getByText('Ada Lovelace')).toBeVisible();
  await expect(pipeline.getByText('Jean Bartik')).toHaveCount(0);
  await pipeline.getByRole('button', { name: /^Clear$/ }).click();
  await pipeline.getByRole('button', { name: /^Select all shown/ }).click();
  await expect(pipeline.getByTestId('selected-count')).toHaveText('1 can be emailed');
});

test('senior leaders are ticked when asked for; the very top, board seats and page text are not', async ({ page }) => {
  await mockPicker(page);
  // Registered after the shared mock, so it answers first.
  await page.route('**/api/contacts?*', route => route.fulfill({ json: {
    items: [
      { id: 1, name: 'Jean Bartik', email: 'jean.bartik@a24films.com', title: 'Director of Operations', company: 'A24', person_level: 'working' },
      { id: 6, name: 'Anat Ashkenazi', email: 'anat@a24films.com', title: 'SVP, Chief Financial Officer', company: 'A24', person_level: 'working' },
      { id: 7, name: 'Kent Walker', email: 'kent@a24films.com', title: 'Vice President, Partnerships', company: 'A24', person_level: 'executive' },
      { id: 8, name: 'Katy George', email: 'katy@a24films.com', title: 'Corporate Vice President', company: 'A24', person_level: 'executive' },
      { id: 9, name: 'Transformation Leader', email: 'tl@a24films.com', title: 'Shaping the future of work', company: 'A24', person_level: 'working' },
      { id: 10, name: 'Steve Mathias B1a579', email: 'steve@a24films.com', title: 'Account Manager', company: 'A24', person_level: 'working' },
      // A proxy names a director by their committee; it read as unknown and was ticked.
      { id: 11, name: 'Fidji Simo', email: 'fidji@a24films.com', title: 'Compensation and Talent Management Committee', company: 'A24' },
      { id: 12, name: 'Daniel Katz', email: 'daniel@a24films.com', title: 'Co-Founder and Chief Executive Officer', company: 'A24', person_level: 'executive' },
    ],
    total: 8, limit: 2000, offset: 0,
  } }));
  await page.goto('/scraper?view=company&companies=A24');

  const picker = page.locator('[data-section="campaign-pipeline"]').getByTestId('recipient-picker');
  const box = (name: string) => picker.getByRole('checkbox', { name: `Write to ${name}` });
  await expect(box('Jean Bartik')).toBeChecked();
  // Team leads and senior leaders are chosen by default, so a chief officer
  // and a corporate VP are ticked; the CEO and a board seat are not.
  await expect(box('Anat Ashkenazi')).toBeChecked();
  await expect(box('Katy George')).toBeChecked();
  await expect(box('Daniel Katz')).not.toBeChecked();
  await expect(box('Fidji Simo')).not.toBeChecked();
  await expect(picker.getByText('Very senior · rarely replies')).toHaveCount(4);
  // A plain vice president is often the right person, and stays.
  await expect(box('Kent Walker')).toBeChecked();
  // Page text stored as a name is flagged; a profile id on a real name is not.
  await expect(box('Transformation Leader')).not.toBeChecked();
  await expect(picker.getByText('Not a person? Check the name')).toHaveCount(1);
  await expect(box('Steve Mathias B1a579')).toBeChecked();
  // The very top is still a deliberate choice away.
  await box('Daniel Katz').check();
  await expect(box('Daniel Katz')).toBeChecked();
});

test("what an earlier visit's search found goes onto the list by itself, with no Add step", async ({ page }) => {
  const { imported, runsCreated } = await mockPicker(page, { earlierRun: true });
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const picker = page.locator('[data-section="campaign-pipeline"]').getByTestId('recipient-picker');
  await expect.poll(() => imported.length).toBe(1);
  expect(imported[0]).toEqual({ prospect_ids: [91, 92, 93] });
  await expect(picker.getByRole('checkbox', { name: 'Write to Margaret Hamilton' })).toBeChecked();
  await expect(picker.getByText(/not on file yet/)).toHaveCount(0);
  await expect(picker.getByRole('button', { name: /^Add / })).toHaveCount(0);
  // It was searched earlier, so it is not searched again.
  expect(runsCreated).toHaveLength(0);
});

test('what the catalog builder found is listed for every member, and cannot be deleted', async ({ page }) => {
  const { imported } = await mockPicker(page, { builderRun: true });
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const neon = pipeline.getByTestId('recipient-picker').locator('[data-company="NEON"]');
  // The import is the server's: its rules decide who lands, and it says why
  // anyone does not.
  await expect.poll(() => imported.length).toBe(1);
  expect(imported[0]).toEqual({ prospect_ids: [91, 92, 93] });
  await expect(neon.getByRole('checkbox', { name: 'Write to Margaret Hamilton' })).toBeChecked();
  await expect(neon.getByText('held: already worked by Alice')).toBeVisible();

  const lane = pipeline.locator('[data-lane="NEON"]');
  await lane.hover();
  await lane.getByLabel('More for NEON').click();
  await expect(lane.getByRole('button', { name: 'Export .xlsx' })).toBeVisible();
  await expect(lane.getByRole('button', { name: 'Delete run' })).toHaveCount(0);
});
