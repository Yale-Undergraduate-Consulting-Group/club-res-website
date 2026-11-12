import { expect, test } from '@playwright/test';

/** Admin → Metrics: targets judged only with enough data, period switching, per-member rows. */

const ADMIN = { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1, apps: ['core', 'outreach', 'projects'] };

const metrics = (days: number) => ({
  days, min_sample: 5,
  targets: [
    { key: 'ready_to_send', label: 'Members ready to send (Gmail connected)', count: 9, of: 10, value: 90, target: '≥ 90%', met: true },
    { key: 'bounce_rate', label: 'Bounce rate', count: 3, of: 20, value: 15, target: '< 5%', met: false },
    { key: 'reply_rate', label: 'Reply rate', count: 1, of: 2, value: 50, target: '≥ 5%', met: null },
  ],
  waiting: { contacts: days === 30 ? 16 : 0, oldest_added_at: '2026-11-01 10:00:00' },
  reply_target: '≥ 5%',
  approaches: [
    { template_id: 3, template: 'Intro', angle: 'intro', sent: 12, replied: 3, value: 25, met: true },
    { template_id: 4, template: 'Project pitch', angle: 'project pitch', sent: 8, replied: 0, value: 0, met: false },
    { template_id: null, template: null, angle: 'social proof', sent: 2, replied: 1, value: 50, met: null },
  ],
  members: [{ user_id: 2, name: 'Ana', searches: days, drafts: 4, sent: 3, replies: 1, bounces: 0, ready_to_send: true }],
  not_measured: ['Positive replies: replies are not classified yet.'],
});

test('admins see targets judged against enough data, and switch the period', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'an admin reporting surface');
  const asked: string[] = [];
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user: ADMIN } });
    if (url.pathname === '/api/analytics/club-metrics') {
      asked.push(url.searchParams.get('days') ?? '');
      return route.fulfill({ json: metrics(Number(url.searchParams.get('days'))) });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto('/admin?view=metrics');
  await expect(page.getByTestId('target-ready_to_send')).toContainText('On target');
  await expect(page.getByTestId('target-bounce_rate')).toContainText('Below target');
  await expect(page.getByTestId('target-reply_rate')).toContainText('Not enough data (2 of 5)');
  await expect(page.getByText('16 contacts are waiting in draft or paused campaigns')).toBeVisible();
  await expect(page.getByTestId('member-metrics-2')).toContainText('Ana');

  // What earns replies: judged per template and approach, never on a handful of cases.
  const approaches = page.getByTestId('approach-metrics');
  await expect(approaches.getByRole('row', { name: /Intro/ })).toContainText('On target');
  await expect(approaches.getByRole('row', { name: /Project pitch/ })).toContainText('Below target');
  await expect(approaches.getByRole('row', { name: /social proof/ })).toContainText('Written by hand');
  await expect(approaches.getByRole('row', { name: /social proof/ })).toContainText('Not enough data (2 of 5)');

  await page.getByRole('button', { name: 'Last 7 days' }).click();
  await expect(page.getByRole('heading', { name: 'By member, last 7 days' })).toBeVisible();
  await expect(page.getByText(/contacts are waiting/)).toHaveCount(0);
  expect([...new Set(asked)]).toEqual(['30', '7']);
});

test('a failed load says so instead of showing an empty table', async ({ page }, info) => {
  test.skip(info.project.name !== 'desktop', 'an admin reporting surface');
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user: ADMIN } });
    if (path === '/api/analytics/club-metrics') return route.fulfill({ status: 500, json: { detail: 'Database is busy' } });
    return route.fulfill({ json: [] });
  });
  await page.goto('/admin?view=metrics');
  await expect(page.getByText('Could not load metrics: Database is busy')).toBeVisible();
});
