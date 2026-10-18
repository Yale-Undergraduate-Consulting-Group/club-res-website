import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

const PEOPLE = [
  { id: 1, name: 'Jean Bartik', email: 'jean.bartik@a24films.com', title: 'Head of Acquisitions', company: 'A24' },
  { id: 2, name: 'Klara Dan', email: 'klara.dan@a24films.com', title: 'VP Partnerships', company: 'A24' },
  // No name, so a message using {first} cannot be rendered for them.
  { id: 3, name: '', email: 'info@a24films.com', title: '', company: 'A24' },
];

/** The resolver's contract: a checked website only where one could be
 *  checked, otherwise the name as typed. */
function resolveFor(q: string) {
  if (q === 'NVIDIA Corporation') {
    return { name: 'NVIDIA Corporation', domain: 'nvidia.com', domain_verified: true, linkedin_url: null, source: 'register', alternatives: [] };
  }
  if (/linkedin\.com\/company\/bartik-foundation/i.test(q)) {
    return { name: 'Bartik Foundation', domain: 'bartik.org', domain_verified: true, linkedin_url: 'https://www.linkedin.com/company/bartik-foundation', source: 'linkedin', alternatives: [] };
  }
  return { name: q, domain: null, domain_verified: false, linkedin_url: null, source: 'typed', alternatives: [] };
}

async function mockPipeline(page: Page) {
  const built: Record<string, unknown>[] = [];
  const sequences: Record<string, unknown>[] = [];
  const runsCreated: Record<string, unknown>[] = [];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    let body: unknown = {};
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') body = { items: PEOPLE, total: PEOPLE.length, limit: 400, offset: 0 };
    else if (p === '/api/campaigns/build') {
      const payload = route.request().postDataJSON();
      // The server renders; the mock mirrors its contract so the spec covers
      // the wiring rather than re-implementing the renderer.
      const named = PEOPLE.filter(c => c.name && payload.contact_ids?.includes(c.id));
      const unnamed = PEOPLE.filter(c => !c.name && payload.contact_ids?.includes(c.id));
      if (payload.preview_only) {
        body = {
          recipients: (payload.contact_ids || []).length,
          ready: named.length,
          held: unnamed.map(c => ({ contact_id: c.id, email: c.email, reason: `${c.email} has no first on record, and the message uses it.` })),
          sample: named[0]
            ? { email: named[0].email, subject: `A24 and Yale`, body: `Hi ${named[0].name.split(' ')[0]}, about A24.` }
            : null,
        };
      } else {
        built.push(payload);
        body = { campaign_id: 42, created: named.length, name: 'A24', held: [] };
      }
    }
    else if (p === '/api/outreach/sequences' && route.request().method() === 'POST') {
      sequences.push(route.request().postDataJSON());
      body = { id: 7, ok: true };
    }
    else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/contacts/email-patterns') body = { domain: '', count: 0, patterns: [] };
    else if (p === '/api/yucgoutreach/resolve-company') body = resolveFor(url.searchParams.get('q') || '');
    else if (p === '/api/yucgoutreach/runs' && route.request().method() === 'POST') {
      runsCreated.push(route.request().postDataJSON());
      body = { id: 9, status: 'queued' };
    }
    else if (p === '/api/yucgoutreach/runs/9') body = { id: 9, company_name: runsCreated[0]?.company_name, status: 'running', progress_pct: 10, progress_message: 'Searching…' };
    else if (p === '/api/yucgoutreach/runs') body = [];
    else if (p === '/api/outreach/flows') body = [];
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (p === '/api/projects/suggest-citations') {
      body = {
        projects: [{ id: 1, client_name: 'Google', description: 'Brand refresh', semester: 'Spring 2026' }],
        team_experience: [{ user_name: 'Aaron Combs', role_in_project: 'Market Analyst', client_name: 'Adidas', semester: 'Fall 2025' }],
      };
    }
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    await route.fulfill({ json: body });
  });
  return { built, sequences, runsCreated };
}

test('companies to people, then each person handed to Drafts', async ({ page }) => {
  await mockPipeline(page);
  await page.goto('/scraper?view=company');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');

  // 1. Companies are a multiple choice, not one at a time.
  await rail.getByLabel('Company').fill('A24');
  await rail.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(rail.getByTestId('chosen-count')).toContainText('1 company added');
  await rail.getByRole('button', { name: 'Next: tick who gets it' }).click();

  // 2. The people found, grouped by company, with the option to drop one.
  await expect(pipeline.getByText('Jean Bartik')).toBeVisible();
  await expect(pipeline.getByText('Klara Dan')).toBeVisible();
  await rail.getByRole('button', { name: /^Write to these/ }).click();

  // 3. No shared template: the exact people ticked go to Drafts, where each
  //    gets an email of their own.
  await expect(rail.getByText(/advisory note on two\s+or three projects/)).toBeVisible();
  await expect(rail.getByLabel('Subject')).toHaveCount(0);
  await rail.getByRole('button', { name: /^Write to these \d+ in Drafts/ }).click();
  await expect(page).toHaveURL(/\/studio\?companies=A24&contact_ids=\d+(%2C\d+)*$/);
});

