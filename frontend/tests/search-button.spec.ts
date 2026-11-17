import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

/**
 * Adding companies searches nothing: the member adds them, says who to look
 * for and how many, then presses Search. A list of companies can be imported
 * from a spreadsheet instead of typed.
 */
async function mock(page: Page, opts: { sheetRows?: string[][] } = {}) {
  const runsCreated: Record<string, unknown>[] = [];
  const resolved: string[] = [];
  let sheetUploads = 0;
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let status = 200;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/yucgoutreach/resolve-company') {
      const q = new URL(request.url()).searchParams.get('q') || '';
      resolved.push(q);
      body = { name: q, domain: null, domain_verified: false, linkedin_url: null, source: 'typed', alternatives: [] };
    } else if (path === '/api/yucgoutreach/company-sheet') {
      sheetUploads += 1;
      body = { rows: opts.sheetRows ?? [] };
    } else if (path === '/api/yucgoutreach/runs' && request.method() === 'POST') {
      // The server runs three at a time for a member.
      if (runsCreated.length >= 3) {
        status = 409;
        body = { detail: 'Already running' };
      } else {
        runsCreated.push(request.postDataJSON());
        body = { id: 100 + runsCreated.length, status: 'queued' };
      }
    } else if (path === '/api/yucgoutreach/runs') {
      body = runsCreated.map((r, i) => ({
        id: 101 + i, company_name: r.company_name, status: 'running', progress_pct: 10, progress_message: 'Searching…',
      }));
    } else if (/\/api\/yucgoutreach\/runs\/\d+$/.test(path)) {
      const id = Number(path.split('/').pop());
      body = { id, company_name: runsCreated[id - 101]?.company_name, status: 'running', progress_pct: 10, progress_message: 'Searching…' };
    } else if (path === '/api/contacts') body = { items: [], total: 0, limit: 2000, offset: 0 };
    else if (/\/companies\/summary$|\/sequences$|\/custom-formats$|\/rosters$/.test(path)) body = [];
    else if (path.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    await route.fulfill({ status, json: body });
  });
  return { runsCreated, resolved, sheetUploads: () => sheetUploads };
}

const csv = (name: string, text: string) => ({ name, mimeType: 'text/csv', buffer: Buffer.from(text) });

test('adding companies searches nothing until Search is pressed, then every added company is searched, three at a time', async ({ page }) => {
  const { runsCreated } = await mock(page);
  await page.goto('/scraper?view=company');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const search = rail.getByTestId('search-all');

  // Nothing to search yet.
  await expect(search).toBeDisabled();

  for (const name of ['Acme', 'Blue Dot', 'Cedar', 'Delta']) {
    await rail.getByLabel('Company').fill(name);
    await rail.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(rail.locator(`[data-company-chip="${name}"]`)).toBeVisible();
  }
  await expect(rail.getByTestId('chosen-count')).toContainText('4 companies added');
  await expect(search).toHaveText('Search 4 companies');
  await expect(search).toBeEnabled();
  // Search sits in the same row as the number of people.
  const goal = await rail.getByLabel('How many people').boundingBox();
  const button = await search.boundingBox();
  expect(Math.abs((goal?.y ?? 0) + (goal?.height ?? 0) / 2 - ((button?.y ?? 0) + (button?.height ?? 0) / 2))).toBeLessThan(8);
  // Adding searched nothing, however long we wait for it to.
  await page.waitForTimeout(1000);
  expect(runsCreated).toHaveLength(0);
  await expect(pipeline.locator('[data-lane="Acme"]')).toHaveAttribute('data-state', 'idle');

  await search.click();
  // Three run together; the fourth waits for room and is not shown as an error.
  await expect.poll(() => runsCreated.map((r) => r.company_name)).toEqual(['Acme', 'Blue Dot', 'Cedar']);
  await expect(pipeline.locator('[data-lane="Delta"]').getByTestId('lane-state')).toHaveText('Queued');
  await expect(pipeline.getByRole('alert')).toHaveCount(0);
  // Every one has been asked for; a second press has nothing left to search.
  await expect(search).toBeDisabled();

  // A company added afterwards is the only one the next Search counts.
  await rail.getByLabel('Company').fill('Echo');
  await rail.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(search).toHaveText('Search 1 company');
});

