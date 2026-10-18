import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

const UK_ROW = { id: 3, source: 'companies_house', tier: 'uk', country: 'GB', company_name: 'Hansford Sensors Limited', sector_label: 'Manufacture of electronic industrial process control equipment', region: 'High Wycombe', employees: null, employees_source: 'companies_house_account_category', last_event_at: '2025-12-31', last_event_amount: null, last_event_kind: 'accounts_filed', officer_count: 0, metadata: { size_band: 'group (consolidated accounts)' } };

const EMPLOYER_ROW = { id: 4, source: 'dol_5500', tier: 'us_employer', country: 'US', company_name: 'Wikoff Color Corporation', sector_label: 'Manufacturing', region: 'SC', employees: 406, employees_source: 'form_5500_active_participants', last_event_at: '2025-01-01', last_event_amount: null, last_event_kind: 'benefit_plan_filed', officer_count: 0, metadata: { city: 'Chester' } };

const NONPROFIT_ROW = { id: 5, source: 'irs_990', tier: 'us_nonprofit', country: 'US', company_name: 'Cheekwood Botanical Garden', sector_label: 'Arts, Culture and Humanities', region: 'TN', employees: 287, employees_source: 'form_990_w3_employee_count', last_event_at: '2024-12-01', last_event_amount: null, last_event_kind: 'form_990_filed', officer_count: 12, metadata: { city: 'Nashville', revenue_range: '$18.4M revenue', buys_outside_advice: '$56k/yr on outside professional fees' } };

const ROWS = [
  { id: 1, source: 'sec_form_d', tier: 'us_private', country: 'US', company_name: 'Gilgamesh Pharma Inc.', company_domain: 'gilgameshpharma.com', sector_label: 'Pharmaceuticals', region: 'New York', employees: null, employees_source: null, last_event_at: '2026-03-27', last_event_amount: 15000000, last_event_kind: 'reg_d_offering', officer_count: 6, metadata: { revenue_range: 'No Revenues' } },
  { id: 2, source: 'sec_form_d', tier: 'us_private', country: 'US', company_name: 'Lucem Health, Inc.', sector_label: 'Other Technology', region: 'North Carolina', employees: null, employees_source: null, last_event_at: '2026-03-26', last_event_amount: 8397541, last_event_kind: 'reg_d_offering', officer_count: 6, metadata: {} },
];

async function mockRegister(page: Page) {
  const calls: string[] = [];
  const fetched: string[] = [];
  const created: Record<string, number[]>[] = [];
  const runsCreated: Record<string, unknown>[] = [];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/yucgoutreach/register/summary') body = {
      tiers: [{ tier: 'us_public', country: 'US', n: 8031, with_officers: 0 }, { tier: 'us_private', country: 'US', n: 1563, with_officers: 1556 }, { tier: 'us_employer', country: 'US', n: 84822, with_officers: 11927 }, { tier: 'us_nonprofit', country: 'US', n: 26689, with_officers: 16009 }, { tier: 'uk', country: 'GB', n: 82819, with_officers: 0 }],
      sectors: [{ sector: 'Other Technology', n: 505 }, { sector: 'Biotechnology', n: 108 }],
      recent_ingests: [],
    };
    else if (path === '/api/yucgoutreach/register') {
      calls.push(url.search);
      const tier = url.searchParams.get('tier');
      const sector = url.searchParams.get('sector');
      let items = [...ROWS, UK_ROW, EMPLOYER_ROW, NONPROFIT_ROW].filter(r => !tier || r.tier === tier);
      if (sector) items = items.filter(r => r.sector_label === sector);
      body = { items, total: items.length, limit: 40, offset: 0 };
    }
    else if (/\/register\/\d+\/people$/.test(path) && route.request().method() === 'POST') {
      fetched.push(path);
      body = { ok: true, attached: 2, officer_count: 2 };
    }
    else if (/\/register\/\d+\/people$/.test(path)) body = [
      { full_name: 'Ada Lovelace', relationship: 'Executive Officer', source_url: 'https://www.sec.gov/Archives/edgar/data/1/x/' },
      { full_name: 'Grace Hopper', relationship: 'Director' },
    ];
    else if (path === '/api/yucg/releases' && route.request().method() === 'POST') {
      created.push(route.request().postDataJSON());
      body = { id: 77, status: 'draft', targets: (route.request().postDataJSON().register_ids || []).length };
    }
    else if (path === '/api/yucgoutreach/runs' && route.request().method() === 'POST') {
      runsCreated.push(route.request().postDataJSON());
      body = { id: 9, status: 'queued' };
    }
    else if (path === '/api/yucgoutreach/runs') body = [];
    else if (path === '/api/yucgoutreach/role-suggestions') body = { company: url.searchParams.get('company'), roles: [], equivalents: [], sources: {} };
    else if (path === '/api/outreach/flows') body = [];
    else if (path === '/api/contacts') body = { items: [], total: 0, limit: 100, offset: 0 };
    else if (path === '/api/ai/models') body = { groups: [] };
    else if (path.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/companies\/summary$|\/sequences$|\/custom-formats$/.test(path)) body = [];
    await route.fulfill({ json: body });
  });
  return { calls, fetched, created, runsCreated };
}

