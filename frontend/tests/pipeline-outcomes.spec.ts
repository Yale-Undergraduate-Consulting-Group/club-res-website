import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

const CONTACTS = [
  { id: 1, name: 'Ada Lovelace', email: 'ada@acme.com', company: 'Acme Corp', pipeline_status: 'cold' },
  { id: 2, name: 'Grace Hopper', email: 'grace@acme.com', company: 'Acme Corp', pipeline_status: 'cold' },
  { id: 3, name: 'Alan Turing', email: 'alan@quiet.io', company: 'Quiet Inc', pipeline_status: 'cold' },
];

const SUMMARY = [
  // Mailed, one answer, and one bounce: the bounce matters most because every
  // other address at this company came from the same format.
  { company: 'Acme Corp', company_domain: 'acme.com', contact_count: 2, mailed_count: 2, replied_count: 1, bounced_count: 1, queued_count: 0 },
  { company: 'Quiet Inc', company_domain: 'quiet.io', contact_count: 1, mailed_count: 0, replied_count: 0, bounced_count: 0, queued_count: 0 },
];

async function mockPipeline(page: Page) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/contacts') body = { items: CONTACTS, total: CONTACTS.length, limit: 100, offset: 0 };
    else if (path === '/api/contacts/companies/summary') body = SUMMARY;
    else if (path === '/api/outreach/metrics/pipeline') body = { by_status: [] };
    else if (path === '/api/outreach/templates') body = [];
    else if (path === '/api/outreach/sequences') body = [];
    else if (path === '/api/outreach/campaigns') body = [];
    else if (path === '/api/outreach/worklists') body = [];
    else if (path === '/api/ai/models') body = { groups: [] };
    await route.fulfill({ json: body });
  });
}

test('grouping the pipeline by company reports what the send ledger says happened', async ({ page }) => {
  await mockPipeline(page);
  await page.goto('/outreach');
  await page.getByText('Group by company').click();

  const acme = page.getByRole('button').filter({ hasText: 'Acme Corp' }).first();
  await expect(acme).toContainText('2 mailed');
  await expect(acme).toContainText('1 replied');
  await expect(acme).toContainText('1 bounced');

  // A company nobody has written to says so, rather than showing a blank line
  // that reads like a zero-reply result.
  const quiet = page.getByRole('button').filter({ hasText: 'Quiet Inc' }).first();
  await expect(quiet).toContainText('not contacted yet');
});

test('ticking contacts across companies hands exactly those people to Drafts', async ({ page }) => {
  await mockPipeline(page);
  await page.goto('/outreach');

  // Ada (Acme Corp) and Alan (Quiet Inc): the people themselves go, not just
  // their companies, so Drafts writes to each of them and nobody else there.
  await page.getByLabel('Select Ada Lovelace').check();
  await page.getByLabel('Select Alan Turing').check();
  await expect(page.getByText('2 selected')).toBeVisible();

  await page.getByRole('button', { name: /Write to each of these 2/ }).click();
  await expect(page).toHaveURL(/\/studio\?companies=/);
  await expect(page).toHaveURL(/Acme(%20|\+)Corp/);
  await expect(page).toHaveURL(/Quiet(%20|\+)Inc/);
  await expect(page).toHaveURL(/contact_ids=\d+%2C\d+/);
});
