import { expect, test, type Locator, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

type Resolved = {
  name: string;
  domain: string | null;
  domain_verified: boolean;
  linkedin_url: string | null;
  source: 'linkedin' | 'register' | 'club' | 'typed';
  alternatives: { name: string; domain: string | null }[];
};

/** What the resolver answers for each thing a member can type or paste. */
function resolveFor(q: string): Resolved {
  if (/linkedin\.com\/company\/a24/i.test(q)) {
    return { name: 'A24', domain: 'a24films.com', domain_verified: true, linkedin_url: 'https://www.linkedin.com/company/a24', source: 'linkedin', alternatives: [] };
  }
  if (q === 'HBO') {
    return { name: 'HBO', domain: 'hbo.com', domain_verified: true, linkedin_url: null, source: 'register', alternatives: [] };
  }
  if (q === 'Bartik Foundation') {
    return { name: 'Bartik Foundation', domain: 'bartik.org', domain_verified: true, linkedin_url: null, source: 'register', alternatives: [] };
  }
  // Nothing could be checked: the name as typed, with what else it might mean.
  return {
    name: q, domain: null, domain_verified: false, linkedin_url: null, source: 'typed',
    alternatives: q === 'Bartik Family Foundation' ? [{ name: 'Bartik Foundation', domain: 'bartik.org' }] : [],
  };
}

async function mockInput(page: Page, opts: { limited?: boolean } = {}) {
  const resolved: string[] = [];
  const runsCreated: Record<string, unknown>[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const p = url.pathname;
    let body: unknown = {};
    let status = 200;
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/yucgoutreach/resolve-company') {
      const q = url.searchParams.get('q') || '';
      resolved.push(q);
      body = resolveFor(q);
    } else if (p === '/api/yucgoutreach/runs' && request.method() === 'POST') {
      runsCreated.push(request.postDataJSON());
      if (opts.limited) {
        status = 429;
        body = { detail: 'Hourly search limit reached' };
      } else {
        body = { id: 9, status: 'queued' };
      }
    } else if (p === '/api/yucgoutreach/runs') body = [];
    else if (p === '/api/yucgoutreach/runs/9') body = { id: 9, company_name: 'A24', status: 'running', progress_pct: 10, progress_message: 'Searching…' };
    else if (p === '/api/yucgoutreach/register') {
      // The top register row for "HBO" is a subsidiary nobody asked for.
      body = (url.searchParams.get('q') || '').toLowerCase() === 'hbo'
        ? { items: [{ company_name: 'HBO Production UK Ltd', company_domain: null, sector_label: 'Film production' }], total: 1, limit: 8, offset: 0 }
        : { items: [], total: 0, limit: 8, offset: 0 };
    }
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/role-suggestions') body = { company: url.searchParams.get('company'), roles: [], equivalents: [], sources: {} };
    // Nobody on file anywhere, so a sure company has every reason to search.
    else if (p === '/api/contacts') body = { items: [], total: 0, limit: 800, offset: 0 };
    else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/outreach/flows') body = [];
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    await route.fulfill({ status, json: body });
  });
  return { resolved, runsCreated };
}

/** A real paste event, as the browser fires it when the member pastes. */
async function paste(input: Locator, text: string) {
  await input.evaluate((el, t) => {
    const data = new DataTransfer();
    data.setData('text/plain', t);
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  }, text);
}

function railOf(page: Page) {
  return page.locator('[data-section="campaign-pipeline"] [data-rail]');
}

test('a pasted LinkedIn page adds the company with its checked website, and searches it at once', async ({ page }) => {
  const { resolved, runsCreated } = await mockInput(page);
  await page.goto('/scraper?view=company');
  const rail = railOf(page);

  await paste(rail.getByLabel('Company'), 'https://www.linkedin.com/company/a24/');
  const chip = rail.locator('[data-company-chip="A24"]');
  await expect(chip).toContainText('a24films.com ✓');
  await expect(chip.getByRole('button', { name: 'Not this?', exact: true })).toBeVisible();
  expect(resolved).toEqual(['https://www.linkedin.com/company/a24/']);
  // The paste went to the resolver, not into the box.
  await expect(rail.getByLabel('Company')).toHaveValue('');

  // A LinkedIn page is sure, and nobody is on file: the search is the reason
  // it was added, so it starts without a button.
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0].company_name).toBe('A24');
  expect(runsCreated[0].company_domain).toBe('a24films.com');
});

test('Enter sends the typed name to the resolver and does not take the top suggestion', async ({ page }) => {
  const { resolved } = await mockInput(page);
  await page.goto('/scraper?view=company');
  const rail = railOf(page);

  const field = rail.getByLabel('Company');
  await field.fill('HBO');
  // The dropdown offers the register's row, but nobody pointed at it.
  await expect(page.getByRole('option', { name: /HBO Production UK Ltd/ })).toBeVisible();
  await field.press('Enter');

  await expect(rail.locator('[data-company-chip="HBO"]')).toContainText('hbo.com ✓');
  await expect(rail.locator('[data-company-chip="HBO Production UK Ltd"]')).toHaveCount(0);
  expect(resolved).toEqual(['HBO']);
  await expect(rail.getByTestId('chosen-count')).toContainText('1 company added');
});

