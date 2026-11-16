import { expect, test, type Page } from '@playwright/test';

/**
 * Moving between main pages never blanks the screen: the shell and the page
 * on screen stay until the next page can render, a slim bar shows while it
 * loads, and the main region is never empty. All data is synthetic.
 */
const admin = { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1, apps: ['core', 'outreach', 'resources'] };

async function mockApi(page: Page, delay: number) {
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === '/api/auth/me') body = { authenticated: true, user: admin };
    else if (path === '/api/resources') body = {
      items: [{
        id: 1, kind: 'past_project', title: 'Pricing study', client: 'Northwind', client_hidden: false, semester: 'Fall 2025',
        industry: 'Healthcare', industries: ['Healthcare'], areas: null, description: null, code_name: null, team_size: null,
        disclosure: 'public', status: null, files_open: true, can_manage: false, files: [], files_hidden_count: 0, team: [],
        created_at: 0, link: null,
      }],
      total: 1,
      facets: { kinds: [{ value: 'past_project', count: 1 }], semesters: [], industries: [], disclosures: [] },
    };
    else if (path === '/api/directory/summary') body = {
      total: 0, capped: false, ready: 0,
      proof: { replied: 0, delivered: 0, bounced: 0, mailbox_confirmed: 0, catch_all: 0, mailbox_rejected: 0, unverified: 0 },
      reach: { replied: 0, bounced: 0, contacted: 0, queued: 0, not_contacted: 0 },
      backlog: { enabled: false, credits_used_today: 0, credits_cap_today: 0, verified_total: 0, queue_remaining: 0 },
    };
    else if (path === '/api/directory') body = { items: [], total: 0, limit: 50, offset: 0, capped: false };
    else if (path === '/api/contacts') body = { items: [], total: 0, limit: 100, offset: 0 };
    else if (path === '/api/analytics/due-follow-ups') body = { count: 0 };
    else if (path === '/api/settings' || path === '/api/auth/profile' || path.startsWith('/api/gmail')) body = {};
    else if (path.startsWith('/api/auth/') && path.endsWith('/status')) body = { connected: false };
    else if (path.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    if (delay) await pause(delay);
    await route.fulfill({ json: body }).catch(() => undefined);
  });
}

function pause(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

/** Records, every frame, whether the main region showed nothing (or the whole app was replaced by a loader). */
async function watchMain(page: Page) {
  await page.evaluate(() => {
    const frames: string[] = [];
    (window as Window & { blankFrames?: string[] }).blankFrames = frames;
    const tick = () => {
      const main = document.querySelector('main');
      const text = main instanceof HTMLElement ? main.innerText.trim() : '';
      if (!text || document.querySelector('.app-auth-loading')) frames.push(`${location.pathname} at ${Math.round(performance.now())}ms`);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}

const blankFrames = (page: Page) => page.evaluate(() => (window as Window & { blankFrames?: string[] }).blankFrames ?? []);

test('switching main pages keeps the shell and a page on screen every frame, with a loading bar while slow', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'one run covers the shell');
  await mockApi(page, 600);
  // Slow page code, as on a first visit over a slow connection.
  await page.route(/\/src\/pages\/(Dashboard|Directory)\.tsx/, async (route) => {
    await pause(600);
    await route.fallback();
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Welcome/ })).toBeVisible();
  await watchMain(page);

  const tools = page.getByRole('region', { name: 'Your tools' });
  await tools.getByRole('link', { name: /Resources/ }).click();
  await expect(page.getByRole('article', { name: 'Pricing study' })).toBeVisible();

  await page.getByRole('link', { name: 'YUCG Resources home' }).click();
  await expect(page.getByRole('heading', { name: /Welcome/ })).toBeVisible();
  await tools.getByRole('link', { name: /Outreach/ }).click();
  // The bar shows while the Overview's code and first data load, over the page still on screen.
  await expect(page.getByRole('progressbar', { name: 'Loading page' })).toBeVisible();
  await expect(page).toHaveURL(/\/campaigns$/);
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();

  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Directory' }).click();
  await expect(page.getByRole('heading', { name: 'Directory' })).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Loading page' })).toHaveCount(0);

  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();

  expect(await blankFrames(page)).toEqual([]);
  await page.unrouteAll({ behavior: 'ignoreErrors' });
});

test('page code is fetched before a click: on hover and when the browser is idle', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'one run covers the shell');
  await mockApi(page, 0);
  const fetched: string[] = [];
  page.on('request', (request) => {
    const match = request.url().match(/\/src\/pages\/(\w+)\.tsx/);
    if (match) fetched.push(match[1]);
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: /Welcome/ })).toBeVisible();
  await expect.poll(() => fetched).toEqual(expect.arrayContaining(['Resources', 'Dashboard', 'Directory']));
});

test('a page that replaces another fades in, except with reduced motion', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'one run covers the shell');
  await mockApi(page, 0);
  const fadeAfterOpening = async () => {
    await page.goto('/');
    await page.getByRole('region', { name: 'Your tools' }).getByRole('link', { name: /Resources/ }).click();
    await expect(page.getByRole('article', { name: 'Pricing study' })).toBeVisible();
    return page.locator('.app-shell-main-inner').evaluate((el) => getComputedStyle(el).animationName);
  };
  // The page the app opens on has nothing to fade from.
  await page.goto('/resources');
  expect(await page.locator('.app-shell-main-inner').evaluate((el) => getComputedStyle(el).animationName)).toBe('none');
  expect(await fadeAfterOpening()).toBe('app-page-in');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  expect(await fadeAfterOpening()).toBe('none');
});
