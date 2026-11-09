import { expect, test, type Page } from '@playwright/test';

/** Onboarding at club scale: a member's Home checklist, the admin's view of
 *  who can send, and a bulk invitation that reports every address. */

const MEMBER = { id: 2, email: 'ana@yale.edu', name: 'Ana', role: 'standard', is_active: 1, apps: ['core', 'outreach', 'projects'] };
const ADMIN = { ...MEMBER, id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin' };
const DONE = { signed_in: true, gmail: true, sign_off: true, slack: true, project: true, has_sent: false, ready_to_send: true };

async function mock(page: Page, user: typeof MEMBER, routes: Record<string, unknown>) {
  const posted: Record<string, unknown>[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user } });
    if (request.method() === 'POST') posted.push({ path, body: request.postDataJSON() });
    if (path in routes) return route.fulfill({ json: routes[path] });
    return route.fulfill({ json: [] });
  });
  return posted;
}

test('Home lists only the missing setup steps and says Gmail blocks sending', async ({ page }) => {
  await mock(page, MEMBER, { '/api/auth/readiness': { ...DONE, gmail: false, slack: false, ready_to_send: false } });
  await page.goto('/');
  const card = page.getByTestId('getting-started');
  await expect(card).toContainText('Not ready to send');
  await expect(card.getByRole('link', { name: 'Connect your Gmail' })).toHaveAttribute('href', '/profile?view=integrations');
  await expect(card).toContainText('Required to send');
  await expect(card.getByRole('link', { name: 'Connect Slack' })).toBeVisible();
  await expect(card).not.toContainText('Set your email sign-off');
});

test('Home shows no checklist once setup is complete', async ({ page }) => {
  await mock(page, MEMBER, { '/api/auth/readiness': DONE });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Home' })).toBeVisible();
  await expect(page.getByTestId('getting-started')).toHaveCount(0);
});

test('admins see who can send and get a result for every invited address', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the members table is a desktop admin surface');
  const posted = await mock(page, ADMIN, {
    '/api/admin/users': [ADMIN, MEMBER],
    '/api/admin/users/readiness': [{ user_id: 1, ...DONE, signed_in: true }, { user_id: 2, ...DONE, gmail: false, slack: false, ready_to_send: false }],
    '/api/admin/invitations/bulk': {
      created: 1, skipped: 1,
      results: [{ email: 'new@yale.edu', status: 'created', id: 9, token: 'tok-new' }, { email: 'ben@yale.edu', status: 'skipped', reason: 'Already a member' }],
    },
  });
  await page.goto('/admin?view=users');
  await expect(page.getByTestId('members-ready-summary')).toHaveText('1 of 2 active members ready to send');
  await expect(page.getByTestId('setup-2')).toContainText('Cannot send');
  await expect(page.getByTestId('setup-2')).toContainText('Missing: Gmail, Slack');

  await page.getByLabel('Email addresses').fill('new@yale.edu, Ben@yale.edu\nnot-an-email');
  await page.getByRole('button', { name: 'Create 2 invitations' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Created 1' })).toContainText('ben@yale.edu: Already a member');
  expect(posted).toContainEqual({ path: '/api/admin/invitations/bulk', body: { emails: ['new@yale.edu', 'ben@yale.edu'], project_ids: [] } });
});
