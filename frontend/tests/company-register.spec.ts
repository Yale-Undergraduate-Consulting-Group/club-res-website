import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

type Kind = 'us_public' | 'us_private' | 'us_employer' | 'us_nonprofit' | 'uk' | 'club_targets';

// One entry per company, as GET /api/yucgoutreach/register returns it.
function company(over: Record<string, unknown> & { entity_key: string; id: number; display_name: string; kinds: Kind[] }) {
  return {
    legal_name: over.display_name, country: 'US', locations: [], record_count: 1, officer_count: 0, working_count: 0,
    company_domain: null, sector_label: null, last_event_at: null, hidden_reason: null, lookup_id: null, claimed_by: null,
    ...over,
  };
}

const GILGAMESH = company({ entity_key: 'US:gilgamesh pharma', id: 1, display_name: 'Gilgamesh Pharma', legal_name: 'Gilgamesh Pharma Inc.', kinds: ['us_private'], locations: ['New York'], officer_count: 6, company_domain: 'gilgameshpharma.com', sector_label: 'Pharmaceuticals' });
const LUCEM = company({ entity_key: 'US:lucem health', id: 2, display_name: 'Lucem Health', legal_name: 'Lucem Health, Inc.', kinds: ['us_private'], locations: ['North Carolina'], officer_count: 6, sector_label: 'Other Technology' });
const HANSFORD = company({ entity_key: 'GB:hansford sensors', id: 3, display_name: 'Hansford Sensors', legal_name: 'Hansford Sensors Limited', country: 'GB', kinds: ['uk'], locations: ['High Wycombe'], lookup_id: 3, sector_label: 'Manufacture of electronic industrial process control equipment' });
const WIKOFF = company({ entity_key: 'US:wikoff color', id: 4, display_name: 'Wikoff Color', legal_name: 'Wikoff Color Corporation', kinds: ['us_employer'], locations: ['SC'], sector_label: 'Manufacturing' });
const CHEEKWOOD = company({ entity_key: 'US:cheekwood botanical garden', id: 5, display_name: 'Cheekwood Botanical Garden', kinds: ['us_nonprofit'], locations: ['TN'], officer_count: 12, sector_label: 'Arts, Culture and Humanities' });
const SCOUTS = company({ entity_key: 'US:boy scouts of america', id: 6, display_name: 'Boy Scouts of America', kinds: ['us_nonprofit'], locations: ['CT', 'NY', 'OH', 'TX'], record_count: 4 });
// One company filed three ways: listed, an employer with a benefit plan, and a Reg D raise.
const APPLE = company({ entity_key: 'US:apple', id: 7, display_name: 'Apple', legal_name: 'Apple Inc.', kinds: ['us_public', 'us_private', 'us_employer'], locations: ['CA'], record_count: 3, officer_count: 2, sector_label: 'Electronic computers' });

const COMPANIES = [APPLE, GILGAMESH, LUCEM, HANSFORD, WIKOFF, CHEEKWOOD, SCOUTS];