test('a company that is not on file anywhere can still be worked', async ({ page }) => {
  await mockPipeline(page);
  await page.goto('/scraper?view=company');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');

  // The public register is a starting point, not a fence. A member who knows
  // a company nobody has heard of types the name and works it exactly like
  // any other.
  await rail.getByLabel('Company').fill('Bartik Family Foundation');
  await rail.getByRole('button', { name: 'Add', exact: true }).click();

  await expect(rail.locator('[data-company-chip="Bartik Family Foundation"]')).toBeVisible();
  await expect(rail.getByTestId('chosen-count')).toContainText('1 company added');

  // And it carries into the next step rather than being dropped as unknown.
  await rail.getByRole('button', { name: 'Next: tick who gets it' }).click();
  await expect(pipeline.locator('[data-lane="Bartik Family Foundation"]')).toBeVisible();
  // With nobody on file the company keeps its group and offers the search
  // that would fill it, rather than silently vanishing from the step.
  await expect(pipeline.getByRole('button', { name: 'Find people here' })).toBeVisible();
});

test('a large company selection stays a count and a screenful, not a wall of chips', async ({ page }) => {
  await mockPipeline(page);
  const many = Array.from({ length: 300 }, (_, i) => `Company ${i + 1}`);
  await page.goto(`/scraper?view=company&companies=${encodeURIComponent(many.join(','))}`);

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  await rail.getByRole('button', { name: /Add companies/ }).click();

  // Every chosen company used to become a DOM node. A selection this size is
  // a count plus the ones being worked on, with the rest one click away.
  await expect(rail.getByTestId('chosen-count')).toContainText('300 companies added');
  const chips = rail.getByTestId('company-chips').locator('[data-company-chip]');
  const collapsed = await chips.count();
  expect(collapsed).toBeLessThanOrEqual(24);
  await rail.getByRole('button', { name: /^\+\d+ more$/ }).click();
  expect(await chips.count()).toBeGreaterThan(collapsed);

  // And the step says the campaign ceiling before the server refuses it.
  await page.goto(`/scraper?view=company&companies=${encodeURIComponent(
    Array.from({ length: 501 }, (_, i) => `Big ${i + 1}`).join(','))}`);
  await rail.getByRole('button', { name: /Add companies/ }).click();
  await expect(rail.getByText(/at most 500/)).toBeVisible();
});

test('a best guess is corrected from its chip by pasting the company\'s LinkedIn page', async ({ page }) => {
  // Replaces the domain-guess warning and its "Use it" button: the resolver
  // checks websites now, and a guess is fixed on the company itself rather
  // than in a settings panel.
  const { runsCreated } = await mockPipeline(page);
  await page.goto('/scraper?view=company');
  const rail = page.locator('[data-section="campaign-pipeline"] [data-rail]');

  await rail.getByLabel('Company').fill('Bartik');
  await rail.getByLabel('Company').press('Enter');
  const guess = rail.locator('[data-company-chip="Bartik"]');
  await guess.getByRole('button', { name: 'Best guess · Not this?' }).click();
  await guess.getByLabel('Their LinkedIn page').fill('https://www.linkedin.com/company/bartik-foundation');
  await guess.getByRole('button', { name: 'Use', exact: true }).click();

  const fixed = rail.locator('[data-company-chip="Bartik Foundation"]');
  await expect(fixed).toContainText('bartik.org ✓');
  await expect(fixed.getByRole('button', { name: 'Not this?', exact: true })).toBeVisible();
  await expect(guess).toHaveCount(0);
  await expect(rail.getByTestId('chosen-count')).toContainText('1 company added');
  // Now sure of it, the search starts with the checked website.
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0]).toMatchObject({ company_name: 'Bartik Foundation', company_domain: 'bartik.org' });
});

test('picking a suggested company adds it with its domain, not the half-typed text, and never removes', async ({ page }) => {
  const { runsCreated } = await mockPipeline(page);
  // Registered after the shared mock, so the register answers first.
  await page.route('**/api/yucgoutreach/register?*', route => route.fulfill({ json: {
    items: [{ company_name: 'NVIDIA Corporation', company_domain: 'nvidia.com', sector_label: 'Semiconductors' }],
    total: 1, limit: 8, offset: 0,
  } }));
  await page.goto('/scraper?view=company');
  const rail = page.locator('[data-section="campaign-pipeline"] [data-rail]');

  await rail.getByLabel('Company').fill('nvid');
  await page.getByRole('option', { name: /NVIDIA Corporation/ }).click();
  const chip = rail.locator('[data-company-chip="NVIDIA Corporation"]');
  await expect(chip).toContainText('nvidia.com ✓');
  await expect(rail.locator('[data-company-chip="nvid"]')).toHaveCount(0);
  await expect(rail.getByTestId('chosen-count')).toContainText('1 company added');

  // Adding it again used to toggle it off.
  await rail.getByLabel('Company').fill('nvid');
  await page.getByRole('option', { name: /NVIDIA Corporation/ }).click();
  await expect(rail.getByTestId('chosen-count')).toContainText('1 company added');
  await expect(chip).toBeVisible();

  // Its domain came with it: the search is told, not left to guess one.
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0]).toMatchObject({ company_name: 'NVIDIA Corporation', company_domain: 'nvidia.com' });
});
