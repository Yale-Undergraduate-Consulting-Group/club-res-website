import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

const FOUND = [
  { id: 1, name: 'Jean Bartik', email: 'jean.bartik@a24films.com', title: 'Director of Operations', company: 'A24', person_level: 'working' },
  { id: 2, name: 'Klara Dan', email: 'klara.dan@a24films.com', title: 'VP Partnerships', company: 'A24', person_level: 'working' },
  { id: 3, name: 'Ada Lovelace', email: 'ada@a24films.com', title: 'Trustee', company: 'A24', person_level: 'board' },
  { id: 4, name: 'Alan Turing', email: 'alan@a24films.com', title: 'Chief Executive Officer', company: 'A24', person_level: 'executive', last_sent_at: '2026-09-02 10:00:00', last_campaign_name: 'Spring intro' },
  { id: 5, name: 'Grace Hopper', email: '', title: 'Head of Insight', company: 'A24', person_level: 'working' },
];

/** What the NEON search finds: two people, one of whom the import refuses,
 *  and a CEO the page leaves for the member to add on purpose. */
const PROSPECTS = [
  { id: 91, run_id: 9, first_name: 'Margaret', last_name: 'Hamilton', email: 'margaret@neon.com', title: 'Head of Partnerships', score: 90 },
  { id: 92, run_id: 9, first_name: 'Dorothy', last_name: 'Vaughan', email: 'dorothy@neon.com', title: 'Programme Lead', score: 80 },
  { id: 93, run_id: 9, first_name: 'Grace', last_name: 'Murray', email: 'grace@neon.com', title: 'Chief Executive Officer', score: 70 },
];

const AFTER_ADD = {
  id: 6, name: 'Margaret Hamilton', email: 'margaret@neon.com', title: 'Head of Partnerships',
  company: 'NEON', person_level: 'working',
};

async function mockPicker(page: Page, opts: { busy?: boolean; earlierRun?: boolean } = {}) {
  const built: Record<string, unknown>[] = [];
  const drafted: Record<string, unknown>[] = [];
  const runsCreated: Record<string, unknown>[] = [];
  const imported: unknown[] = [];
  let runPolls = 0;
  let added = false;
  let attempted = false;

  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const method = route.request().method();
    let body: unknown = {};
    let status = 200;
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') {
      const items = added ? [...FOUND, AFTER_ADD] : FOUND;
      body = { items, total: items.length, limit: 800, offset: 0 };
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
      body = [{ id: 9, company_name: 'NEON', status: 'completed', progress_pct: 100, progress_message: 'Done' }];
    } else if (p === '/api/yucgoutreach/runs' && method === 'GET') {
      body = opts.busy && attempted
        ? [{ id: 8, company_name: 'Acme Corp', status: 'running', progress_pct: 30, progress_message: 'Website and web search…' }]
        : [];
    } else if (p === '/api/yucgoutreach/runs/9') {
      runPolls += 1;
      body = runPolls === 1
        ? { id: 9, company_name: 'NEON', status: 'running', progress_pct: 40, progress_message: 'Website and web search…' }
        : { id: 9, company_name: 'NEON', status: 'completed', progress_pct: 100, progress_message: 'Done — 2 verified prospects saved' };
    } else if (p === '/api/yucgoutreach/runs/9/prospects') {
      body = PROSPECTS;
    } else if (p === '/api/yucgoutreach/runs/9/import-contacts') {
      const payload = route.request().postDataJSON();
      imported.push(payload);
      const ids: number[] = payload.prospect_ids;
      added = ids.includes(91);
      body = {
        created: ids.includes(91) ? 1 : 0, updated: 0, skipped: ids.includes(92) ? 1 : 0,
        results: ids.map((id) => (id === 91
          ? { prospect_id: 91, contact_id: 6, outcome: 'created' }
          : { prospect_id: 92, contact_id: null, outcome: 'skipped', reason: 'already worked by Alice' })),
      };
    } else if (p === '/api/campaigns/draft-template') {
      const payload = route.request().postDataJSON();
      drafted.push(payload);
      body = payload.per_company
        ? { messages: Object.fromEntries((payload.companies || []).map((c: string) => [
            c, { subject: `${c} and Yale`, body: `Hi {first}, about {company}.` }])), grounded: payload.companies }
        : { subject: 'One subject', body: 'Hi {first}.' };
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
    else if (p === '/api/yucgoutreach/role-suggestions') body = { company: url.searchParams.get('company'), roles: [], equivalents: [], sources: {} };
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    await route.fulfill({ status, json: body });
  });
  return { built, drafted, runsCreated, imported };
}

test('the campaign is written to the people who were ticked, not to everyone found', async ({ page }) => {
  await mockPicker(page);
  await page.goto('/scraper?view=company&companies=A24');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const picker = pipeline.getByTestId('recipient-picker');
  await expect(picker).toBeVisible();

  // Four people have an address. The two who work there are ticked; the board
  // seat and the person already written to are not, because writing to either
  // is a decision, not a default.
  await expect(pipeline.getByTestId('selected-count')).toHaveText('2 of 4 selected');
  await expect(picker.getByRole('checkbox', { name: 'Write to Jean Bartik' })).toBeChecked();
  await expect(picker.getByRole('checkbox', { name: 'Write to Ada Lovelace' })).not.toBeChecked();

  // Someone with no address cannot be ticked at all, rather than being ticked
  // and failing at send time.
  await expect(picker.getByRole('checkbox', { name: 'Write to Grace Hopper' })).toBeDisabled();

  // Untick one, add the board seat deliberately.
  await picker.getByRole('checkbox', { name: 'Write to Klara Dan' }).uncheck();
  await picker.getByRole('checkbox', { name: 'Write to Ada Lovelace' }).check();
  await expect(pipeline.getByTestId('selected-count')).toHaveText('2 of 4 selected');

  await rail.getByRole('button', { name: /^Write to these 2/ }).click();
  // From here the sheet is the recipients and nothing else: no ticks, no
  // filters, and nobody who was not chosen.
  await expect(picker).toHaveAttribute('data-mode', 'review');
  await expect(pipeline.getByTestId('selected-count')).toHaveText('2 recipients');
  await expect(picker.getByText('Klara Dan')).toHaveCount(0);
  await expect(picker.getByRole('checkbox')).toHaveCount(0);

  // Exactly the ticks, and nothing the member turned off, go to Drafts.
  await rail.getByRole('button', { name: /^Write to these 2 in Drafts/ }).click();
  await expect(page).toHaveURL(/contact_ids=1%2C3$/);
});