test('register browses the free public pool and hands a company to Find people', async ({ page }) => {
  const { calls, runsCreated } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();

  // The index opens on every company on record - the register is the whole
  // pool - and a tier button narrows it.
  await page.getByRole('button', { name: 'Recently funded' }).click();

  // States what is on record across every source.
  await expect(page.getByText('8,031 listed · 84,822 US employers · 26,689 nonprofits that buy advice · 1,563 recently funded · 82,819 UK · 29,492 with named officers')).toBeVisible();
  await expect(page.getByText('Gilgamesh Pharma Inc.')).toBeVisible();
  await expect(page.getByText(/Pharmaceuticals · New York · raised \$15M · 2026-03-27/)).toBeVisible();

  // Officers come from the company's own filing, on demand.
  await page.getByRole('button', { name: /6 officer\(s\) on file/ }).first().click();
  await expect(page.getByText('Ada Lovelace (Executive Officer), Grace Hopper (Director)')).toBeVisible();

  // Sector filter narrows the pool.
  await page.getByLabel('Sector', { exact: true }).selectOption('Biotechnology');
  await expect(page.getByText('0 matches')).toBeVisible();
  await page.getByLabel('Sector', { exact: true }).selectOption('Other Technology');
  await expect(page.getByText('Lucem Health, Inc.')).toBeVisible();

  // UK tier shows Companies House rows with their statutory size band, and
  // never an invented employee count.
  await page.getByLabel('Sector', { exact: true }).selectOption('');
  await page.getByRole('button', { name: 'UK' }).click();
  await expect(page.getByText('Hansford Sensors Limited')).toBeVisible();
  await expect(page.getByText(/High Wycombe · group \(consolidated accounts\)/)).toBeVisible();
  await expect(page.getByText(/employees \(/)).toHaveCount(0);

  // Tier switch asks the API for listed companies.
  await page.getByRole('button', { name: 'US listed' }).click();
  await expect.poll(() => calls.some(search => search.includes('tier=us_public'))).toBe(true);

  // Handing a company to Find people opens the pipeline on it: the company
  // has its lane and its group in the sheet, and the domain the register
  // holds travels with it so the search is not left to guess one.
  await page.getByRole('button', { name: 'Recently funded' }).click();
  await page.getByLabel('Sector', { exact: true }).selectOption('');   // clear the sector narrowing
  await expect(page.getByText('Gilgamesh Pharma Inc.')).toBeVisible();
  await page.getByRole('button', { name: 'Find people here' }).first().click();
  await expect(page).toHaveURL(/view=company&company=Gilgamesh.*domain=gilgameshpharma\.com/);
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const lane = pipeline.locator('[data-lane="Gilgamesh Pharma Inc."]');
  await expect(lane).toBeVisible();
  await expect(pipeline.getByTestId('recipient-picker').locator('[data-company="Gilgamesh Pharma Inc."]')).toBeVisible();
  // The company came with its domain, so the step does not ask for one.
  await expect(pipeline.getByLabel('Company domain')).toHaveCount(0);
  // Its website came from the register's filings, so its search starts by
  // itself - no button to press, and exactly one search.
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0].company_name).toBe('Gilgamesh Pharma Inc.');
  expect(runsCreated[0].company_domain).toBe('gilgameshpharma.com');
  await page.screenshot({ path: 'test-results/company-register.png', fullPage: true });
});