const APPLE_LOG = {
  entity_key: 'US:apple', display_name: 'Apple', hidden_reason: null, total: 3,
  records: [
    { id: 71, source: 'sec_form_d', source_label: 'Reg D filing', tier: 'us_private', legal_name: 'Apple, Inc.', region: 'California', date: '2026-02-01', detail: ['Raised $15M'], url: 'https://www.sec.gov/Archives/edgar/data/999/000099926000001/', officer_count: 1 },
    { id: 72, source: 'dol_5500', source_label: 'Benefit plan filing', tier: 'us_employer', legal_name: 'APPLE INC', region: 'CA', date: '2025-01-01', detail: ['90,000 plan participants'], url: null, officer_count: 1 },
    { id: 73, source: 'sec_public', source_label: 'SEC listing', tier: 'us_public', legal_name: 'Apple Inc.', region: null, date: null, detail: [], url: 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=0000320193', officer_count: 0 },
  ],
};

const SCOUTS_LOG = {
  entity_key: 'US:boy scouts of america', display_name: 'Boy Scouts of America', hidden_reason: null, total: 4,
  records: ['CT', 'NY', 'OH', 'TX'].map((state, i) => ({
    id: 60 + i, source: 'irs_990', source_label: 'IRS nonprofit return', tier: 'us_nonprofit', legal_name: 'Boy Scouts Of America',
    region: state, date: `2024-0${i + 1}-01`, detail: ['$6.0M revenue'], url: null, officer_count: 0,
  })),
};

async function mockRegister(page: Page, opts: { many?: number } = {}) {
  const calls: string[] = [];
  const fetched: string[] = [];
  const logsRead: string[] = [];
  const created: Record<string, number[]>[] = [];
  const runsCreated: Record<string, unknown>[] = [];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/yucgoutreach/register/summary') body = {
      companies: 190979, with_people: 29492, pending: 0,
      tiers: [{ tier: 'us_public', country: 'US', n: 8031, with_officers: 0 }, { tier: 'us_private', country: 'US', n: 1563, with_officers: 1556 }, { tier: 'us_employer', country: 'US', n: 84822, with_officers: 11927 }, { tier: 'us_nonprofit', country: 'US', n: 26689, with_officers: 16009 }, { tier: 'uk', country: 'GB', n: 82819, with_officers: 0 }],
      sectors: [{ sector: 'Other Technology', n: 505 }, { sector: 'Biotechnology', n: 108 }],
      recent_ingests: [],
    };
    else if (path === '/api/yucgoutreach/register') {
      calls.push(url.search);
      const tier = url.searchParams.get('tier');
      const sector = url.searchParams.get('sector');
      const offset = Number(url.searchParams.get('offset') || 0);
      // A company is listed when any one of its kinds is the tier asked for.
      let items = opts.many
        ? Array.from({ length: opts.many }, (_, i) => company({ entity_key: `US:bulk ${i}`, id: 100 + i, display_name: `Bulk Company ${i}`, kinds: ['us_employer'], locations: ['TX'] }))
        : COMPANIES.filter(c => !tier || c.kinds.includes(tier as Kind));
      if (sector) items = items.filter(c => c.sector_label === sector);
      body = { items: items.slice(offset, offset + 40), total: items.length, limit: 40, offset };
    }
    else if (path === '/api/yucgoutreach/register/records') {
      const key = url.searchParams.get('key');
      logsRead.push(key || '');
      body = key === 'US:apple' ? APPLE_LOG : SCOUTS_LOG;
    }
    else if (path === '/api/yucgoutreach/register/entity-people') body = [
      { full_name: 'Ada Lovelace', relationship: 'Executive Officer', source_url: 'https://www.sec.gov/Archives/edgar/data/1/x/' },
      { full_name: 'Grace Hopper', relationship: 'Director' },
    ];
    else if (/\/register\/\d+\/people$/.test(path) && route.request().method() === 'POST') {
      fetched.push(path);
      body = { ok: true, attached: 2, officer_count: 2 };
    }
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
  return { calls, fetched, logsRead, created, runsCreated };
}