test('a company with nobody on file is searched from its lane, and the people it finds are ticked, not queued for adding', async ({ page }) => {
  const { runsCreated, imported } = await mockPicker(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const picker = pipeline.getByTestId('recipient-picker');
  const neon = picker.locator('[data-company="NEON"]');
  const neonLane = pipeline.locator('[data-lane="NEON"]');
  // Wait for the people already on file, so the row states are settled.
  await expect(picker.getByText('Jean Bartik')).toBeVisible();

  // Titles the member types win over who-to-look-for, and reach the search
  // as typed.
  await rail.getByRole('button', { name: 'Other titles…' }).click();
  await rail.getByLabel('Other titles').fill('Head of Partnerships');

  // Searching used to mean a second panel with its own company field. The
  // company that needs people carries the button that finds them, on its lane.
  await neonLane.hover();
  await neonLane.getByRole('button', { name: 'Find people' }).click();
  await expect(neonLane).toHaveAttribute('data-state', 'searching');

  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0].company_name).toBe('NEON');
  expect(runsCreated[0].title_hints).toBe('Head of Partnerships');
  expect(runsCreated[0].max_prospects).toBe(60);

  // A search started here adds the people the default tick would choose as
  // soon as it finishes, so they are ticked, not waiting under "Add". The
  // CEO is left under found: reaching one is a deliberate act.
  await expect(neonLane).toHaveAttribute('data-state', 'done', { timeout: 15000 });
  await expect.poll(() => imported.length).toBe(1);
  expect(imported[0]).toEqual({ prospect_ids: [91, 92] });
  await expect(picker.getByRole('checkbox', { name: 'Write to Margaret Hamilton' })).toBeChecked();
  await expect(neon.locator('[data-found="91"]')).toHaveCount(0);

  // A person the server refuses stays listed with the reason, and cannot be
  // added again.
  await expect(neon.getByText('not added: already worked by Alice')).toBeVisible();
  await expect(neon.getByRole('button', { name: 'Add Dorothy Vaughan' })).toHaveCount(0);

  // The CEO is folded away - a large company's search no longer opens on its
  // chief officers - and still one click from a deliberate add.
  await expect(neon.getByRole('button', { name: 'Add Grace Murray' })).toHaveCount(0);
  await neon.getByRole('button', { name: 'Show 1 more: very senior, or the name is unclear' }).click();
  await expect(neon.getByRole('button', { name: 'Add Grace Murray' })).toBeVisible();

});

test('a second search while one is running waits its turn, and a 409 is never shown as an error', async ({ page }) => {
  await mockPicker(page, { busy: true });
  await page.goto('/scraper?view=company&companies=NEON');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const neonLane = pipeline.locator('[data-lane="NEON"]');
  await neonLane.hover();
  await neonLane.getByRole('button', { name: 'Find people' }).click();

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
  await expect(pipeline.getByTestId('selected-count')).toHaveText('1 of 4 selected');
});

test('the very senior and names that are not people are shown but never ticked by default', async ({ page }) => {
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
    ],
    total: 7, limit: 800, offset: 0,
  } }));
  await page.goto('/scraper?view=company&companies=A24');

  const picker = page.locator('[data-section="campaign-pipeline"]').getByTestId('recipient-picker');
  const box = (name: string) => picker.getByRole('checkbox', { name: `Write to ${name}` });
  await expect(box('Jean Bartik')).toBeChecked();
  // A chief officer and a corporate VP: too senior to answer a cold email.
  await expect(box('Anat Ashkenazi')).not.toBeChecked();
  await expect(box('Katy George')).not.toBeChecked();
  await expect(box('Fidji Simo')).not.toBeChecked();
  await expect(picker.getByText('Very senior · rarely replies')).toHaveCount(3);
  // A plain vice president is often the right person, and stays.
  await expect(box('Kent Walker')).toBeChecked();
  // Page text stored as a name is flagged; a profile id on a real name is not.
  await expect(box('Transformation Leader')).not.toBeChecked();
  await expect(picker.getByText('Not a person? Check the name')).toHaveCount(1);
  await expect(box('Steve Mathias B1a579')).toBeChecked();
  // Still a deliberate choice away.
  await box('Anat Ashkenazi').check();
  await expect(box('Anat Ashkenazi')).toBeChecked();
});

test("what an earlier visit's search found is shown, never added behind the member's back", async ({ page }) => {
  const { imported } = await mockPicker(page, { earlierRun: true });
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const neon = page.locator('[data-section="campaign-pipeline"]').getByTestId('recipient-picker').locator('[data-company="NEON"]');
  await expect(neon.getByText('3 found · not on file yet')).toBeVisible();
  await expect(neon.getByRole('button', { name: 'Add Margaret Hamilton' })).toBeVisible();
  await page.waitForTimeout(1000);
  expect(imported).toHaveLength(0);
});
