import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

const SCHEDULE = {
  scheduled: [
    {
      campaign_contact_id: 1, contact_name: 'Ada Lovelace', email: 'ada@acme.com', company: 'Acme Corp',
      campaign_id: 1, campaign_name: 'Acme first touch', campaign_status: 'sent',
      sequence_name: 'Polite two-step', next_subject: 'Following up on my note',
      due_on: '2026-09-18', overdue: true,
    },
    {
      campaign_contact_id: 2, contact_name: 'Grace Hopper', email: 'grace@acme.com', company: 'Acme Corp',
      campaign_id: 1, campaign_name: 'Acme first touch', campaign_status: 'sent',
      sequence_name: 'Polite two-step', next_subject: 'Following up on my note',
      due_on: '2026-09-27', overdue: false,
    },
  ],
  stopped: [
    { campaign_contact_id: 3, contact_name: 'Alan Turing', email: 'alan@acme.com', company: 'Acme Corp',
      campaign_id: 1, reason: 'They replied' },
    { campaign_contact_id: 4, contact_name: 'Jean Bartik', email: 'jean@quiet.io', company: 'Quiet Inc',
      campaign_id: 2, reason: 'The campaign is needs_attention, so follow-ups are paused' },
  ],
};

async function mockPipeline(page: Page) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/outreach/follow-ups/schedule') body = SCHEDULE;
    else if (path === '/api/contacts') body = { items: [], total: 0, limit: 100, offset: 0 };
    else if (path === '/api/contacts/companies/summary') body = [];
    else if (path === '/api/outreach/metrics/pipeline') body = { by_status: [] };
    else if (path === '/api/outreach/sequences') body = [{ id: 1, name: 'Polite two-step', steps: [{ days_after: 3 }, { days_after: 7 }] }];
    else if (path === '/api/outreach/templates') body = [];
    else if (path === '/api/outreach/campaigns') body = [];
    else if (path === '/api/outreach/worklists') body = [];
    else if (path === '/api/ai/models') body = { groups: [] };
    await route.fulfill({ json: body });
  });
}

test('follow-ups are a destination showing what is queued and what quietly stopped', async ({ page }) => {
  await mockPipeline(page);
  await page.goto('/outreach');

  await page.getByRole('tab', { name: 'Follow-ups' }).click();
  await expect(page.getByText('2 queued · 1 due now · 2 stopped')).toBeVisible();

  // What goes out next, and to whom.
  const due = page.getByRole('listitem').filter({ hasText: 'Ada Lovelace' });
  await expect(due).toContainText('Following up on my note');
  await expect(due).toContainText('due now');
  await expect(page.getByRole('listitem').filter({ hasText: 'Grace Hopper' })).toContainText('due 2026-09-27');

  // A reply is the good stop. A paused campaign is the one worth seeing: it
  // stops follow-ups and says so nowhere else in the app.
  await expect(page.getByText('They replied')).toBeVisible();
  await expect(page.getByText(/campaign is needs_attention, so follow-ups are paused/)).toBeVisible();

  // The sequence builder moved here too, so defining steps and seeing their
  // effect are one screen rather than a form at the bottom of another tab.
  await expect(page.getByRole('heading', { name: 'Sequences (follow-ups)' })).toBeVisible();
  await expect(page.getByPlaceholder('Sequence name')).toBeVisible();

  // And it is gone from where it used to be buried.
  await page.getByRole('tab', { name: 'Email resources' }).click();
  await expect(page.getByRole('heading', { name: 'Sequences (follow-ups)' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Templates' })).toBeVisible();
});