test('register lists companies by their public name and hands one to Find people', async ({ page }) => {
  const { calls, runsCreated } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();

  // The index opens on every company on record, and a tier button narrows it.
  await page.getByRole('button', { name: 'Recently funded' }).click();

  // The summary counts companies, not records, in plain words.
  await expect(page.getByText('190,979 companies · 8,031 US listed · 84,822 US employers · 26,689 nonprofits · 1,563 recently funded · 82,819 UK · 29,492 with named people')).toBeVisible();

  // Public name, kind, place and named people - nothing else in the row.
  const row = page.locator('[data-company="Gilgamesh Pharma"]');
  await expect(row.getByText('Gilgamesh Pharma', { exact: true })).toBeVisible();
  await expect(row).toContainText('Recently funded');
  await expect(row).toContainText('New York');
  await expect(row).toContainText('6 named people');
  await expect(row).not.toContainText('Inc.');
  await expect(row).not.toContainText('Pharmaceuticals');
  await expect(row).not.toContainText(/raised|\$|revenue|Form|employees/i);

  // Named people come from the company's own filings, on demand.
  await row.getByRole('button', { name: '6 named people' }).click();
  await expect(page.getByText('Ada Lovelace (Executive Officer), Grace Hopper (Director)')).toBeVisible();

  // Sector filter narrows the pool.
  await page.getByLabel('Sector', { exact: true }).selectOption('Biotechnology');
  await expect(page.getByText('0 matches')).toBeVisible();
  await page.getByLabel('Sector', { exact: true }).selectOption('Other Technology');
  await expect(page.getByText('Lucem Health', { exact: true })).toBeVisible();

  // UK shows Companies House companies with the country spelled out.
  await page.getByLabel('Sector', { exact: true }).selectOption('');
  await page.getByRole('button', { name: 'UK' }).click();
  const uk = page.locator('[data-company="Hansford Sensors"]');
  await expect(uk).toContainText('High Wycombe, UK');
  await expect(uk).not.toContainText('Limited');
  await expect(page.getByText(/group \(consolidated accounts\)|employees \(/)).toHaveCount(0);

  // Tier switch asks the API for listed companies.
  await page.getByRole('button', { name: 'US listed' }).click();
  await expect.poll(() => calls.some(search => search.includes('tier=us_public'))).toBe(true);

  // Handing a company to Find people opens the pipeline on it under its public
  // name, and the domain the register holds travels with it so the search is
  // not left to guess one.
  await page.getByRole('button', { name: 'Recently funded' }).click();
  await expect(page.getByText('Gilgamesh Pharma', { exact: true })).toBeVisible();
  await page.locator('[data-company="Gilgamesh Pharma"]').getByRole('button', { name: 'Find people here' }).click();
  await expect(page).toHaveURL(/view=company&company=Gilgamesh.*domain=gilgameshpharma\.com/);
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const lane = pipeline.locator('[data-lane="Gilgamesh Pharma"]');
  await expect(lane).toBeVisible();
  await expect(pipeline.getByTestId('recipient-picker').locator('[data-company="Gilgamesh Pharma"]')).toBeVisible();
  // The company came with its domain, so the step does not ask for one.
  await expect(pipeline.getByLabel('Company domain')).toHaveCount(0);
  // Its website came from the register's filings, so its search starts by
  // itself - no button to press, and exactly one search.
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0].company_name).toBe('Gilgamesh Pharma');
  expect(runsCreated[0].company_domain).toBe('gilgameshpharma.com');
  await page.screenshot({ path: 'test-results/company-register.png', fullPage: true });
});

