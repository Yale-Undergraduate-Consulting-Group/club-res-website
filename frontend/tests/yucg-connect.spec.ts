import { expect, test, type Page } from '@playwright/test';

/**
 * YUCG Connect: one sign-in page for members and alumni (the server routes the
 * Google sign-in, backend routers/auth.py), and the links between the member
 * site and the alumni portal for someone signed in to both. Every /api call is
 * mocked with synthetic data.
 */

const MEMBER = { id: 2, email: 'ana@yale.edu', name: 'Ana Lee', role: 'standard', is_active: 1, apps: ['core'] };
const ALUMNUS = { email: 'sam.alumnus@example.org', name: 'Sam Alumnus', board: false };

type Sessions = { member: boolean; alumni: boolean; alumniDevLogin?: number };

async function mock(page: Page, sessions: Sessions) {
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') {
      return route.fulfill({ json: sessions.member ? { authenticated: true, user: MEMBER } : { authenticated: false, user: null } });
    }
    if (path === '/api/alumni/me') {
      return sessions.alumni ? route.fulfill({ json: ALUMNUS }) : route.fulfill({ status: 401, json: { detail: 'Sign in on YUCG Connect' } });
    }
    if (path === '/api/alumni/home') {
      return route.fulfill({ json: { me: ALUMNUS, profile: { exists: true, listed: false }, posts: [], events: [], unread: null } });
    }
    if (path === '/api/auth/dev-login') {
      sessions.member = true;
      return route.fulfill({ json: { ok: true, dev: true } });
    }
    if (path === '/api/alumni/dev-login') {
      const status = sessions.alumniDevLogin ?? 200;
      if (status === 200) sessions.alumni = true;
      return route.fulfill({ status, json: status === 200 ? { ok: true, dev: true } : { detail: 'Alumni sign-in refused: not_eligible' } });
    }
    return route.fulfill({ json: [] });
  });
}

test.describe('YUCG Connect sign-in', () => {
  test('one page, one Google sign-in, and both local dev sign-ins on a loopback host', async ({ page }) => {
    await mock(page, { member: false, alumni: false });
    await page.goto('/login');
    await expect(page.getByRole('heading', { name: 'YUCG Connect' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Sign in with Google' })).toHaveCount(1);
    await expect(page.getByRole('button', { name: 'Dev sign-in: member (local only)' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Dev sign-in: alumni (local only)' })).toBeVisible();

    // The one button starts the one Google flow: no separate alumni start.
    const start = page.waitForRequest((request) => request.isNavigationRequest() && new URL(request.url()).pathname.startsWith('/api/'));
    await page.getByRole('button', { name: 'Sign in with Google' }).click();
    const url = new URL((await start).url());
    expect(url.pathname + url.search).toBe('/api/auth/google');
  });

  test('the former alumni sign-in link opens /login and keeps its error', async ({ page }) => {
    await mock(page, { member: false, alumni: false });
    await page.goto('/alumni/login?error=revoked');
    await expect(page).toHaveURL(/\/login\?error=revoked$/);
    await expect(page.getByRole('heading', { name: 'YUCG Connect' })).toBeVisible();
    await expect(page.getByText('Alumni access for this address has been turned off')).toBeVisible();
  });

  test('a refusal reads the same for members and alumni', async ({ page }) => {
    await mock(page, { member: false, alumni: false });
    await page.goto('/login?error=domain_not_allowed');
    await expect(page.getByText(/Members use their @yale\.edu account; alumni use the address the club added/)).toBeVisible();
  });

  test('the member dev sign-in opens the member site', async ({ page }) => {
    await mock(page, { member: false, alumni: false });
    await page.goto('/login');
    await page.getByRole('button', { name: 'Dev sign-in: member (local only)' }).click();
    await expect(page.getByRole('heading', { name: 'Welcome, Ana' })).toBeVisible();
  });

  test('the alumni dev sign-in opens the portal, and a refused one says why', async ({ page }) => {
    const sessions: Sessions = { member: false, alumni: false, alumniDevLogin: 403 };
    await mock(page, sessions);
    await page.goto('/login');
    await page.getByRole('button', { name: 'Dev sign-in: alumni (local only)' }).click();
    await expect(page).toHaveURL(/\/login\?error=not_eligible$/);
    await expect(page.getByText('This Google account is not on the club’s alumni list')).toBeVisible();

    sessions.alumniDevLogin = 200;
    await page.getByRole('button', { name: 'Dev sign-in: alumni (local only)' }).click();
    await expect(page.getByRole('heading', { name: 'YUCG Connect · Alumni' })).toBeVisible();
  });
});

test.describe('switching between the member site and the alumni portal', () => {
  test('someone signed in to both can switch either way', async ({ page }) => {
    await mock(page, { member: true, alumni: true });
    await page.goto('/');
    const account = page.getByRole('navigation', { name: 'Account' });
    await account.getByRole('link', { name: 'Switch to alumni' }).click();
    await expect(page).toHaveURL(/\/alumni$/);
    await expect(page.getByRole('heading', { name: 'YUCG Connect · Alumni' })).toBeVisible();
    await page.getByRole('link', { name: 'Member site' }).click();
    await expect(page.getByRole('heading', { name: 'Welcome, Ana' })).toBeVisible();
  });

  test('a member without an alumni session gets no switch link', async ({ page }) => {
    await mock(page, { member: true, alumni: false });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Welcome, Ana' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('navigation', { name: 'Account' }).getByRole('link', { name: 'Profile' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Switch to alumni' })).toHaveCount(0);
  });

  test('an alumnus without a member session gets no member-site link', async ({ page }) => {
    await mock(page, { member: false, alumni: true });
    await page.goto('/alumni');
    await expect(page.getByRole('heading', { name: 'YUCG Connect · Alumni' })).toBeVisible();
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Member site' })).toHaveCount(0);
  });
});