test('Search does not ask twice for a company whose search is still running', async ({ page }) => {
  const { runsCreated } = await mock(page);
  await page.goto('/scraper?view=company&companies=Acme');
  const rail = page.locator('[data-section="campaign-pipeline"] [data-rail]');
  const search = rail.getByTestId('search-all');

  await expect(search).toHaveText('Search 1 company');
  await search.click();
  await expect.poll(() => runsCreated.length).toBe(1);
  await expect(search).toBeDisabled();

  await rail.getByLabel('How many people').fill('40');
  await expect(search).toHaveText('Search');
  // The run is still going, so it is not asked for twice.
  await expect(search).toBeDisabled();
});

test('a spreadsheet of companies adds them, with or without a heading, and searches none until Search', async ({ page }) => {
  const { runsCreated, resolved } = await mock(page);
  await page.goto('/scraper?view=company');
  const rail = page.locator('[data-section="campaign-pipeline"] [data-rail]');
  const file = rail.getByLabel('List of companies');

  // A Company and a Website column: the website goes with the company.
  await file.setInputFiles(csv('targets.csv', 'Company,Website\nGlobex,https://www.globex.com/about\n"Initech, Inc.",\nGlobex,globex.com\n'));
  await expect(rail.getByTestId('chosen-count')).toContainText('2 companies added');
  await expect(rail.locator('[data-company-chip="Globex"]')).toContainText('globex.com');
  await expect(rail.locator('[data-company-chip="Initech, Inc."]')).toBeVisible();

  // No heading: the first column, first row included.
  await file.setInputFiles(csv('more.csv', 'Hooli\nPied Piper\n'));
  await expect(rail.getByTestId('chosen-count')).toContainText('4 companies added');
  await expect(rail.locator('[data-company-chip="Hooli"]')).toBeVisible();
  await expect(rail.locator('[data-company-chip="Pied Piper"]')).toBeVisible();
  expect([...resolved].sort()).toEqual(['Globex', 'Hooli', 'Initech, Inc.', 'Pied Piper']);

  expect(runsCreated).toHaveLength(0);
  const search = rail.getByTestId('search-all');
  await expect(search).toHaveText('Search 4 companies');
  await search.click();
  await expect.poll(() => runsCreated.map((r) => r.company_name)).toEqual(['Globex', 'Initech, Inc.', 'Hooli']);
  expect(runsCreated[0]).toMatchObject({ company_domain: 'globex.com' });
});

test('an Excel list of companies is read by the server and added the same way', async ({ page }) => {
  const server = await mock(page, { sheetRows: [['Name', 'Domain'], ['Globex', 'globex.com'], ['Soylent', '']] });
  await page.goto('/scraper?view=company');
  const rail = page.locator('[data-section="campaign-pipeline"] [data-rail]');

  await rail.getByLabel('List of companies').setInputFiles({
    name: 'targets.xlsx',
    mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    buffer: Buffer.from('PK'),
  });
  await expect(rail.getByTestId('chosen-count')).toContainText('2 companies added');
  expect(server.sheetUploads()).toBe(1);
  await expect(rail.locator('[data-company-chip="Globex"]')).toContainText('globex.com');
});

test('a file with no companies in it says so, and a file of another kind is refused', async ({ page }) => {
  await mock(page);
  await page.goto('/scraper?view=company');
  const rail = page.locator('[data-section="campaign-pipeline"] [data-rail]');
  const file = rail.getByLabel('List of companies');

  await file.setInputFiles(csv('empty.csv', 'Company,Website\n'));
  await expect(rail.getByText(/No companies found/)).toBeVisible();
  await file.setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('Globex') });
  await expect(rail.getByText('Upload a CSV or Excel (.xlsx) file.')).toBeVisible();
  await expect(rail.getByTestId('chosen-count')).toHaveCount(0);
});

test('the sentences that only restate a control are gone from the left panel and the people list', async ({ page }) => {
  await mock(page);
  await page.goto('/scraper?view=company&companies=Acme,Blue%20Dot,Cedar');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  await expect(rail.locator('[data-company-chip="Cedar"]')).toBeVisible();

  for (const gone of [
    /A search keeps going until it has this many/,
    /searched as soon as it is added/,
    /Searching for:/,
    /Or import a spreadsheet/,
    /^3 added$/,
  ]) {
    await expect(pipeline.getByText(gone)).toHaveCount(0);
  }
  // What still carries information, and the controls themselves, stay.
  await expect(rail.getByRole('button', { name: 'Other titles…' })).toBeVisible();
  await expect(rail.getByLabel('How many people')).toBeVisible();
  await expect(rail.getByRole('button', { name: 'Or import a list of companies' })).toBeVisible();
  await expect(rail.getByTestId('chosen-count')).toContainText('3 companies added');
  await expect(pipeline.getByTestId('recipient-picker').locator('[data-company="Acme"]')).toBeVisible();
});
