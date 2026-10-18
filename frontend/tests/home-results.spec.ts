import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'member', is_active: 1 };

const DASHBOARD = {
  contacts_discovered_today: 3,
  emails_in_queue: 5,
  active_campaigns: 1,
  total_sent: 40,
  opened: 12,
  open_rate: 30,
  reply_rate: 7.5,
  mine: { mailed: 12, replied: 2, bounced: 1, queued: 4, awaiting: 9, reply_rate: 16.7 },
  club: { mailed: 40, replied: 3, bounced: 2, queued: 5, awaiting: 35, reply_rate: 7.5 },
  my_sectors: [{ sector: 'Entertainment', count: 7 }, { sector: 'Biotech', count: 2 }],
  club_sectors: [{ sector: 'Manufacturing', count: 30 }, { sector: 'Entertainment', count: 9 }],
};

async function mockHome(page: Page, dashboard: Record<string, unknown> = DASHBOARD) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/analytics/dashboard') body = dashboard;
    else if (path === '/api/analytics/leaderboard') body = [];
    else if (path === '/api/analytics/companies-reached') body = [];
    else if (path === '/api/analytics/due-follow-ups') body = { count: 0 };
    else if (path === '/api/campaigns') body = [];
    else if (path === '/api/contacts/pipeline-metrics') body = { by_status: [] };
    else if (path.startsWith('/api/gmail')) body = { connected: false };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test('the front page shows results as a chart, the member’s own and the club’s', async ({ page }) => {
  await mockHome(page);
  await page.goto('/');

  const results = page.getByLabel('Results');
  await expect(results).toBeVisible();

  // Both halves are present. A club total alone hides whether any individual
  // is doing anything; a personal total alone hides whether the club is.
  const mine = results.getByRole('img', { name: /^Yours:/ });
  const club = results.getByRole('img', { name: /^The club:/ });
  await expect(mine).toBeVisible();
  await expect(club).toBeVisible();

  // The chart is a real chart: one drawn slice per non-zero outcome.
  await expect(mine.locator('path')).toHaveCount(4);
  // The accessible name carries the whole reading, so the chart is usable
  // without seeing it.
  await expect(mine.locator('title')).toHaveText(/2 replied.*1 bounced/);

  // Percentages are of that scope, not of the club: 2 of 16 counted people.
  await expect(results).toContainText('13%');

  // Sector split is what the member chose to work on, shown next to the club's.
  await expect(results).toContainText('What you work on');
  await expect(results).toContainText('Entertainment');
  await expect(results).toContainText('What the club works on');
  await expect(results).toContainText('Manufacturing');
});

test('a member who has mailed nobody is told where to start, not shown a blank chart', async ({ page }) => {
  await mockHome(page, {
    ...DASHBOARD,
    mine: { mailed: 0, replied: 0, bounced: 0, queued: 0, awaiting: 0, reply_rate: 0 },
    my_sectors: [],
  });
  await page.goto('/');

  const results = page.getByLabel('Results');
  await expect(results).toContainText('You have not mailed anyone yet');
  // The club's chart still draws, so a new member sees there is work happening.
  await expect(results.getByRole('img', { name: /^The club:/ })).toBeVisible();
  await expect(results).toContainText('not a handed-down one');
});

test('a slice is interactive: it responds to hover and opens its own rows', async ({ page }) => {
  await mockHome(page);
  await page.goto('/');

  const results = page.getByLabel('Results');
  const mine = results.getByRole('img', { name: /^Yours:/ });

  // Idle state names the whole, not a slice.
  await expect(results.getByText('16 people').first()).toBeVisible();

  // Hovering a wedge pulls it out and names that slice's share. A static
  // image cannot do either.
  const replied = mine.locator('g').first();
  await replied.hover();
  await expect(results.getByText('2 of 16 — click to open')).toBeVisible();
  // The active wedge is outlined and the others dim. Nothing moves: a wedge
  // that slides out from under the cursor flickers between hover states.
  await expect(replied.locator('path, circle').first()).toHaveAttribute('stroke', 'var(--ink)');
  await expect(mine.locator('g').nth(1).locator('path, circle').first()).toHaveAttribute('opacity', '0.45');

  // Colours come from the club tokens, not arbitrary hex.
  const fill = await replied.locator('path, circle').first().getAttribute('fill');
  expect(fill).toMatch(/^var\(--chart-/);

  // Activating it opens exactly those people rather than an unfiltered table.
  await replied.click();
  await expect(page).toHaveURL(/\/outreach\?status=replied&owner=me/);
});

test('slice keyboard focus reaches the same rows as the mouse', async ({ page }) => {
  await mockHome(page);
  await page.goto('/');

  const bounced = page.getByLabel('Results')
    .getByRole('link', { name: '1 bounced — open the list' }).first();
  await bounced.focus();
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/\?filter=needs_attention/);
});

test('"yours" stays yours after the click: the pipeline asks for your rows only', async ({ page }) => {
  const asked: string[] = [];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/contacts') asked.push(url.search);
    let body: unknown = {};
    if (url.pathname === '/api/auth/me') body = { authenticated: true, user };
    else if (url.pathname === '/api/analytics/dashboard') body = DASHBOARD;
    else if (url.pathname === '/api/contacts') body = { items: [], total: 0, limit: 1000, offset: 0 };
    else if (url.pathname === '/api/outreach/pipeline-metrics') body = { by_status: [] };
    else if (url.pathname === '/api/analytics/leaderboard') body = [];
    else if (url.pathname === '/api/analytics/companies-reached') body = [];
    else if (url.pathname === '/api/analytics/due-follow-ups') body = { count: 0 };
    else if (url.pathname === '/api/campaigns') body = [];
    else if (url.pathname === '/api/contacts/pipeline-metrics') body = { by_status: [] };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });

  await page.goto('/outreach?status=replied&owner=me');
  await expect.poll(() => asked.some(s => s.includes('mine_only=true') && s.includes('pipeline_status=replied')))
    .toBe(true);
});
