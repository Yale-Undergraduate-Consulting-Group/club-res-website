import { expect, test, type Page } from '@playwright/test';
import { REMINDERS } from './support/clubMock';

/** Admin → Members → Send setup reminders: preview, untick, per-member results, and the states where it cannot send. */

const ADMIN = { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1, apps: ['core', 'outreach', 'projects'] };

type Reply = { status?: number; json: unknown };

async function mock(page: Page, preview: Reply, send: (ids: number[]) => Reply) {
  const posted: number[][] = [];
  await page.route('**/api/**', (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user: ADMIN } });
    if (path === '/api/admin/users') return route.fulfill({ json: [ADMIN] });
    if (path === '/api/admin/setup-reminders' && request.method() === 'GET') return route.fulfill(preview);
    if (path === '/api/admin/setup-reminders') {
      const body: { user_ids: number[] } = request.postDataJSON();
      posted.push(body.user_ids);
      return route.fulfill(send(body.user_ids));
    }
    return route.fulfill({ json: [] });
  });
  return posted;
}

test('the preview lists exactly what each member is missing; unticked members are not messaged; each result is shown', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'an admin surface');
  const posted = await mock(page, { json: REMINDERS }, ([id]) => ({
    json: {
      results: [id === 2 ? { user_id: 2, status: 'sent', error: null, items: [] }
        : { user_id: id, status: 'not_found', error: null, items: [] }],
    },
  }));
  await page.goto('/admin');
  await page.getByRole('button', { name: 'Send setup reminders' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByTestId('reminder-2')).toContainText('Missing: Gmail, Signature role, LinkedIn, Phone, Sign-off name, Slack');
  await expect(dialog.getByTestId('reminder-3')).toContainText('Missing: Signature role, Phone');
  // Reminded this week: shown with its date and unticked.
  await expect(dialog.getByTestId('reminder-4')).toContainText('reminded 2026-11-15');
  await expect(dialog.getByRole('checkbox', { name: 'Remind cal@yale.edu' })).not.toBeChecked();
  await expect(dialog.getByRole('button', { name: 'Send to 2 members' })).toBeVisible();

  // Untick one, tick the reminded one to resend.
  await dialog.getByRole('checkbox', { name: /Remind Bartholomew/ }).uncheck();
  await dialog.getByRole('checkbox', { name: 'Remind cal@yale.edu' }).check();
  await dialog.getByRole('button', { name: 'Send to 2 members' }).click();

  await expect(dialog.getByTestId('reminder-2')).toContainText('Sent');
  await expect(dialog.getByTestId('reminder-4')).toContainText('Not found in Slack');
  await expect(dialog.getByTestId('reminder-3')).not.toContainText('Sent');
  await expect(dialog.getByText('1 sent, 1 not found in Slack, 0 failed.')).toBeVisible();
  expect(posted, 'one request per member, and only the ticked ones').toEqual([[2], [4]]);
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('a Slack failure shows its code, never a success', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'an admin surface');
  await mock(page, { json: REMINDERS }, ([id]) => ({
    json: { results: [{ user_id: id, status: 'failed', error: 'account_inactive', items: [] }] },
  }));
  await page.goto('/admin');
  await page.getByRole('button', { name: 'Send setup reminders' }).click();
  await page.getByRole('button', { name: 'Send to 2 members' }).click();
  await expect(page.getByTestId('reminder-2')).toContainText('Failed (account_inactive)');
  await expect(page.getByText('0 sent, 0 not found in Slack, 2 failed.')).toBeVisible();
});

test('when the server refuses, sending stops with its message', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'an admin surface');
  const posted = await mock(page, { json: REMINDERS }, () => ({
    status: 409, json: { detail: 'Slack is not connected: the club bot has no usable token.' },
  }));
  await page.goto('/admin');
  await page.getByRole('button', { name: 'Send setup reminders' }).click();
  await page.getByRole('button', { name: 'Send to 2 members' }).click();
  await expect(page.getByRole('alert')).toContainText('Slack is not connected');
  expect(posted).toHaveLength(1);
  await expect(page.getByTestId('reminder-2')).not.toContainText('Sent');
});

test('without a Slack token the button says so and cannot be used', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'an admin surface');
  await mock(page, { json: { ...REMINDERS, slack_connected: false } }, () => ({ json: { results: [] } }));
  await page.goto('/admin');
  await expect(page.getByRole('button', { name: 'Slack not connected' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Send setup reminders' })).toHaveCount(0);
});

test('a complete club and a failed load are both said plainly', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'an admin surface');
  await mock(page, { json: { slack_connected: true, members: [] } }, () => ({ json: { results: [] } }));
  await page.goto('/admin');
  await page.getByRole('button', { name: 'Send setup reminders' }).click();
  await expect(page.getByText('Everyone has finished setting up.')).toBeVisible();
  await expect(page.getByRole('button', { name: /Send to 0 members/ })).toBeDisabled();

  await page.unroute('**/api/**');
  await mock(page, { status: 500, json: { detail: 'Database is busy' } }, () => ({ json: { results: [] } }));
  await page.reload();
  await page.getByRole('button', { name: 'Send setup reminders' }).click();
  await expect(page.getByRole('alert')).toContainText('Database is busy');
  await expect(page.getByRole('button', { name: /Send to 0 members/ })).toBeDisabled();
});
