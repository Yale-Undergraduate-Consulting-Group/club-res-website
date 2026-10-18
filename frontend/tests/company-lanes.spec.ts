import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

const PEOPLE = [
  { id: 1, name: 'Jean Bartik', email: 'jean.bartik@a24films.com', title: 'Director of Operations', company: 'A24', person_level: 'working' },
  { id: 2, name: 'Klara Dan', email: 'klara.dan@a24films.com', title: 'Head of Partnerships', company: 'A24', person_level: 'working' },
];

async function mockLanes(page: Page) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    let body: unknown = {};
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') body = { items: PEOPLE, total: PEOPLE.length, limit: 800, offset: 0 };
    else if (p === '/api/campaigns/build') body = { recipients: 2, ready: 2, held: [], sample: { email: 'jean.bartik@a24films.com', subject: 'A24', body: 'Hi Jean.' } };
    else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/yucgoutreach/runs') body = [];
    else if (p === '/api/yucgoutreach/role-suggestions') body = { company: url.searchParams.get('company'), roles: [], equivalents: [], sources: {} };
    else if (p === '/api/outreach/flows') body = [];
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    await route.fulfill({ json: body });
  });
}

test('the lanes and the sheet stay on screen together at every step', async ({ page }) => {
  await mockLanes(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const lanes = pipeline.getByTestId('company-lanes');
  const picker = pipeline.getByTestId('recipient-picker');

  // Step 2: the people are being chosen, and the flowchart of where each
  // company stands sits above them rather than on another step.
  await expect(picker).toBeVisible();
  await expect(picker).toHaveAttribute('data-mode', 'select');
  await expect(lanes).toBeVisible();
  await expect(lanes.getByText('2 companies · 2 people ticked')).toBeVisible();
  await expect(lanes.locator('[data-lane="A24"]').getByTestId('lane-state')).toHaveText('2 found · 2 ticked');
  // The company nobody has been found at says so, rather than looking done.
  await expect(lanes.locator('[data-lane="NEON"]').getByTestId('lane-state')).toHaveText('nobody yet');

  // Step 1: same lanes, and the sheet previews what the companies come with.
  await rail.getByRole('button', { name: /Add companies/ }).click();
  await expect(lanes).toBeVisible();
  await expect(picker).toHaveAttribute('data-mode', 'preview');
  await expect(picker.getByRole('checkbox', { name: 'Write to Jean Bartik' })).toBeDisabled();

  // Step 3: lanes, then the recipients - nothing else - and the hand-off.
  await rail.getByRole('button', { name: 'Next: tick who gets it' }).click();
  await rail.getByRole('button', { name: /^Write to these 2/ }).click();
  await expect(lanes).toBeVisible();
  await expect(rail.getByRole('button', { name: /^Write to these 2 in Drafts/ })).toBeVisible();
  await expect(picker).toHaveAttribute('data-mode', 'review');
  await expect(picker.getByRole('checkbox')).toHaveCount(0);
});

test('the state column lines up regardless of how long each row\'s text is', async ({ page }, testInfo) => {
  // "nobody yet" and "2 found · 2 ticked" are very different lengths. A
  // natural-width column shifts with every row's own text, so what looked
  // like a flowchart read as a ragged list instead. Fixed-width columns are
  // a desktop layout: a phone does not have 450px of row to spare, so below
  // `sm` each lane wraps to two lines instead and alignment does not apply.
  test.skip(testInfo.project.name !== 'desktop', 'fixed-width columns only exist at sm and above');
  await mockLanes(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const lanes = page.locator('[data-section="campaign-pipeline"]').getByTestId('company-lanes');
  await expect(lanes.locator('[data-lane="A24"]').getByTestId('lane-state')).toHaveText('2 found · 2 ticked');
  await expect(lanes.locator('[data-lane="NEON"]').getByTestId('lane-state')).toHaveText('nobody yet');

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
  await expect(lanes.getByText('20 companies · 0 people ticked')).toBeVisible();
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

test('passing the pointer over a lane does not focus it, and each lane shows its search', async ({ page }, testInfo) => {
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

  // The search is visible without hovering, and names what it does.
  await page.mouse.move(0, 0);
  await expect(a24.getByRole('button', { name: '+ Find more people' })).toBeVisible();
  await expect(neon.getByRole('button', { name: 'Find people' })).toBeVisible();
});

test('a company in the sheet folds to its header and opens again', async ({ page }) => {
  await mockLanes(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');

  const picker = page.locator('[data-section="campaign-pipeline"]').getByTestId('recipient-picker');
  const group = picker.locator('[data-company="A24"]');
  await expect(group.getByText('Jean Bartik')).toBeVisible();

  await group.getByRole('button', { name: 'Hide people at A24' }).click();
  await expect(group.getByText('Jean Bartik')).toBeHidden();
  // The header keeps the count, the tick-all box and the search.
  await expect(group.getByText('2 on file · 2 selected')).toBeVisible();
  await expect(group.getByRole('checkbox', { name: 'Select everyone at A24' })).toBeChecked();
  await expect(group.getByRole('button', { name: '+ Find more people' })).toBeVisible();

  await group.getByRole('button', { name: 'Show people at A24' }).click();
  await expect(group.getByText('Jean Bartik')).toBeVisible();
});
