import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

const PEOPLE = [
  { id: 1, name: 'Jean Bartik', email: 'jean.bartik@a24films.com', title: 'Director of Operations', company: 'A24', person_level: 'working' },
  { id: 2, name: 'Klara Dan', email: 'klara.dan@a24films.com', title: 'Head of Partnerships', company: 'A24', person_level: 'working' },
];

/** What the server keeps for a finished run: its words, and the research behind them. */
function finishedRun(id: number, company: string, research: Record<string, unknown>, message = 'Done') {
  return { id, company_name: company, status: 'completed', progress_pct: 100, progress_message: message, research_json: JSON.stringify(research) };
}

/** A24 and NEON were both searched earlier, so adding them starts nothing by itself. */
async function mockLanes(page: Page) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    let body: unknown = {};
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') body = { items: PEOPLE, total: PEOPLE.length, limit: 800, offset: 0 };
    else if (p === '/api/campaigns/build') body = { recipients: 2, ready: 2, held: [], sample: { email: 'jean.bartik@a24films.com', subject: 'A24', body: 'Hi Jean.' } };
    else if (p === '/api/outreach/templates') body = [{ id: 11, name: 'Intro', subject: 'Hello {first}', body: 'Hi {first}', use_case: 'intro', industry: null, scope: 'club', mine: false, editable: false }];
    else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/yucgoutreach/runs' && route.request().method() === 'POST') body = { id: 5, status: 'queued' };
    else if (p === '/api/yucgoutreach/runs') body = [finishedRun(8, 'A24', {}), finishedRun(9, 'NEON', {})];
    else if (/^\/api\/yucgoutreach\/runs\/\d+\/prospects$/.test(p)) body = [];
    else if (p === '/api/outreach/flows') body = [];
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    await route.fulfill({ json: body });
  });
}

