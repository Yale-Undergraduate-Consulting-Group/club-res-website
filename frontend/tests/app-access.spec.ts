import { expect, test, type Page } from '@playwright/test';

/** Per-app access (docs/PLATFORM.md step 3): the menu shows only the apps a
 *  member may use, and an admin restricts an app from Admin → Apps. */

const ADMIN = { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1, apps: ['core', 'outreach', 'projects'] };
const BEN = { id: 3, email: 'ben@yale.edu', name: 'Ben', role: 'standard', is_active: 1, apps: ['core', 'projects'] };
const ANA = { id: 2, email: 'ana@yale.edu', name: 'Ana', role: 'standard', is_active: 1 };

async function mock(page: Page, user: typeof ADMIN) {
  const updates: { app: string; body: Record<string, unknown> }[] = [];
  let outreach = { app: 'outreach', title: 'Outreach', restricted: false, member_ids: [] as number[] };
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user } });
    if (path === '/api/admin/apps') return route.fulfill({ json: [outreach, { app: 'projects', title: 'Projects', restricted: false, member_ids: [] }] });
    if (path === '/api/admin/apps/outreach' && request.method() === 'PUT') {
      const body = request.postDataJSON();
      updates.push({ app: 'outreach', body });
      outreach = { ...outreach, ...body };
      return route.fulfill({ json: { ok: true } });
    }
    if (path === '/api/admin/users') return route.fulfill({ json: [ADMIN, ANA, BEN] });
    await route.fulfill({ json: [] });
  });
  return { updates };
}

test('a member without Outreach does not see it in the menu', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the desktop header lists every destination');
  await mock(page, BEN);
  await page.goto('/');
  const header = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(header.getByRole('link', { name: 'Home' })).toBeVisible();
  await expect(header.getByRole('link', { name: 'Find contacts' })).toHaveCount(0);
  await expect(header.getByText('Outreach')).toHaveCount(0);
});

test('an admin restricts Outreach to chosen members from Admin → Apps', async ({ page }) => {
  const { updates } = await mock(page, ADMIN);
  await page.goto('/admin?view=apps');
  const card = page.getByTestId('app-access-outreach');
  await expect(card.getByText('All members', { exact: true }).first()).toBeVisible();
  await expect(card.getByRole('button', { name: 'Save' })).toBeDisabled();

  await card.getByRole('radio', { name: 'Only chosen members' }).click();
  await card.getByRole('checkbox', { name: 'Ana' }).check();
  await card.getByRole('button', { name: 'Save' }).click();

  await expect.poll(() => updates).toEqual([{ app: 'outreach', body: { restricted: true, member_ids: [2] } }]);
  await expect(card.getByText('Restricted · 1 member(s)')).toBeVisible();
});
