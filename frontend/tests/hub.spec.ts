import { expect, test, type Page } from '@playwright/test';

/** The portal's Home: one card per app the member may use, each leading to
 *  that app's home page. */

async function mock(page: Page, user: Record<string, unknown>) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user } });
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
  await tools.getByRole('link', { name: /Outreach/ }).click();
  await expect(page).toHaveURL(/\/campaigns$/);
});

test('an administrator gets the admin link on Home', async ({ page }) => {
  await mock(page, { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1 });
  await page.goto('/');
  await expect(page.getByRole('main').getByRole('link', { name: 'Admin', exact: true })).toBeVisible();
});