test('the lanes, the sheet and the message stay on screen together', async ({ page }) => {
  await mockLanes(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const lanes = pipeline.getByTestId('company-lanes');
  const picker = pipeline.getByTestId('recipient-picker');

  // The flowchart of where each company stands sits above the people, and the
  // message and Send are in the rail beside them: one screen, no steps.
  await expect(picker).toBeVisible();
  await expect(lanes).toBeVisible();
  await expect(rail.getByTestId('message-panel')).toBeVisible();
  await expect(rail.getByTestId('send-button')).toBeVisible();
  // One number, in the header, in each lane and on the button.
  await expect(lanes.getByText('2 companies · 2 can be emailed')).toBeVisible();
  await expect(pipeline.getByTestId('selected-count')).toHaveText('2 can be emailed');
  await expect(lanes.locator('[data-lane="A24"]').getByTestId('lane-state')).toHaveText('2 can be emailed');
  await expect(rail.getByTestId('send-button')).toHaveText('Send to 2');
  // The company nobody has been found at says so, rather than looking done.
  await expect(lanes.locator('[data-lane="NEON"]').getByTestId('lane-state')).toHaveText('Nobody found');

  // Selected and unselected choices use the same control geometry; choosing a
  // level changes only its colour, never the size of the row.
  const who = pipeline.getByRole('group', { name: 'Who to look for' });
  const team = who.getByRole('button', { name: 'Team leads' });
  const board = who.getByRole('button', { name: 'Board' });
  const before = await Promise.all([team.boundingBox(), board.boundingBox()]);
  expect(before[0]?.height).toBe(before[1]?.height);
  await board.click();
  const after = await Promise.all([team.boundingBox(), board.boundingBox()]);
  expect(after[0]?.height).toBe(after[1]?.height);
  expect(after[0]?.height).toBe(before[0]?.height);
});

test('the state column lines up regardless of how long each row\'s text is', async ({ page }, testInfo) => {
  // "Nobody found" and "2 can be emailed" are very different lengths. A
  // natural-width column shifts with every row's own text, so what looked
  // like a flowchart read as a ragged list instead. Fixed-width columns are
  // a desktop layout: a phone does not have 450px of row to spare, so below
  // `sm` each lane wraps to two lines instead and alignment does not apply.
  test.skip(testInfo.project.name !== 'desktop', 'fixed-width columns only exist at sm and above');
  await mockLanes(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const lanes = page.locator('[data-section="campaign-pipeline"]').getByTestId('company-lanes');
  await expect(lanes.locator('[data-lane="A24"]').getByTestId('lane-state')).toHaveText('2 can be emailed');
  await expect(lanes.locator('[data-lane="NEON"]').getByTestId('lane-state')).toHaveText('Nobody found');

  const a24Box = await lanes.locator('[data-lane="A24"]').getByTestId('lane-state').boundingBox();
  const neonBox = await lanes.locator('[data-lane="NEON"]').getByTestId('lane-state').boundingBox();
  expect(a24Box?.x).toBeCloseTo(neonBox?.x ?? -1, 0);
});

test('the lanes panel keeps its own header pinned while the surface scrolls, same as the sheet', async ({ page }) => {
  await mockLanes(page);
  const many = Array.from({ length: 20 }, (_, i) => `Company ${i + 1}`);
  await page.goto(`/scraper?view=company&companies=${encodeURIComponent(many.join(','))}`);

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const lanesHeader = pipeline.getByTestId('lanes-header');
  await expect(lanesHeader).toBeVisible();
  await expect(lanesHeader.getByRole('heading', { name: 'Company progress' })).toBeVisible();
  const position = await lanesHeader.evaluate((el) => getComputedStyle(el).position);
  expect(position).toBe('sticky');
});

test('past six companies the lanes fold behind a count so the sheet stays reachable', async ({ page }, testInfo) => {
  await mockLanes(page);
  const many = Array.from({ length: 20 }, (_, i) => `Company ${i + 1}`);
  await page.goto(`/scraper?view=company&companies=${encodeURIComponent(many.join(','))}`);

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const lanes = pipeline.getByTestId('company-lanes');
  await expect(lanes.getByText('20 companies · 0 can be emailed')).toBeVisible();
  await expect(lanes.locator('[data-lane]')).toHaveCount(6);
  await expect(lanes.locator('[data-lane="Company 6"]')).toBeVisible();
  await expect(lanes.locator('[data-lane="Company 7"]')).toHaveCount(0);

  // The 340px budget assumes the fixed single-line row height that only
  // applies at `sm` and above; below it each lane wraps to two lines and
  // grows to fit, so the same cap does not apply.
  if (testInfo.project.name === 'desktop') {
    const box = await lanes.boundingBox();
    expect(box?.height ?? 0).toBeLessThanOrEqual(340);
  }


  await lanes.getByRole('button', { name: '14 more' }).click();
  await expect(lanes.locator('[data-lane]')).toHaveCount(20);
  await lanes.getByRole('button', { name: 'Show fewer' }).click();
  await expect(lanes.locator('[data-lane]')).toHaveCount(6);
});

test('passing the pointer over a lane does not focus it, and each lane shows its search status', async ({ page }, testInfo) => {
  // Hover used to focus a lane. Focus re-renders the rail and can raise the
  // domain warning above the panel, so the lanes jumped under the pointer and
  // two rows lit up at once.
  test.skip(testInfo.project.name !== 'desktop', 'hover is a pointer-device behaviour');
  await mockLanes(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const lanes = page.locator('[data-section="campaign-pipeline"]').getByTestId('company-lanes');
  const a24 = lanes.locator('[data-lane="A24"]');
  const neon = lanes.locator('[data-lane="NEON"]');
  await expect(a24).toHaveAttribute('data-focused', 'true');
  await neon.hover();
  await expect(a24).toHaveAttribute('data-focused', 'true');
  await expect(neon).not.toHaveAttribute('data-focused', 'true');
  await neon.getByTestId('lane-state').click();
  await expect(neon).toHaveAttribute('data-focused', 'true');

  // The status is visible without hovering.
  await page.mouse.move(0, 0);
  await expect(a24.getByTestId('lane-state')).toBeVisible();
  await expect(neon.getByTestId('lane-state')).toHaveText('Nobody found');
});

test('a company in the sheet folds to its header and opens again', async ({ page }) => {
  await mockLanes(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const picker = page.locator('[data-section="campaign-pipeline"]').getByTestId('recipient-picker');
  const group = picker.locator('[data-company="A24"]');
  await expect(group.getByText('Jean Bartik')).toBeVisible();

  await group.getByRole('button', { name: 'Hide people at A24' }).click();
  await expect(group.getByText('Jean Bartik')).toBeHidden();
  // The header keeps the count and the tick-all box.
  await expect(group.getByTestId('group-count')).toHaveText('2 can be emailed · 2 listed');
  await expect(group.getByRole('checkbox', { name: 'Select everyone at A24' })).toBeChecked();

  await group.getByRole('button', { name: 'Show people at A24' }).click();
  await expect(group.getByText('Jean Bartik')).toBeVisible();
});

test('the one search action is each company\'s Search again in the people sheet', async ({ page }) => {
  // Searching starts with the Search button in the rail. The way to ask again
  // is a button in the company's own heading in the sheet: none on the lanes
  // or in the sheet's pinned header, and no separate officer lookup.
  await mockLanes(page);
  const posted: Record<string, unknown>[] = [];
  await page.route('**/api/yucgoutreach/runs', route => {
    if (route.request().method() === 'POST') {
      posted.push(route.request().postDataJSON());
      return route.fulfill({ json: { id: 50, status: 'queued' } });
    }
    return route.fulfill({ json: [finishedRun(8, 'A24', {}), finishedRun(9, 'NEON', {})] });
  });
  await page.route(/\/api\/yucgoutreach\/runs\/50$/, route => route.fulfill({
    json: { id: 50, company_name: 'NEON', status: 'running', progress_pct: 20, progress_message: 'Searching the web' },
  }));
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const picker = pipeline.getByTestId('recipient-picker');
  await expect(picker.locator('[data-company="A24"]')).toBeVisible();
  for (const name of ['A24', 'NEON']) {
    await expect(picker.locator(`[data-company="${name}"]`).getByRole('button', { name: 'Search again' })).toHaveCount(1);
  }
  await expect(pipeline.getByRole('button', { name: 'Search again' })).toHaveCount(2);
  await expect(pipeline.getByTestId('company-lanes').getByRole('button', { name: /search|find|officers/i })).toHaveCount(0);
  await expect(pipeline.getByTestId('sheet-header').getByRole('button', { name: /search|find|officers/i })).toHaveCount(0);
  // The rail's one Search button is for companies not yet searched: both have been.
  await expect(pipeline.locator('[data-rail]').getByRole('button', { name: /search|find|officers/i })).toHaveCount(1);
  await expect(pipeline.getByTestId('search-all')).toBeDisabled();
  await expect(pipeline.getByText(/Look up officers/)).toHaveCount(0);

  // Asking again starts one search for that company and the button waits for it.
  const neon = picker.locator('[data-company="NEON"]').getByTestId('company-search');
  await neon.click();
  await expect(neon).toBeDisabled();
  expect(posted.map((body) => body.company_name)).toEqual(['NEON']);
  const box = await neon.boundingBox();
  const viewport = page.viewportSize();
  expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual((viewport?.width ?? 0) + 1);
});

const PAUSED = 'People search is paused: the search service is out of credit. A maintainer needs to top it up.';

/** mockLanes with its own people and runs, registered after it so they answer first. */
async function mockPeople(page: Page, opts: { people?: Record<string, unknown>[]; runs?: Record<string, unknown>[]; prospects?: Record<string, unknown>[] }) {
  await mockLanes(page);
  await page.route('**/api/contacts?*', route => route.fulfill({
    json: { items: opts.people ?? [], total: (opts.people ?? []).length, limit: 800, offset: 0 },
  }));
  await page.route('**/api/yucgoutreach/runs?*', route => route.fulfill({ json: opts.runs ?? [] }));
  await page.route('**/api/yucgoutreach/runs/*/prospects**', route => route.fulfill({ json: opts.prospects ?? [] }));
  await page.route(/\/api\/yucgoutreach\/runs\/10$/, route => route.fulfill({
    json: { id: 10, company_name: 'Acme', status: 'running', progress_pct: 35, progress_message: 'Searching the web' },
  }));
}

test('a search that could not run says it is paused, never that nobody was found', async ({ page }) => {
  await mockPeople(page, {
    runs: [finishedRun(9, 'NEON', {
      search_errors: { count: 17, queries: 17, all_failed: true, limit_reached: true, kind: 'out_of_credit', message: PAUSED },
      shortfall: { target: 60, found: 0, reason: 'search_failed' },
    }, `No contacts saved. Website 0 · roster 0 · web 0. ${PAUSED}`)],
  });
  await page.goto('/scraper?view=company&companies=NEON');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  await expect(pipeline.locator('[data-lane="NEON"]').getByTestId('lane-state')).toHaveText('Search paused');
  // One plain sentence, once at the top and as the step's own note - and the
  // empty-company copy that blames the company is nowhere.
  await expect(pipeline.getByTestId('search-paused')).toHaveText(PAUSED);
  await expect(pipeline.getByTestId('no-people-note')).toHaveText(PAUSED);
  await expect(pipeline.locator('[data-company="NEON"]').getByTestId('company-status')).toHaveText('Search paused');
  await expect(pipeline.getByText(/Nobody on record|Nobody found/)).toHaveCount(0);
  // No raw provider text or key anywhere on the page.
  await expect(page.locator('body')).not.toContainText(/WALLET|monid_|402/);
  // The company's own Search again is there to try once more.
  await expect(pipeline.locator('[data-company="NEON"]').getByRole('button', { name: 'Search again' })).toBeEnabled();
});

test('a search that ran and found nobody says for whom, and follows who to look for', async ({ page }) => {
  await mockPeople(page, { runs: [finishedRun(9, 'NEON', {}, 'No contacts saved. Website 0 · roster 0 · web 0')] });
  await page.goto('/scraper?view=company&companies=NEON');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const status = pipeline.locator('[data-company="NEON"]').getByTestId('company-status');
  const who = pipeline.locator('[data-rail]').getByRole('group', { name: 'Who to look for' });
  await expect(pipeline.locator('[data-lane="NEON"]').getByTestId('lane-state')).toHaveText('Nobody found');
  await expect(status).toHaveText('Nobody found for Team leads and Senior leaders (try Board)');
  await expect(pipeline.getByTestId('search-paused')).toHaveCount(0);

  await who.getByRole('button', { name: 'Team leads' }).click();
  await expect(status).toHaveText('Nobody found for Senior leaders (try Team leads)');
  await who.getByRole('button', { name: 'No title shown' }).click();
  await expect(status).toHaveText('Nobody found for Senior leaders and No title shown (try Team leads)');
});

test('people are grouped by company, the chosen level first, duplicates merged, each company with its status', async ({ page }) => {
  await mockPeople(page, {
    people: [
      // The same person under two addresses: the company's own site beats a web search.
      { id: 1, name: 'Jean M. Bartik', email: 'jbartik@a24films.com', title: 'Director of Operations', company: 'A24', person_level: 'working', contact_source: 'web_discovery' },
      { id: 2, name: 'Jean Bartik', email: 'jean.bartik@a24films.com', title: 'Director of Operations', company: 'A24', person_level: 'working', contact_source: 'domain_scrape', linkedin_url: 'https://www.linkedin.com/in/jean-bartik' },
      { id: 3, name: 'Lena Chief', email: 'lena@a24films.com', title: 'Chief of Staff', company: 'A24', person_level: 'executive', contact_source: 'roster_sec' },
      { id: 4, name: 'Tomas Board', email: 'tomas@a24films.com', title: 'Trustee', company: 'A24', person_level: 'board', contact_source: 'roster_sec' },
      { id: 5, name: 'Klara Dan', email: 'klara@a24films.com', title: 'Head of Partnerships', company: 'A24', person_level: 'working', contact_source: 'web_discovery' },
    ],
    runs: [
      finishedRun(8, 'A24', {}),
      finishedRun(9, 'NEON', {}, 'No contacts saved. Website 0 · roster 0 · web 0'),
      { id: 10, company_name: 'Acme', status: 'running', progress_pct: 35, progress_message: 'Searching the web' },
    ],
  });
  await page.goto('/scraper?view=company&companies=A24,NEON,Acme');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const picker = pipeline.getByTestId('recipient-picker');
  const a24 = picker.locator('[data-company="A24"]');

  // One Jean, not two - and the row carries what a member needs to judge it.
  await expect(a24.getByText(/Jean (M\. )?Bartik/)).toHaveCount(1);
  const jean = a24.locator('li', { hasText: 'Jean Bartik' });
  await expect(jean).toContainText('Director of Operations');
  await expect(jean).toContainText('A24');
  await expect(jean.getByTestId('match-reason')).toHaveText('Team lead: "Director of" in the title');
  await expect(jean.getByTestId('person-source')).toContainText('Source: Company website');
  await expect(jean.getByRole('link', { name: 'LinkedIn profile' })).toHaveAttribute('href', 'https://www.linkedin.com/in/jean-bartik');
  await expect(jean.getByRole('checkbox', { name: 'Write to Jean Bartik' })).toBeChecked();

  // The people at the chosen level come first, and nobody is folded away: the
  // rest follow in order, the board seat last.
  const names = () => a24.getByRole('checkbox').evaluateAll((els) => els
    .map((el) => el.getAttribute('aria-label') || '').filter((label) => label.startsWith('Write to ')));
  await expect.poll(names).toEqual(['Write to Jean Bartik', 'Write to Klara Dan', 'Write to Lena Chief', 'Write to Tomas Board']);
  await expect(a24.locator('li', { hasText: 'Lena Chief' }).getByTestId('match-reason')).toHaveText('Senior leader: "Chief of Staff" in the title');

  // Every company says where its search stands.
  await expect(a24.getByTestId('company-status')).toHaveText('3 can be emailed · 4 people found · 3 for Team leads and Senior leaders');
  await expect(picker.locator('[data-company="NEON"]').getByTestId('company-status'))
    .toHaveText('Nobody found for Team leads and Senior leaders (try Board)');
  await expect(picker.locator('[data-company="Acme"]').getByTestId('company-status')).toHaveText('Searching…');
  const lanes = pipeline.getByTestId('company-lanes');
  await expect(lanes.locator('[data-lane="A24"]').getByTestId('lane-state')).toHaveText('3 can be emailed');
  await expect(lanes.locator('[data-lane="NEON"]').getByTestId('lane-state')).toHaveText('Nobody found');
  await expect(lanes.locator('[data-lane="Acme"]').getByTestId('lane-state')).toHaveText('Searching… 35%');
});
