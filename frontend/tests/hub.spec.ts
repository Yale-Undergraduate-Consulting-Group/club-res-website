import { expect, test, type Page } from '@playwright/test';

/** The portal's Home: the whole page, one tile per app the member may use,
 *  each opening that app with its own bar. */

async function mock(page: Page, user: Record<string, unknown>) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user } });
    if (path === '/api/surveys/library') {
      return route.fulfill({ json: { qualtrics: false, surveys: 0, questions: 0, names: [], synced_at: null, syncing: false, last_sync: null } });
    }
    await route.fulfill({ json: [] });
  });
}

test('Home shows a card for each app the member may use and opens its home page', async ({ page }) => {
  await mock(page, { id: 2, email: 'ana@yale.edu', name: 'Ana Lee', role: 'standard', is_active: 1, apps: ['core', 'outreach', 'surveys'] });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome, Ana' })).toBeVisible();
  const tools = page.getByRole('region', { name: 'Your tools' });
  await expect(tools.getByRole('link', { name: /Survey builder/ })).toBeVisible();
  await expect(tools.getByRole('link', { name: /Market sentiment/ })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Admin', exact: true })).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(0);
  await tools.getByRole('link', { name: /Outreach/ }).click();
  await expect(page).toHaveURL(/\/campaigns$/);
  // Outreach's bar: Home and Outreach's own pages, not the other tools.
  const bar = page.getByRole('navigation', { name: 'Main navigation' }).first();
  await expect(bar.getByRole('link', { name: 'Home' })).toBeVisible();
  await expect(bar.getByRole('link', { name: 'Survey builder' })).toHaveCount(0);
  await bar.getByRole('link', { name: 'Home' }).click();
  await expect(page).toHaveURL(/\/$/);
  // Leaving an app removes its bar; opening another shows only that one.
  await expect(page.locator('.app-shell-header')).toHaveCount(0);
  await page.getByRole('region', { name: 'Your tools' }).getByRole('link', { name: /Survey builder/ }).click();
  await expect(page.locator('.app-shell-header')).toHaveCount(1);
  await expect(page.locator('.app-shell-header')).toHaveAttribute('data-app', 'surveys');
});

test('a member without a Google name is greeted by the first part of their address', async ({ page }) => {
  await mock(page, { id: 4, email: 'andre.h.costa@yale.edu', name: 'andre.h.costa', role: 'standard', is_active: 1 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome, Andre' })).toBeVisible();
});

test('an administrator gets the admin link on Home', async ({ page }) => {
  await mock(page, { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1 });
  await page.goto('/');
  await expect(page.getByRole('main').getByRole('link', { name: 'Admin', exact: true })).toBeVisible();
});
