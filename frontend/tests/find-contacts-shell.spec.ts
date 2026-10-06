import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

const REGISTER_HIT = {
  id: 9,
  source: 'companies_house',
  tier: 'uk',
  country: 'GB',
  company_name: 'Hansford Sensors Limited',
  company_domain: 'hansfordsensors.com',
  sector_label: 'Manufacture of electronic industrial process control equipment',
  region: 'High Wycombe',
  employees: null,
  employees_source: null,
  last_event_at: '2025-12-31',
  last_event_amount: null,
  last_event_kind: 'accounts_filed',
  officer_count: 3,
  metadata: {},
};

async function mockPage(page: Page) {
  const registerQueries: string[] = [];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/yucgoutreach/register') {
      registerQueries.push(url.searchParams.get('q') || '');
      body = { items: [REGISTER_HIT], total: 1, limit: 8, offset: 0 };
    } else if (path === '/api/contacts/companies/summary') body = [{ company: 'Acme Corp', company_domain: 'acme.com', contact_count: 4 }];
    else if (path === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (path === '/api/yucgoutreach/resolve-company') {
      const q = url.searchParams.get('q') || '';
      body = q === REGISTER_HIT.company_name
        ? { name: q, domain: REGISTER_HIT.company_domain, domain_verified: false, linkedin_url: null, source: 'register', alternatives: [] }
        : { name: q, domain: null, domain_verified: false, linkedin_url: null, source: 'typed', alternatives: [] };
    }
    else if (path === '/api/yucgoutreach/runs') body = [];
    else if (path === '/api/outreach/flows') body = [];
    else if (path === '/api/contacts') body = { items: [], total: 0, limit: 100, offset: 0 };
    else if (path === '/api/ai/models') body = { groups: [] };
    else if (path.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/sequences$|\/custom-formats$/.test(path)) body = [];
    await route.fulfill({ json: body });
  });
  return { registerQueries };
}

test('every Find contacts surface is a pill, and the company field reaches the public register', async ({ page }) => {
  const { registerQueries } = await mockPage(page);

  // Find contacts opens on the company index, because choosing a company is
  // the step before looking for people at it.
  await page.goto('/scraper');
  await expect(page.getByRole('tab', { name: 'Companies' })).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('tab', { name: 'Find people' }).click();

  // Every surface is a pill in one strip. Previously two were tabs, one was a
  // fold at the bottom of the page and two were sentence links above it, so
  // the ways into this page did not look like each other or like a menu.
  // Three doors. Bulk research is not one of them - queuing research is a way
  // people arrive at a list, so it lives inside the step that gathers people -
  // but uploading a file is its own act, on its own tab.
  await expect(page.getByRole('tab')).toHaveCount(3);
  for (const name of ['Companies', 'Find people', 'Import a file']) {
    await expect(page.getByRole('tab', { name })).toBeVisible();
  }
  await expect(page.getByRole('tab', { name: 'Bulk research' })).toHaveCount(0);
  await expect(page.getByText('Have a spreadsheet already?')).toHaveCount(0);

  // Company email formats are no longer a browsable list of templates and
  // percentages: a format is only useful at the moment an address is missing,
  // so it appears on the contact row that lacks one, and only once confirmed.
  await expect(page.getByText('Company email formats')).toHaveCount(0);
  // Find people is the campaign itself: a rail of numbered steps, with the
  // work of the current step in the wide column beside it.
  await expect(page.getByRole('button', { name: /Add companies/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Tick who gets it/ })).toBeVisible();

  // Looking up a single named person is gone: it was a company search with
  // one name in it, which Find people already does, and the address guess it
  // offered now appears on the contact row that lacks one.
  await expect(page.getByText('Look up one named person')).toHaveCount(0);

  // Typing a company reaches the 100k-row register, which cannot be held in
  // the browser, and the row is labelled with where it came from.
  await page.getByLabel('Company', { exact: true }).fill('hansford');
  await expect.poll(() => registerQueries.includes('hansford')).toBe(true);
  const option = page.getByRole('option', { name: /Hansford Sensors Limited/ });
  await expect(option).toBeVisible();
  await expect(option).toContainText('hansfordsensors.com');
  await option.click();
  // Picking from the register adds it to the chosen companies rather than
  // only filling a box: choosing is the point of the step.
  await expect(page.getByTestId('chosen-count')).toContainText('1 company added');
  await expect(page.locator('[data-company-chip="Hansford Sensors Limited"]')).toContainText('hansfordsensors.com');
});

test('a one-character company does not query the register', async ({ page }) => {
  const { registerQueries } = await mockPage(page);
  await page.goto('/scraper?view=company');
  await page.getByLabel('Company', { exact: true }).fill('a');
  await page.waitForTimeout(600);
  expect(registerQueries).toEqual([]);
});

test('the guide explains the page instead of a chatbot driving it', async ({ page }) => {
  await mockPage(page);
  await page.goto('/scraper');

  // The assistant existed to translate a sentence into a form fill, which
  // mattered when the work was spread over six pages. The work is now four
  // numbered steps on one page, so the help is static, page-specific and
  // costs nothing to open.
  await expect(page.getByRole('button', { name: 'Open assistant' })).toHaveCount(0);

  await page.getByRole('button', { name: 'How this page works' }).click();
  const guide = page.getByRole('dialog', { name: /How Find contacts works/ });
  await expect(guide).toBeVisible();
  await expect(guide).toContainText('215,000 companies');
  await expect(guide).toContainText('Nothing leaves until you release the campaign');

  // It points at the next thing rather than ending the trail.
  await guide.getByRole('link', { name: /Next: Home/ }).click();
  await expect(page).toHaveURL(/^http:\/\/[^/]+\/$/);
});
