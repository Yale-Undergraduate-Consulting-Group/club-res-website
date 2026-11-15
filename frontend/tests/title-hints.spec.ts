import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

/**
 * What the search is told is two settings in the rail: who to look for, with
 * titles in the member's own words when they want them, and how many people.
 * Both reach the run request of whichever company the sheet searches.
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
    else if (path === '/api/contacts') body = { items: [], total: 0, limit: 100, offset: 0 };
    else if (path === '/api/ai/models') body = { groups: [] };
    else if (/\/companies\/summary$|\/sequences$|\/custom-formats$|\/rosters$/.test(path)) body = [];
    else if (path.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    await route.fulfill({ json: body });
  });
  return mutations;
}

test('typed titles win over who-to-look-for, and they and the collection size reach the run request', async ({ page }) => {
  const mutations = await mockFlow(page);
  // A link can carry how many people to collect; it fills the "How many people" field.
  await page.goto('/scraper?view=company&companies=OpenAI,A24&max=120');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  const titles = rail.getByLabel('Other titles');

  // The titles field stays folded away until asked for; nothing else in the
  // rail searches or suggests - who to look for and how many are the settings.
  await expect(titles).toHaveCount(0);
  await expect(rail.getByTestId('role-suggestions')).toHaveCount(0);
  await expect(rail.getByLabel('Function to look for')).toHaveCount(0);
  await rail.getByRole('button', { name: 'Other titles…' }).click();
  await titles.fill('healthcare PMs, VPs');
  await expect(rail.getByText('Searching for: healthcare PMs, VPs')).toBeVisible();

  // The sheet searches the company in focus.
  await pipeline.locator('[data-lane="A24"]').getByTestId('lane-state').click();
  await pipeline.getByTestId('sheet-find').click();
  await expect.poll(() => mutations.filter((m) => m.path === '/api/yucgoutreach/runs').length).toBe(1);
  const run = mutations.find((m) => m.path === '/api/yucgoutreach/runs')?.body as Record<string, unknown>;
  expect(run.company_name).toBe('A24');
  expect(run.title_hints).toBe('healthcare PMs, VPs');
  expect(run.max_prospects).toBe(120);
});
