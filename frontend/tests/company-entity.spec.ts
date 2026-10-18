import { expect, test, type Page } from '@playwright/test';

/**
 * Which Barclays a search means. "Barclays" returned only Barclays Global
 * Service Centre (India) staff at @barclays.bank.in, because nothing carried
 * the entity or the country into the search. The resolver now returns an
 * entity profile; the chip shows its country, "Not this?" offers the other
 * group entities and "Any country", and every search sends the profile.
 */

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

const GSC = {
  legal_name: 'Barclays Global Service Centre Private Limited', display_name: 'Barclays Global Service Centre',
  brand_words: ['barclays'], hq_country: 'IN', hq_city: 'Pune', mail_domain: 'barclays.bank.in', mail_domain_evidence: 3,
  alt_mail_domains: [], exclude: [], target_country: 'IN', source: 'override',
};
const BARCLAYS = {
  legal_name: 'Barclays PLC', display_name: 'Barclays', brand_words: ['barclays'], hq_country: 'GB', hq_city: 'London',
  mail_domain: 'barclays.com', mail_domain_evidence: 4, alt_mail_domains: [{ domain: 'barclays.co.uk', country: 'GB' }],
  exclude: [{ name: 'Barclays Global Service Centre', country: 'IN', domains: ['barclays.bank.in'], kind: 'captive' }],
  target_country: 'GB', source: 'override',
};

async function mockEntity(page: Page, opts: { skipped?: boolean } = {}) {
  const runsCreated: Record<string, unknown>[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const p = url.pathname;
    let body: unknown = {};
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/yucgoutreach/resolve-company') {
      body = {
        name: 'Barclays', domain: 'barclays.com', domain_verified: true, linkedin_url: null, source: 'register',
        country: 'GB', entity: BARCLAYS,
        alternatives: [{ name: 'Barclays Global Service Centre', domain: 'barclays.bank.in', country: 'IN', kind: 'captive', entity: GSC }],
      };
    } else if (p === '/api/yucgoutreach/runs' && request.method() === 'POST') {
      runsCreated.push(request.postDataJSON());
      body = { id: 20 + runsCreated.length, status: 'queued' };
    } else if (p === '/api/yucgoutreach/runs') body = [];
    else if (/^\/api\/yucgoutreach\/runs\/\d+$/.test(p)) {
      const id = Number(p.split('/').pop());
      body = {
        id, company_name: 'Barclays', status: 'completed', progress_pct: 100,
        progress_message: opts.skipped
          ? 'Done — 12 saved · 30 skipped (26 other Barclays entities, 4 outside UK)'
          : 'Done — 12 saved',
      };
    } else if (/\/runs\/\d+\/prospects$/.test(p)) body = [];
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 8, offset: 0 };
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/role-suggestions') body = { company: url.searchParams.get('company'), roles: [], equivalents: [], sources: {} };
    else if (p === '/api/contacts') body = { items: [], total: 0, limit: 800, offset: 0 };
    else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/outreach/flows') body = [];
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    await route.fulfill({ json: body });
  });
  return { runsCreated };
}

async function addBarclays(page: Page) {
  await page.goto('/scraper?view=company');
  const rail = page.locator('[data-section="campaign-pipeline"] [data-rail]');
  await rail.getByLabel('Company').fill('Barclays');
  await rail.getByLabel('Company').press('Enter');
  const chip = rail.locator('[data-company-chip]').first();
  await expect(chip).toContainText('barclays.com ✓');
  return { rail, chip };
}

test('the chip names the entity\'s country and the search carries the entity', async ({ page }) => {
  const { runsCreated } = await mockEntity(page);
  const { chip } = await addBarclays(page);
  await expect(chip.getByTestId('chip-country')).toHaveText('· UK');

  // Sure of it, so it searches by itself, telling the search which Barclays.
  await expect.poll(() => runsCreated.length).toBe(1);
  const entity = runsCreated[0].entity as Record<string, unknown>;
  expect(entity.legal_name).toBe('Barclays PLC');
  expect(entity.mail_domain).toBe('barclays.com');
  // No country chosen: the search skips the excluded entities and their
  // countries, not everyone outside the UK.
  expect(entity.target_country).toBeNull();
});

test('"UK only" is a choice the member makes, and the search then enforces it', async ({ page }) => {
  const { runsCreated } = await mockEntity(page);
  const { chip } = await addBarclays(page);
  await expect.poll(() => runsCreated.length).toBe(1);
  await chip.getByRole('button', { name: 'Not this?' }).click();
  await chip.getByRole('button', { name: 'UK only' }).click();
  await expect(chip.getByTestId('chip-country')).toHaveText('· UK only');
});

test('"Not this?" closes on Escape and on a click elsewhere, never trapping the step', async ({ page }) => {
  await mockEntity(page);
  const { rail, chip } = await addBarclays(page);
  const panel = rail.getByRole('button', { name: /Barclays Global Service Centre/ });
  await chip.getByRole('button', { name: 'Not this?' }).click();
  await expect(panel).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  await chip.getByRole('button', { name: 'Not this?' }).click();
  await expect(panel).toBeVisible();
  await page.locator('h1, [data-step="1"]').first().click();
  await expect(panel).toHaveCount(0);
  await rail.getByRole('button', { name: 'Next: tick who gets it' }).click();
});

test('another group entity is one pick away, with its own country and domain', async ({ page }) => {
  const { runsCreated } = await mockEntity(page);
  const { rail, chip } = await addBarclays(page);
  await expect.poll(() => runsCreated.length).toBe(1);

  await chip.getByRole('button', { name: 'Not this?' }).click();
  const gsc = rail.getByRole('button', { name: /Barclays Global Service Centre · IN · service centre · barclays\.bank\.in/ });
  await expect(gsc).toBeVisible();
  await gsc.click();

  const swapped = rail.locator('[data-company-chip="Barclays Global Service Centre"]');
  await expect(swapped).toContainText('· IN');
  await expect(swapped).toContainText('barclays.bank.in');
  await expect(rail.locator('[data-company-chip="Barclays"]')).toHaveCount(0);
  await expect.poll(() => runsCreated.length).toBe(2);
  expect((runsCreated[1].entity as Record<string, unknown>).target_country).toBe('IN');
});

test('"Any country" widens where the search looks', async ({ page }) => {
  await mockEntity(page);
  const { chip } = await addBarclays(page);
  await chip.getByRole('button', { name: 'Not this?' }).click();
  await chip.getByRole('button', { name: 'Any country' }).click();
  await expect(chip.getByTestId('chip-country')).toHaveText('· any country');
});

test('a search that skipped people elsewhere offers them back in one click', async ({ page }) => {
  const { runsCreated } = await mockEntity(page, { skipped: true });
  await addBarclays(page);
  await page.getByRole('button', { name: 'Next: tick who gets it' }).click();

  const group = page.getByTestId('recipient-picker').locator('[data-company="Barclays"]');
  await expect(group.getByText(/30 skipped \(26 other Barclays entities, 4 outside UK\)/)).toBeVisible({ timeout: 15_000 });
  await group.getByRole('button', { name: 'Search all countries' }).click();
  await expect.poll(() => runsCreated.length).toBe(2);
  expect((runsCreated[1].entity as Record<string, unknown>).target_country).toBe('*');
});
