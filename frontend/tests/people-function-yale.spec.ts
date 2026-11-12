import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

const EVIDENCE = 'Education: Yale University';

// Alphabetical order would put Ada first; the person tied to Yale must lead.
const PEOPLE = [
  { id: 1, name: 'Ada Lovelace', email: 'ada@a24films.com', title: 'Finance Manager', company: 'A24', person_level: 'working', person_function: 'finance' },
  { id: 2, name: 'Grace Hopper', email: 'grace@a24films.com', title: 'Director of Finance', company: 'A24', person_level: 'working', person_function: 'finance', yale_signal: EVIDENCE },
  { id: 3, name: 'Owen Hart', email: 'owen@a24films.com', title: 'Operations Manager', company: 'A24', person_level: 'working', person_function: 'operations' },
  { id: 4, name: 'Sam Ortiz', email: 'sam@a24films.com', title: 'Senior Manager', company: 'A24', person_level: 'working', person_function: '' },
];

async function mock(page: Page) {
  const runsCreated: Record<string, unknown>[] = [];
  await page.route('**/api/**', async (route) => {
    const p = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let body: unknown = {};
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') body = { items: PEOPLE, total: PEOPLE.length, limit: 800, offset: 0 };
    else if (p === '/api/yucgoutreach/runs' && method === 'POST') {
      runsCreated.push(route.request().postDataJSON());
      body = { id: 9, status: 'queued' };
    } else if (p === '/api/yucgoutreach/runs') body = [];
    else if (p === '/api/yucgoutreach/runs/9') body = { id: 9, company_name: 'NEON', status: 'running', progress_pct: 40, progress_message: 'Searching…' };
    else if (p === '/api/yucgoutreach/runs/9/prospects') body = [];
    else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/outreach/flows') body = [];
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p === '/api/yucgoutreach/role-suggestions') body = { company: 'A24', roles: [], equivalents: [], sources: {} };
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    await route.fulfill({ json: body });
  });
  return { runsCreated };
}

test('a person tied to Yale leads the sheet and says why on hover', async ({ page }) => {
  await mock(page);
  await page.goto('/scraper?view=company&companies=A24');
  const picker = page.locator('[data-section="campaign-pipeline"]').getByTestId('recipient-picker');
  await expect(picker.getByText('Grace Hopper')).toBeVisible();

  const rows = picker.locator('li');
  await expect(rows.first()).toContainText('Grace Hopper');
  const badge = picker.getByTestId('yale-badge');
  await expect(badge).toHaveCount(1);
  await expect(rows.first().getByTestId('yale-badge')).toHaveAttribute('title', new RegExp(EVIDENCE));
  await expect(rows.first().getByTestId('person-function')).toHaveText('Finance');
  // Nobody else is marked, and no one is marked without evidence.
  await expect(rows.nth(1).getByTestId('yale-badge')).toHaveCount(0);
});

test('the function filter narrows who is shown and who select-all reaches', async ({ page }) => {
  await mock(page);
  await page.goto('/scraper?view=company&companies=A24');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const picker = pipeline.getByTestId('recipient-picker');
  await expect(picker.getByText('Owen Hart')).toBeVisible();

  await picker.getByTestId('function-filter').selectOption('finance');
  await expect(picker.getByText('Ada Lovelace')).toBeVisible();
  await expect(picker.getByText('Grace Hopper')).toBeVisible();
  await expect(picker.getByText('Owen Hart')).toHaveCount(0);
  // A title that names no function is not a finance match.
  await expect(picker.getByText('Sam Ortiz')).toHaveCount(0);

  // "Select all shown" means the filter, so the send cannot widen by accident.
  await pipeline.getByRole('button', { name: /^Clear$/ }).click();
  await pipeline.getByRole('button', { name: /^Select all shown/ }).click();
  await expect(pipeline.getByTestId('selected-count')).toHaveText('2 of 4 selected');

  await picker.getByTestId('function-filter').selectOption('legal');
  await expect(picker.getByText('Nobody matches your filters. Widen them above.')).toBeVisible();

  await picker.getByTestId('function-filter').selectOption('all');
  await expect(picker.getByText('Owen Hart')).toBeVisible();
});

test('a search asks for the function chosen, and for none when none is chosen', async ({ page }) => {
  const { runsCreated } = await mock(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const neonLane = pipeline.locator('[data-lane="NEON"]');
  await expect(pipeline.getByText('Grace Hopper')).toBeVisible();

  await rail.getByLabel('Function to look for').selectOption('finance');
  await neonLane.hover();
  await neonLane.getByRole('button', { name: 'Find people' }).click();
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0].company_name).toBe('NEON');
  expect(runsCreated[0].function).toBe('finance');
  expect(runsCreated[0].level).toBe('working');
});

test('no function is sent unless one was chosen', async ({ page }) => {
  const { runsCreated } = await mock(page);
  await page.goto('/scraper?view=company&companies=A24,NEON');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const neonLane = pipeline.locator('[data-lane="NEON"]');
  await expect(pipeline.getByText('Grace Hopper')).toBeVisible();

  await neonLane.hover();
  await neonLane.getByRole('button', { name: 'Find people' }).click();
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0]).not.toHaveProperty('function');
});
