import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

/**
 * The search asks a company what its people are called before it looks for
 * them: role bubbles under the titles field turn "healthcare PMs" into the
 * titles that company actually uses. The bubbles belong to the company in
 * focus - the lane last pointed at - and only ever add to the field when
 * clicked, so one company's vocabulary is never quietly applied to another.
 */
async function mockFlow(page: Page) {
  const mutations: { path: string; body: unknown }[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') mutations.push({ path, body: request.postDataJSON() });
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/yucgoutreach/runs' && request.method() === 'POST') body = { id: 9, status: 'queued' };
    else if (path === '/api/yucgoutreach/runs') body = [];
    else if (path === '/api/yucgoutreach/role-suggestions') {
      const q = new URL(request.url()).searchParams;
      const company = q.get('company');
      body = company === 'OpenAI' ? {
        company,
        roles: [
          { title: 'Member of Technical Staff', count: 9, source: 'run' },
          { title: 'Product Lead', count: 4, source: 'search' },
          { title: 'Chief Operating Officer', count: 1, source: 'roster' },
        ],
        equivalents: q.get('hints')
          ? [
              { asked: 'healthcare PMs', at_company: ['Product Lead'], note: 'No PM title in use; product roles are Product Lead.' },
              { asked: 'VPs', at_company: [], note: 'No VP titles observed.' },
            ]
          : [],
        sources: { run: 1, roster: 1, catalog: 0, search: 1 },
      } : { company, roles: [{ title: 'Studio Head', count: 2, source: 'roster' }], equivalents: [], sources: { run: 0, roster: 1, catalog: 0, search: 0 } };
    }
    else if (path === '/api/contacts') body = { items: [], total: 0, limit: 100, offset: 0 };
    else if (path === '/api/ai/models') body = { groups: [] };
    else if (/\/companies\/summary$|\/sequences$|\/custom-formats$|\/rosters$/.test(path)) body = [];
    else if (path.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    await route.fulfill({ json: body });
  });
  return mutations;
}

test('role bubbles translate asked roles into the focused company vocabulary, and only add when clicked', async ({ page }) => {
  const mutations = await mockFlow(page);
  // A link can carry how many people to collect; there is no field for it.
  await page.goto('/scraper?view=company&companies=OpenAI,A24&max=120');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const bubbles = rail.getByTestId('role-suggestions');
  const titles = rail.getByLabel('Other titles');

  // The first chosen company is in focus; its observed roles are offered,
  // not typed into a field - the titles field stays folded away.
  await expect(bubbles.getByText('Roles seen at OpenAI')).toBeVisible({ timeout: 10_000 });
  await expect(bubbles.getByRole('button', { name: /Member of Technical Staff/ })).toBeVisible();
  await expect(titles).toHaveCount(0);

  // Clicking one of the company's titles opens the field with it.
  await bubbles.getByRole('button', { name: /Member of Technical Staff/ }).click();
  await expect(titles).toHaveValue('Member of Technical Staff');

  // Typing hints triggers the equivalence strip.
  await titles.fill('healthcare PMs, VPs');
  await expect(bubbles.getByText('No PM title in use; product roles are Product Lead.')).toBeVisible({ timeout: 10_000 });
  await expect(bubbles.getByText('no matching title seen at OpenAI')).toBeVisible();

  // Clicking the company's equivalent appends it to the hints and disables that chip.
  await bubbles.getByRole('button', { name: '+ Product Lead' }).click();
  await expect(titles).toHaveValue('healthcare PMs, VPs, Product Lead');
  await expect(bubbles.getByRole('button', { name: /^Product Lead/ })).toBeDisabled();

  // Clicking another lane moves the bubbles to that company. What the
  // member typed stays; OpenAI's vocabulary is not carried across.
  await pipeline.locator('[data-lane="A24"]').getByTestId('lane-state').click();
  await expect(bubbles.getByText('Roles seen at A24')).toBeVisible({ timeout: 10_000 });
  await expect(bubbles.getByRole('button', { name: /Member of Technical Staff/ })).toHaveCount(0);
  await expect(titles).toHaveValue('healthcare PMs, VPs, Product Lead');

  // Typed titles win over who-to-look-for, and they and the collection
  // size reach the run request.
  await expect(rail.getByText('Searching for: healthcare PMs, VPs, Product Lead')).toBeVisible();
  const lane = pipeline.locator('[data-lane="A24"]');
  await lane.hover();
  await lane.getByRole('button', { name: 'Find people' }).click();
  await expect.poll(() => mutations.filter((m) => m.path === '/api/yucgoutreach/runs').length).toBe(1);
  const run = mutations.find((m) => m.path === '/api/yucgoutreach/runs')?.body as Record<string, unknown>;
  expect(run.company_name).toBe('A24');
  expect(run.title_hints).toBe('healthcare PMs, VPs, Product Lead');
  expect(run.max_prospects).toBe(120);
  await page.screenshot({ path: 'test-results/role-bubbles.png', fullPage: true });
});

test('a malformed role-suggestions payload never breaks the Find people page', async ({ page }) => {
  // The endpoint is advisory; an empty or partial body (proxy hiccup, older
  // deploy) must degrade to no chips, not white-screen the step.
  await mockFlow(page);
  await page.route('**/api/yucgoutreach/role-suggestions*', route => route.fulfill({ json: {} }));
  await page.goto('/scraper?view=company&company=OpenAI');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  await rail.getByRole('button', { name: 'Other titles…' }).click();
  await rail.getByLabel('Other titles').fill('PMs');
  await expect(rail.getByLabel('Other titles')).toHaveValue('PMs');
  const lane = pipeline.locator('[data-lane="OpenAI"]');
  await lane.hover();
  await expect(lane.getByRole('button', { name: 'Find people' })).toBeEnabled();
});
