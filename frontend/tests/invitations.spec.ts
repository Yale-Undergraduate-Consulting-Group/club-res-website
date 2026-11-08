import { expect, test, type Page } from '@playwright/test';

const ADMIN = { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1, apps: ['core', 'outreach', 'projects'] };
type Invitation = { id: number; email: string; state: string; delivery_state: string; expires_at: number; delivery_error?: string };

async function mock(page: Page, initial: Invitation[]) {
  let items = initial;
  let rotations = 0;
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user: ADMIN } });
    if (path === '/api/admin/invitations' && request.method() === 'GET') return route.fulfill({ json: items });
    if (path === '/api/admin/invitations' && request.method() === 'POST') {
      items = [{ id: 13, email: request.postDataJSON().email, state: 'pending', delivery_state: 'ready', expires_at: 2_000_000_000 }];
      return route.fulfill({ json: { id: 13, token: 'new-invitation-token' } });
    }
    if (path === '/api/admin/invitations/12/link' && request.method() === 'POST') {
      rotations += 1;
      items = [{ ...items[0], delivery_state: 'manual', delivery_error: 'The emailed link was replaced. Share the new link directly.' }];
      return route.fulfill({ json: { link: 'https://site.example/api/auth/google?invitation=replacement-token', expires_at: items[0].expires_at } });
    }
    return route.fulfill({ json: [] });
  });
  return { rotations: () => rotations };
}

test('a missing invitation email can be replaced with a link to send directly', async ({ page }) => {
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  const state = await mock(page, [{ id: 12, email: 'friend@yale.edu', state: 'pending', delivery_state: 'sent', expires_at: 2_000_000_000 }]);
  await page.goto('/admin?view=members');

  await page.getByRole('button', { name: 'Replace with shareable link' }).click();
  const dialog = page.getByRole('dialog', { name: 'Replace emailed invitation link?' });
  await expect(page.getByText('Accepted by Gmail')).toBeVisible();
  await expect(dialog).toContainText('invalidates the link in the email');
  await dialog.getByRole('button', { name: 'Replace link' }).click();

  const link = page.getByLabel('Invitation link for friend@yale.edu');
  await expect(link).toHaveValue('https://site.example/api/auth/google?invitation=replacement-token');
  await expect(page.getByText('Share the new link directly.')).toBeVisible();
  expect(state.rotations()).toBe(1);
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('https://site.example/api/auth/google?invitation=replacement-token');
});

test('a newly created invitation exposes its existing link without rotating it', async ({ page }) => {
  const state = await mock(page, []);
  await page.goto('/admin?view=members');
  await page.getByLabel('Email addresses').fill('new@yale.edu');
  await page.getByRole('button', { name: 'Create invitations' }).click();

  await expect(page.getByLabel('Invitation link for new@yale.edu')).toHaveValue(/\/api\/auth\/google\?invitation=new-invitation-token$/);
  expect(state.rotations()).toBe(0);
});