test('a company filed several ways is one row with a badge per kind, and a log of its records', async ({ page }) => {
  const { logsRead } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();

  const apple = page.locator('[data-company="Apple"]');
  await expect(page.locator('[data-company="Apple"]')).toHaveCount(1);
  for (const badge of ['US listed', 'Recently funded', 'US employer']) await expect(apple).toContainText(badge);
  await expect(apple).not.toContainText('Nonprofit');
  // The list never shows an amount, a form name or a sector line.
  await expect(apple).not.toContainText(/\$|plan participants|Electronic computers|Inc\./);

  // The log lists one line per record: plain source, date, state, the name as
  // filed, the amount, and a link when the record has one.
  await apple.getByRole('button', { name: '3 records' }).click();
  const lines = apple.getByTestId('register-record');
  await expect(lines).toHaveCount(3);
  await expect(lines.nth(0)).toContainText('Reg D filing');
  await expect(lines.nth(0)).toContainText('2026-02-01');
  await expect(lines.nth(0)).toContainText('California');
  await expect(lines.nth(0)).toContainText('Apple, Inc.');
  await expect(lines.nth(0)).toContainText('Raised $15M');
  await expect(lines.nth(0).getByRole('link', { name: 'View' })).toHaveAttribute('href', 'https://www.sec.gov/Archives/edgar/data/999/000099926000001/');
  await expect(lines.nth(1)).toContainText('Benefit plan filing');
  await expect(lines.nth(1)).toContainText('APPLE INC');
  await expect(lines.nth(1)).toContainText('90,000 plan participants');
  await expect(lines.nth(1).getByRole('link')).toHaveCount(0);
  await expect(lines.nth(2)).toContainText('SEC listing');
  expect(logsRead).toEqual(['US:apple']);

  // Closing and reopening does not read it again.
  await apple.getByRole('button', { name: '3 records' }).click();
  await expect(lines).toHaveCount(0);
  await apple.getByRole('button', { name: '3 records' }).click();
  await expect(lines).toHaveCount(3);
  expect(logsRead).toEqual(['US:apple']);

  // A tier filter lists a company when any one of its records is that kind,
  // and the row still shows every kind it is.
  await page.getByRole('button', { name: 'US employers' }).click();
  await expect(page.locator('[data-company="Apple"]')).toContainText('US listed');
  await expect(page.locator('[data-company="Wikoff Color"]')).toBeVisible();
  await expect(page.locator('[data-company="Hansford Sensors"]')).toHaveCount(0);
});

test('chapters filed in many states fold into one company and the log lists each state', async ({ page }) => {
  await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();

  const scouts = page.locator('[data-company="Boy Scouts of America"]');
  await expect(page.locator('[data-company="Boy Scouts of America"]')).toHaveCount(1);
  await expect(scouts).toContainText('4 states');
  await scouts.getByRole('button', { name: '4 records' }).click();
  const lines = scouts.getByTestId('register-record');
  await expect(lines).toHaveCount(4);
  for (const state of ['CT', 'NY', 'OH', 'TX']) await expect(scouts.getByTestId('register-log')).toContainText(state);
  await expect(lines.first()).toContainText('IRS nonprofit return');
  await expect(lines.first()).toContainText('Boy Scouts Of America');
});

test('browsing opens on the companies worth reaching, with the newest filings one click away', async ({ page }) => {
  const { calls } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();

  // A member browsing without a name is looking for a company to reach out
  // to: the server ranks listed and large companies first by default.
  const order = page.getByTestId('register-sort');
  await expect(order.getByRole('button', { name: 'Worth reaching first' })).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => calls.some(search => search.includes('sort=value'))).toBe(true);

  // The "what is new" feed is still there.
  await order.getByRole('button', { name: 'Newest filings' }).click();
  await expect(order.getByRole('button', { name: 'Newest filings' })).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => calls.some(search => search.includes('sort=recent'))).toBe(true);

  // A typed name ranks by match, so the toggle steps aside.
  await page.getByLabel('Company or sector').fill('gilgamesh');
  await expect(order).toHaveCount(0);
});

test('a long list loads a page at a time', async ({ page }) => {
  const { calls } = await mockRegister(page, { many: 55 });
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();

  await expect(page.getByText('55 matches')).toBeVisible();
  await expect(page.getByRole('listitem').filter({ hasText: /Bulk Company/ })).toHaveCount(40);
  await page.getByRole('button', { name: 'Show more companies' }).click();
  await expect(page.getByRole('listitem').filter({ hasText: /Bulk Company/ })).toHaveCount(55);
  await expect(page.getByRole('button', { name: 'Show more companies' })).toHaveCount(0);
  expect(calls.some(search => search.includes('offset=40'))).toBe(true);
});