test('an unsure company is marked a best guess, is not searched by itself, and can be corrected', async ({ page }) => {
  const { runsCreated } = await mockInput(page);
  await page.goto('/scraper?view=company');
  const rail = railOf(page);
  const pipeline = page.locator('[data-section="campaign-pipeline"]');

  await rail.getByLabel('Company').fill('Bartik Family Foundation');
  await rail.getByRole('button', { name: 'Add', exact: true }).click();

  const chip = rail.locator('[data-company-chip="Bartik Family Foundation"]');
  await expect(chip.getByRole('button', { name: 'Best guess · Not this?' })).toBeVisible();
  // Its lane waits for the member instead of spending a search on a guess.
  const lane = pipeline.locator('[data-lane="Bartik Family Foundation"]');
  await expect(lane.getByTestId('lane-state')).toHaveText('nobody yet');
  await page.waitForTimeout(1000);
  expect(runsCreated).toHaveLength(0);
  await expect(lane).toHaveAttribute('data-state', 'idle');

  // "Not this?" offers what the words could have meant; choosing one
  // replaces the guess rather than adding beside it.
  await chip.getByRole('button', { name: 'Best guess · Not this?' }).click();
  await chip.getByRole('button', { name: /^Bartik Foundation/ }).click();
  await expect(rail.locator('[data-company-chip="Bartik Foundation"]')).toContainText('bartik.org ✓');
  await expect(chip).toHaveCount(0);
  await expect(rail.getByTestId('chosen-count')).toContainText('1 company added');
  // The replacement is sure, so it is searched.
  await expect.poll(() => runsCreated.map((r) => r.company_name)).toEqual(['Bartik Foundation']);
});

test('pasting two lines adds two companies', async ({ page }) => {
  const { resolved } = await mockInput(page);
  await page.goto('/scraper?view=company');
  const rail = railOf(page);

  await paste(rail.getByLabel('Company'), 'A24\nNEON\n');
  await expect(rail.locator('[data-company-chip="A24"]')).toBeVisible();
  await expect(rail.locator('[data-company-chip="NEON"]')).toBeVisible();
  await expect(rail.getByTestId('chosen-count')).toContainText('2 companies added');
  expect([...resolved].sort()).toEqual(['A24', 'NEON']);
});

test('× removes a company, and clicking its name does not', async ({ page }) => {
  await mockInput(page);
  await page.goto('/scraper?view=company');
  const rail = railOf(page);
  const pipeline = page.locator('[data-section="campaign-pipeline"]');

  await paste(rail.getByLabel('Company'), 'A24\nNEON');
  const a24 = rail.locator('[data-company-chip="A24"]');
  await expect(rail.getByTestId('chosen-count')).toContainText('2 companies added');

  await a24.getByText('A24', { exact: true }).click();
  await expect(a24).toBeVisible();

  await rail.getByRole('button', { name: 'Remove A24' }).click();
  await expect(a24).toHaveCount(0);
  await expect(rail.getByTestId('chosen-count')).toContainText('1 company added');
  await expect(pipeline.locator('[data-lane="A24"]')).toHaveCount(0);
  await expect(pipeline.locator('[data-lane="NEON"]')).toBeVisible();
});

test('who to look for sets the titles the search asks for', async ({ page }) => {
  const { runsCreated } = await mockInput(page);
  await page.goto('/scraper?view=company&companies=NEON');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = railOf(page);

  // Team leads by default; nobody has to type a title.
  await expect(rail.getByRole('radio', { name: 'Team leads' })).toHaveAttribute('aria-checked', 'true');
  await expect(rail.getByText('Searching for: Director of, Head of, Manager, Lead')).toBeVisible();

  await rail.getByRole('radio', { name: 'Board' }).click();
  await expect(rail.getByRole('radio', { name: 'Board' })).toHaveAttribute('aria-checked', 'true');
  await expect(rail.getByRole('radio', { name: 'Team leads' })).toHaveAttribute('aria-checked', 'false');
  await expect(rail.getByText('Searching for: Board member, Trustee, Director')).toBeVisible();

  await pipeline.locator('[data-lane="NEON"]').getByRole('button', { name: 'Find people' }).click();
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0].company_name).toBe('NEON');
  expect(runsCreated[0].title_hints).toBe('Board member, Trustee, Director');
});

test('a search refused by the hourly limit pauses with a notice, not an error', async ({ page }) => {
  const { runsCreated } = await mockInput(page, { limited: true });
  await page.goto('/scraper?view=company&companies=NEON');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const lane = pipeline.locator('[data-lane="NEON"]');

  await lane.getByRole('button', { name: 'Find people' }).click();
  await expect.poll(() => runsCreated.length).toBe(1);
  await expect(pipeline.getByRole('status').filter({ hasText: 'Searches are paused for a few minutes' })).toBeVisible();
  await expect(pipeline.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText('Hourly search limit reached')).toHaveCount(0);
  // It keeps its place and starts by itself later.
  await expect(lane).toHaveAttribute('data-state', 'queued');
});
