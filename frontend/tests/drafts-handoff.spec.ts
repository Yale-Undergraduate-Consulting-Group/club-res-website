import { expect, test, type Page } from '@playwright/test';

/**
 * Drafts follows the one-template rule: no AI assistance, no AI email per
 * person. A contact opens written from the club template, merged for them by
 * the server, with their company's line to confirm; the member can still edit
 * by hand. People handed over from Find or the sidebar are listed to open.
 */

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

const PEOPLE = [
  { id: 1, name: 'Jordan Rivers', email: 'jordan@acme.com', title: 'VP Marketing', company: 'Acme Corp' },
  { id: 2, name: 'Sam Lee', email: 'sam@acme.com', title: 'Head of Strategy', company: 'Acme Corp' },
  { id: 3, name: 'Not Chosen', email: 'nc@acme.com', title: 'Analyst', company: 'Acme Corp' },
];

const CLUB_INTRO = {
  id: 11, name: 'Intro', subject: 'Introduction from the Yale Undergraduate Consulting Group',
  body: "Hi {first},\n\nI'd love to work with {company} on a project in the new year. {personalisation}\n\nBest,\n{sender_first_name}",
  use_case: 'intro', industry: null, scope: 'club', mine: false, editable: false,
};

async function mockDrafts(page: Page) {
  const builds: Record<string, unknown>[] = [];
  const suggested: string[] = [];
  const saved: Record<string, unknown>[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/contacts') body = { items: PEOPLE, total: PEOPLE.length, limit: 800, offset: 0 };
    else if (path === '/api/settings') body = {};
    else if (path === '/api/outreach/templates') body = [CLUB_INTRO];
    else if (path === '/api/campaigns/build') {
      // The server's merge, as /build previews it for one contact.
      const sent = request.postDataJSON();
      builds.push(sent);
      const person = PEOPLE.find((p) => p.id === sent.contact_ids[0])!;
      const line = (sent.personalisation || {})[person.company] || '';
      body = {
        recipients: 1, ready: 1, held: [], skipped: 0, companies: [person.company],
        sample: {
          email: person.email, subject: CLUB_INTRO.subject,
          body: `Hi ${person.name.split(' ')[0]},\n\nI'd love to work with ${person.company} on a project in the new year. ${line}`.trimEnd() + '\n\nBest,\nAlice',
        },
      };
    } else if (path === '/api/campaigns/personalisation') {
      const { company } = request.postDataJSON();
      suggested.push(company);
      body = { company, line: `Your new stores caught our eye at ${company}.`, sources: [] };
    } else if (path === '/api/emails/generated' && request.method() === 'POST') {
      saved.push(request.postDataJSON());
      body = { id: 5, ...request.postDataJSON() };
    } else if (/\/companies\/summary$/.test(path)) body = [];
    else if (/\/releases$|\/templates$|\/sequences$|\/generated$|\/my-projects$|\/attachments$/.test(path)) body = [];
    await route.fulfill({ json: body });
  });
  return { builds, suggested, saved };
}

test('a person handed to Drafts opens written from the club template, with their company line', async ({ page }) => {
  const { builds, suggested, saved } = await mockDrafts(page);
  await page.goto('/studio?companies=Acme%20Corp&contact_ids=1%2C2');

  // No AI anywhere on the page: no assistance panel, no model picker, no per-person drafting.
  await expect(page.getByText(/AI assistance|Generate email|advisory/i)).toHaveCount(0);
  await expect(page.getByPlaceholder(/use AI assistance/)).toHaveCount(0);

  const panel = page.getByTestId('handoff-people');
  await expect(panel.getByRole('heading', { name: '2 people to write to with the club template' })).toBeVisible();
  await panel.getByRole('button', { name: /Show the 2 people/ }).click();
  // Only the people chosen, not everyone on file at the company.
  await expect(panel.getByText('Not Chosen')).toHaveCount(0);
  await panel.getByRole('button', { name: /^Jordan Rivers/ }).click();

  const message = page.getByRole('textbox', { name: 'Message', exact: true });
  await expect(page.getByLabel('Subject', { exact: true })).toHaveValue(CLUB_INTRO.subject);
  await expect(message).toContainText('Hi Jordan,');
  await expect(message).toContainText('Best,Alice');
  expect(builds[0]).toMatchObject({ contact_ids: [1], template_id: 11, preview_only: true });

  // The same per-company line step as the Send panel: suggest, then confirm.
  const lines = page.getByTestId('company-lines');
  await lines.getByRole('button', { name: 'Suggest', exact: true }).click();
  await expect(lines.getByRole('textbox', { name: 'Line for Acme Corp' })).toHaveValue('Your new stores caught our eye at Acme Corp.');
  await lines.getByRole('checkbox', { name: 'Confirm the line for Acme Corp' }).check();
  await expect(message).toContainText('on a project in the new year. Your new stores caught our eye at Acme Corp.');
  expect(suggested).toEqual(['Acme Corp']);

  // Edited by hand, the member's text stands and the line step steps aside.
  await message.click();
  await page.keyboard.press('End');
  await page.keyboard.type(' P.S. Hello.');
  await expect(lines).toHaveCount(0);
  await page.getByRole('button', { name: 'Save draft', exact: true }).click();
  await expect.poll(() => saved.length).toBe(1);
  expect(String(saved[0].body)).toContain('P.S. Hello.');
});

test('ticking people in the Drafts sidebar lists them the same way', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the sidebar is a desktop column');
  await mockDrafts(page);
  await page.goto('/studio');

  await page.getByLabel('Select Jordan Rivers').check();
  await page.getByLabel('Select Sam Lee').check();
  await page.getByRole('button', { name: 'Write to each of these 2 →' }).click();

  const panel = page.getByTestId('handoff-people');
  await expect(panel.getByRole('heading', { name: '2 people to write to with the club template' })).toBeVisible();
  await expect(page).toHaveURL(/\/studio\?companies=Acme(%20|\+)Corp&contact_ids=1%2C2/);
});
