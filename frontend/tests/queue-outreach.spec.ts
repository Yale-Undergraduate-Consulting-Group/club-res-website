import { expect, test, type Page } from '@playwright/test';

/**
 * One panel from a list of people to a released campaign: choose a template,
 * read who is ready and who is held (and why), then create and release. The
 * server does the resolving and the rendering; these specs defend what the
 * member is shown and what is sent to it.
 */

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

const TEMPLATES = [
  { id: 11, name: 'Intro', subject: 'Hello {first}', body: 'Hi {first}', use_case: 'intro', industry: null, scope: 'club', mine: false, editable: false },
  { id: 12, name: 'My pitch', subject: 'For {company}', body: 'Hi {first}', use_case: 'project pitch', industry: null, scope: 'mine', mine: true, editable: true },
];

const PEOPLE = [
  { id: 1, name: 'Jordan Rivers', email: 'jordan@acme.com', title: 'VP Marketing', company: 'Acme Corp' },
  { id: 2, name: 'Sam Lee', email: 'sam@acme.com', title: 'Head of Strategy', company: 'Acme Corp' },
];

const dirPerson = (name: string, email: string, extra: Record<string, unknown> = {}) => ({
  name, email, title: 'Head of Finance', company: 'Acme', source: 'Company website', on_file: true,
  proof: 'mailbox_confirmed', reach: { state: 'not_contacted', by: null, by_me: false, at: null, sends: 0, claimed_by: null },
  ready: true, contact_id: null, run_id: null, prospect_id: null, ...extra,
});

const DIRECTORY = [
  dirPerson('Barbara Liskov', 'barbara@clean.com', { contact_id: 21 }),
  dirPerson('Edsger Dijkstra', 'edsger@clean.com', { contact_id: 22 }),
  // Found by a search, not yet in the member's contacts: ticking it imports it from its run when queued.
  dirPerson('Margaret Hamilton', 'margaret@neon.com', { on_file: false, run_id: 9, prospect_id: 90 }),
  // Found by a search but not ready: cannot be ticked.
  dirPerson('Grace Hopper', 'grace@neon.com', { on_file: false, run_id: 9, prospect_id: 91, ready: false }),
  // Someone already wrote to her: not ready, so not selectable.
  dirPerson('Ada Lovelace', 'ada@acme.com', { contact_id: 23, ready: false, reach: { state: 'contacted', by: 'Ana', by_me: false, at: null, sends: 1, claimed_by: null } }),
];

async function mockServer(page: Page, opts: { aiDown?: boolean } = {}) {
  const builds: Record<string, unknown>[] = [];
  const imports: Record<string, unknown>[] = [];
  const releases: string[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown = {};
    let status = 200;
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/outreach/templates') body = TEMPLATES;
    else if (path === '/api/outreach/templates/preview') {
      const draft = request.postDataJSON();
      body = {
        fields: [{ name: 'first', help: 'their first name' }, { name: 'company', help: 'their employer' }],
        recipient: { name: 'Alex Rivera', title: 'Head of Partnerships', company: 'Northwind Health', email: 'alex@example.com' },
        subject: draft.subject.replace('{first}', 'Alex'), body: draft.body.replace('{first}', 'Alex'),
        error: /\{firstname\}/.test(draft.subject + draft.body) ? '{firstname} is not a field. Use {first}.' : null,
      };
    } else if (path === '/api/outreach/campaigns') body = [{ id: 7, name: 'Spring targets', type: 'individual', contact_count: 3 }];
    else if (path === '/api/outreach/campaigns/7') body = { id: 7, name: 'Spring targets', type: 'individual', contacts: PEOPLE };
    else if (path === '/api/yucgoutreach/runs/9/import-contacts') {
      imports.push(request.postDataJSON());
      body = { created: 1, updated: 0, skipped: 0, results: [{ prospect_id: 90, contact_id: 31, outcome: 'created' }] };
    }
    else if (path === '/api/campaigns/build') {
      const sent = request.postDataJSON();
      builds.push(sent);
      const preview = {
        recipients: 3, ready: 2, skipped: 1,
        held: [{ contact_id: 3, email: 'info@acme.com', name: '', reason: 'info@acme.com has no first on record, and the message uses it.' }],
        sample: { email: 'jordan@acme.com', subject: 'Hello Jordan', body: 'Hi Jordan' },
      };
      body = sent.preview_only ? preview : { campaign_id: 42, created: 2, held: preview.held, name: sent.name };
    } else if (path === '/api/campaigns/42/release') {
      releases.push(path);
      body = { ok: true, status: 'releasing', counts: { pending: 2 }, allowance: { daily_limit: 100, queued: 2, days_to_drain: 1, warn_at: null } };
    } else if (path === '/api/contacts') body = { items: PEOPLE, total: 2, limit: 800, offset: 0 };
    else if (path === '/api/directory/summary') {
      body = {
        total: 4, capped: false, ready: 3,
        proof: { replied: 0, delivered: 0, bounced: 0, mailbox_confirmed: 4, catch_all: 0, mailbox_rejected: 0, unverified: 0 },
        reach: { replied: 0, bounced: 0, contacted: 1, queued: 0, not_contacted: 3 },
        backlog: { enabled: false, credits_used_today: 0, credits_cap_today: 0, verified_total: 0, queue_remaining: 0 },
      };
    } else if (path === '/api/directory') {
      const ready = new URL(request.url()).searchParams.get('ready');
      const items = DIRECTORY.filter((p) => !ready || p.ready);
      body = { items, total: items.length, limit: 50, offset: 0, capped: false };
    } else if (path === '/api/emails/generate' && opts.aiDown) {
      status = 503;
      body = { detail: 'The language model is unavailable right now.' };
    } else if (path === '/api/projects/suggest-citations') body = { projects: [], team_experience: [] };
    else if (path === '/api/outreach/metrics/pipeline') body = { by_status: [] };
    else if (/\/companies\/summary$|\/sequences$|\/generated$|\/releases$|\/my-projects$|\/attachments$/.test(path)) body = [];
    await route.fulfill({ status, json: body });
  });
  return { builds, releases, imports };
}