test('a listed or UK company with no officers on file can be looked up on demand', async ({ page }) => {
  const { fetched } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();
  await page.getByRole('button', { name: 'UK' }).click();

  // The bulk Companies House file carries no people, so the row arrives empty
  // and offers to read the per-company register instead of showing nothing.
  const row = page.locator('[data-company="Hansford Sensors"]');
  await expect(row.getByRole('button', { name: 'Look up officers' })).toBeVisible();
  await row.getByRole('button', { name: 'Look up officers' }).click();

  // It asks about the register row the company's listing holds, not the company name.
  await expect.poll(() => fetched).toEqual(['/api/yucgoutreach/register/3/people']);
  await expect(page.getByText('Ada Lovelace (Executive Officer), Grace Hopper (Director)')).toBeVisible();
  await expect(row).toContainText('2 named people');
  // Once they are on file the offer is gone: no second call against a
  // rate-limited public API.
  await expect(row.getByRole('button', { name: 'Look up officers' })).toHaveCount(0);
});

test('an employer row names no headcount and offers no officer lookup', async ({ page }) => {
  const { fetched } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();
  await page.getByRole('button', { name: 'US employers' }).click();

  const row = page.locator('[data-company="Wikoff Color"]');
  await expect(row).toContainText('US employer');
  await expect(row).toContainText('SC');
  await expect(row).not.toContainText(/employees|participants|Manufacturing|Corporation/);

  // Form 5500 has no per-company officer register to call, unlike SEC and
  // Companies House, so the row must not offer a lookup that cannot work.
  await expect(row.getByRole('button', { name: 'Look up officers' })).toHaveCount(0);
  expect(fetched).toEqual([]);
});

test('tier buttons use plain labels and hints', async ({ page }) => {
  await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();

  await page.getByRole('button', { name: 'Nonprofits', exact: true }).click();
  const row = page.locator('[data-company="Cheekwood Botanical Garden"]');
  await expect(row).toContainText('Nonprofit');
  await expect(row).toContainText('12 named people');
  // The buying signal that qualified it is not the list's business.
  await expect(row).not.toContainText(/\$|revenue|outside|advice|Form 990|employees/i);

  for (const label of ['All', 'Nonprofits', 'US employers', 'Recently funded', 'US listed', 'UK']) {
    const hint = await page.getByRole('button', { name: label, exact: true }).getAttribute('title');
    expect(hint, label).toBeTruthy();
    expect(hint, label).not.toMatch(/Form|\$|\dM|\bSEC\b|IRS|DOL|Reg D|thresholds?/);
  }
  await expect(page.getByRole('button', { name: /buy advice/i })).toHaveCount(0);
});

test('companies picked in the register go straight into the campaign pipeline', async ({ page }) => {
  const { created } = await mockRegister(page);
  await page.goto('/scraper');
  await page.getByRole('tab', { name: 'Companies' }).click();
  await page.getByRole('button', { name: 'Recently funded' }).click();
  await expect(page.getByText('Gilgamesh Pharma', { exact: true })).toBeVisible();

  // Nothing to act on until something is picked.
  await expect(page.getByRole('button', { name: 'Create target list' })).toHaveCount(0);

  await page.getByRole('checkbox', { name: 'Select Gilgamesh Pharma' }).check();
  await expect(page.getByText('1 company selected')).toBeVisible();

  // A list can be assembled across searches, so the pick survives a tier change.
  await page.getByRole('button', { name: 'UK' }).click();
  await expect(page.getByText('1 company selected')).toBeVisible();
  await page.getByRole('checkbox', { name: 'Select Hansford Sensors' }).check();
  await expect(page.getByText('2 companies selected')).toBeVisible();

  // Choosing companies starts the work rather than filing it. This used to
  // create a "target list" on another page - an object production had never
  // once used, its table empty - so the picks now go straight into the
  // campaign pipeline, under their public names.
  await page.getByRole('button', { name: 'Find people and write to them' }).click();
  await expect(page).toHaveURL(/\/scraper\?view=company&companies=/);
  await expect(page).toHaveURL(/Gilgamesh/);
  await expect(page).toHaveURL(/Hansford/);
  expect(created).toHaveLength(0);
});