test('a listed or UK company with no officers on file can be looked up on demand', async ({ page }) => {
  const { fetched } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();
  await page.getByRole('button', { name: 'UK' }).click();

  // The bulk Companies House file carries no people, so the row arrives empty
  // and offers to read the per-company register instead of showing nothing.
  const row = page.getByRole('listitem').filter({ hasText: 'Hansford Sensors Limited' });
  await expect(row.getByRole('button', { name: 'Look up officers' })).toBeVisible();
  await row.getByRole('button', { name: 'Look up officers' }).click();

  await expect.poll(() => fetched.length).toBe(1);
  await expect(page.getByText('Ada Lovelace (Executive Officer), Grace Hopper (Director)')).toBeVisible();
  // Once they are on file the offer is gone: no second call against a
  // rate-limited public API.
  await expect(row.getByRole('button', { name: 'Look up officers' })).toHaveCount(0);
});

test('the US employer tier shows the headcount the company filed, and offers no officer lookup', async ({ page }) => {
  const { fetched } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();
  await page.getByRole('button', { name: 'US employers' }).click();

  const row = page.getByRole('listitem').filter({ hasText: 'Wikoff Color Corporation' });
  await expect(row).toContainText('406 employees (form_5500_active_participants)');
  await expect(row).toContainText('Manufacturing');

  // Form 5500 has no per-company officer register to call, unlike SEC and
  // Companies House, so the row must not offer a lookup that cannot work.
  await expect(row.getByRole('button', { name: 'Look up officers' })).toHaveCount(0);
  expect(fetched).toEqual([]);
});

test('the nonprofit tier shows that the organisation already pays for outside advice', async ({ page }) => {
  await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();
  await page.getByRole('button', { name: 'Nonprofits that buy advice' }).click();

  const row = page.getByRole('listitem').filter({ hasText: 'Cheekwood Botanical Garden' });
  // Size alone does not mean a client. The row states the buying signal that
  // qualified it, taken from the organisation's own Form 990.
  await expect(row).toContainText('$56k/yr on outside professional fees');
  await expect(row).toContainText('$18.4M revenue');
  await expect(row).toContainText('287 employees (form_990_w3_employee_count)');
  // Officers came from Part VII of the same return, so they are already on file.
  await expect(row.getByRole('button', { name: /12 officer\(s\) on file/ })).toBeVisible();
});

test('companies picked in the register go straight into the campaign pipeline', async ({ page }) => {
  const { created } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();
  await page.getByRole('button', { name: 'Recently funded' }).click();
  await expect(page.getByText('Gilgamesh Pharma Inc.')).toBeVisible();

  // Nothing to act on until something is picked.
  await expect(page.getByRole('button', { name: 'Create target list' })).toHaveCount(0);

  await page.getByRole('checkbox', { name: 'Select Gilgamesh Pharma Inc.' }).check();
  await expect(page.getByText('1 company selected')).toBeVisible();

  // A list can be assembled across searches, so the pick survives a tier change.
  await page.getByRole('button', { name: 'UK' }).click();
  await expect(page.getByText('1 company selected')).toBeVisible();
  await page.getByRole('checkbox', { name: 'Select Hansford Sensors Limited' }).check();
  await expect(page.getByText('2 companies selected')).toBeVisible();

  // Choosing companies starts the work rather than filing it. This used to
  // create a "target list" on another page - an object production had never
  // once used, its table empty - so the picks now go straight into the
  // campaign pipeline.
  await page.getByRole('button', { name: 'Find people and write to them' }).click();
  await expect(page).toHaveURL(/\/scraper\?view=company&companies=/);
  await expect(page).toHaveURL(/Gilgamesh/);
  await expect(page).toHaveURL(/Hansford/);
  expect(created).toHaveLength(0);
});