async function pickTemplateAndQueue(page: Page) {
  const panel = page.getByTestId('queue-outreach');
  await panel.getByLabel('Template').selectOption({ label: 'Intro' });
  // Who is ready and who is held, with the reason, before anything is created.
  await expect(panel.getByRole('status').filter({ hasText: '2 ready' })).toContainText('1 held');
  await expect(panel).toContainText('1 left out');
  await expect(panel).toContainText('Hello Jordan');
  await expect(panel).toContainText('info@acme.com: info@acme.com has no first on record, and the message uses it.');
  await panel.getByRole('button', { name: 'Create campaign and release 2' }).click();
  await expect(panel.getByRole('status').filter({ hasText: 'Queued 2 emails from campaign #42' })).toContainText('up to 100 a day');
  await expect(panel.getByRole('link', { name: 'Open the campaign' })).toHaveAttribute('href', '/campaigns/42');
}

test('a worklist is queued in one panel: template, preview, create and release', async ({ page }) => {
  const { builds, releases } = await mockServer(page);
  await page.goto('/outreach?view=worklists');
  await page.getByText('Spring targets').first().click();
  await page.getByRole('button', { name: 'Queue outreach' }).click();

  await pickTemplateAndQueue(page);

  // The list itself is sent for the server to resolve (and to authorise); the
  // browser never lists its people. The preview and the build ask the same thing.
  expect(builds).toEqual([
    { target_list_id: 7, template_id: 11, preview_only: true },
    { target_list_id: 7, template_id: 11, name: 'Spring targets: Intro' },
  ]);
  expect(releases).toHaveLength(1);
});

test('ticked Directory people are queued; a search result is added to contacts first, and only ready people can be ticked', async ({ page }) => {
  const { builds, imports } = await mockServer(page);
  await page.goto('/directory');
  await page.getByRole('button', { name: /Ready to contact/ }).click();

  const people = page.getByRole('region', { name: 'People' });
  await expect(people.getByRole('row', { name: /Ada Lovelace/ })).toHaveCount(0);
  await expect(people.getByRole('row', { name: /Grace Hopper/ })).toHaveCount(0);

  await page.getByLabel('Select everyone on this page who can be queued').check();
  await expect(page.getByText('3 selected')).toBeVisible();
  await page.getByLabel('Select Edsger Dijkstra').uncheck();
  await expect(page.getByText('2 selected')).toBeVisible();
  await page.getByLabel('Select Edsger Dijkstra').check();
  await page.getByRole('button', { name: 'Queue outreach' }).click();

  await pickTemplateAndQueue(page);
  // The search result is imported from its own run by prospect id, then queued with the others.
  expect(imports).toEqual([{ prospect_ids: [90] }]);
  expect(builds.at(-1)).toEqual({ contact_ids: [21, 22, 31], template_id: 11, name: 'Directory selection: Intro' });
});

test('when the AI model is unavailable the member is told, and can use a template instead', async ({ page }) => {
  const { builds } = await mockServer(page, { aiDown: true });
  await page.goto('/studio?companies=Acme%20Corp&contact_ids=1%2C2');

  const batch = page.getByTestId('advisory-batch');
  await batch.getByRole('button', { name: /Draft an advisory email for each/ }).click();
  await expect(batch).toContainText('AI drafting is unavailable right now — use a template.');
  // Not the provider's error, and not a row of failed drafts.
  await expect(batch).not.toContainText('language model');
  await expect(batch).not.toContainText('failed');

  await batch.getByRole('button', { name: 'Use a template' }).click();
  await pickTemplateAndQueue(page);
  expect(builds.at(-1)).toMatchObject({ contact_ids: [1, 2], template_id: 11 });
});

test('the template editor previews the merge and flags a field that does not exist', async ({ page }) => {
  await mockServer(page);
  await page.goto('/outreach?view=resources');

  await page.getByPlaceholder('Subject').fill('Hello {first}');
  await page.getByPlaceholder('Body').fill('Hi {first}');
  const preview = page.getByTestId('template-preview');
  await expect(preview).toContainText('{first}');
  await expect(preview).toContainText('Preview for Alex Rivera, Head of Partnerships at Northwind Health');
  await expect(preview).toContainText('Hello Alex');

  await page.getByPlaceholder('Body').fill('Hi {firstname}');
  await expect(preview).toContainText('{firstname} is not a field');

  // A club template cannot be deleted by a member; their own private one can.
  await expect(page.getByText('Intro').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Delete' })).toHaveCount(1);
});
