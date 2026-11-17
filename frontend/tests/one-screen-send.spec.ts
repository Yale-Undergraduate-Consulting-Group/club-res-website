import { expect, test, type Page } from '@playwright/test';

/**
 * Companies to sent emails on the one Find contacts screen, with every API
 * mocked. The mock is stateful the way the server is: one search per member at
 * a time (a second is refused with 409), a finished search's people land on the
 * member's contacts only through the import call, and a build previews what it
 * would hold. What these specs defend is what the member sees and what is sent.
 */

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

const TEMPLATES = [
  { id: 12, name: 'My pitch', subject: 'For {company}', body: 'Hi {first}', use_case: 'pitch', industry: null, scope: 'mine', mine: true, editable: true },
  { id: 11, name: 'Intro', subject: 'Hello {first}', body: 'Hi {first}, we are YUCG.', use_case: 'intro', industry: null, scope: 'club', mine: false, editable: false },
];
/** The club's one template, with the line each company gets. */
const CLUB_INTRO = [
  { id: 11, name: 'Intro', subject: 'Introduction from the Yale Undergraduate Consulting Group', body: "Hi {first},\n\nI'd love to work with {company} on a project in the new year. {personalisation}\n\nBest,\n{sender_first_name}", use_case: 'intro', industry: null, scope: 'club', mine: false, editable: false },
];

type Prospect = { first: string; last: string; title: string; refused?: string };

const FIRST = ['Jean', 'Klara', 'Ada', 'Grace', 'Alan', 'Edsger'];
/** Two team leads each at every company but A24, whose list exercises the holds. */
function prospectsFor(company: string): Prospect[] {
  if (company === 'A24') {
    return [
      { first: 'Jean', last: 'Bartik', title: 'Director of Operations' },
      { first: 'Klara', last: 'Dan', title: 'VP Partnerships' },
      { first: 'Ada', last: 'Lovelace', title: 'Head of Strategy' },
      { first: 'Alan', last: 'Turing', title: 'Chief Executive Officer' },
      { first: 'Grace', last: 'Murray', title: 'Head of Insight', refused: 'already worked by Alice' },
    ];
  }
  if (company === 'BigTeam') {
    return FIRST.map((first, i) => ({ first, last: `Teammate${'ABCDEF'[i]}`, title: 'Head of Partnerships' }));
  }
  return [
    { first: 'Alex', last: `${company}Lead`, title: 'Head of Partnerships' },
    { first: 'Sam', last: `${company}Ops`, title: 'Director of Operations' },
  ];
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '');

async function mock(page: Page, opts: { heldName?: string; clubIntro?: boolean; warnTeams?: boolean } = {}) {
  const runsCreated: string[] = [];
  const builds: Record<string, unknown>[] = [];
  const releases: string[] = [];
  const suggested: { company: string; previous: string }[] = [];
  const log = { runConflicts: 0 };
  const runs: { id: number; company: string; polled: boolean }[] = [];
  const contacts: Record<string, unknown>[] = [];
  let nextContact = 1000;

  await page.addInitScript(() => {
    const w = window as unknown as { __clicks: number };
    w.__clicks = 0;
    document.addEventListener('click', (event) => { if (event.isTrusted) w.__clicks += 1; }, true);
  });

  await page.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const p = url.pathname;
    const method = request.method();
    let body: unknown = {};
    let status = 200;
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/outreach/templates') body = opts.clubIntro ? CLUB_INTRO : TEMPLATES;
    else if (p === '/api/yucgoutreach/resolve-company') {
      const q = url.searchParams.get('q') || '';
      body = { name: q, domain: null, domain_verified: false, linkedin_url: null, source: 'typed', alternatives: [] };
    } else if (p === '/api/yucgoutreach/runs' && method === 'POST') {
      // One search per member at a time.
      if (runs.some((r) => !r.polled)) {
        log.runConflicts += 1;
        status = 409;
        body = { detail: 'You already have a company search queued or running' };
      } else {
        const payload = request.postDataJSON();
        runsCreated.push(payload.company_name);
        const id = runs.length + 1;
        runs.push({ id, company: payload.company_name, polled: false });
        body = { id, status: 'queued' };
      }
    } else if (p === '/api/yucgoutreach/runs' && method === 'GET') {
      body = runs.map((r) => (r.polled
        ? { id: r.id, company_name: r.company, status: 'completed', progress_pct: 100, progress_message: 'Done' }
        : { id: r.id, company_name: r.company, status: 'running', progress_pct: 10, progress_message: 'Searching…' }));
    } else if (/^\/api\/yucgoutreach\/runs\/\d+$/.test(p)) {
      const run = runs.find((r) => r.id === Number(p.split('/').pop()))!;
      run.polled = true;
      body = { id: run.id, company_name: run.company, status: 'completed', progress_pct: 100, progress_message: 'Done' };
    } else if (/^\/api\/yucgoutreach\/runs\/\d+\/prospects$/.test(p)) {
      const run = runs.find((r) => r.id === Number(p.split('/')[4]))!;
      body = prospectsFor(run.company).map((x, i) => ({
        id: run.id * 100 + i, run_id: run.id, first_name: x.first, last_name: x.last,
        email: `${slug(x.first)}.${slug(x.last)}@${slug(run.company)}.com`, title: x.title, company: run.company,
        person_level: /chief/i.test(x.title) ? 'executive' : 'working', score: 90 - i,
      }));
    } else if (/^\/api\/yucgoutreach\/runs\/\d+\/import-contacts$/.test(p)) {
      const run = runs.find((r) => r.id === Number(p.split('/')[4]))!;
      const ids: number[] = request.postDataJSON().prospect_ids;
      const all = prospectsFor(run.company);
      const results = ids.map((id) => {
        const x = all[id - run.id * 100];
        if (x.refused) return { prospect_id: id, contact_id: null, outcome: 'skipped', reason: x.refused };
        const contactId = nextContact++;
        contacts.push({
          id: contactId, name: `${x.first} ${x.last}`, email: `${slug(x.first)}.${slug(x.last)}@${slug(run.company)}.com`,
          title: x.title, company: run.company, contact_source: 'web_discovery', person_level: /chief/i.test(x.title) ? 'executive' : 'working',
        });
        return { prospect_id: id, contact_id: contactId, outcome: 'created' };
      });
      body = { created: results.filter((r) => r.outcome === 'created').length, updated: 0, skipped: results.filter((r) => r.outcome === 'skipped').length, results };
    } else if (p === '/api/contacts') {
      const wanted = new Set((url.searchParams.get('companies') || '').toLowerCase().split(','));
      const items = contacts.filter((c) => wanted.has(String(c.company).toLowerCase()));
      body = { items, total: items.length, limit: 2000, offset: 0 };
    } else if (p === '/api/campaigns/build') {
      const payload = request.postDataJSON();
      const ids: number[] = payload.contact_ids;
      const heldContact = contacts.find((c) => c.name === (opts.heldName ?? 'Klara Dan'));
      const held = heldContact && ids.includes(heldContact.id as number)
        ? [{ contact_id: heldContact.id, email: heldContact.email, name: heldContact.name, reason: `${heldContact.email} has no company on record, and the message uses it.` }]
        : [];
      const ready = ids.length - held.length;
      const first = contacts.find((c) => ids.includes(c.id as number) && !held.some((h) => h.contact_id === c.id)) as { name: string; email: string } | undefined;
      if (payload.preview_only) {
        const company = String(contacts.find((c) => ids.includes(c.id as number))?.company ?? '');
        // The server ranks a team most senior first; here, the last found is the most senior.
        const ranked = [...ids].sort((a, b) => b - a);
        body = {
          recipients: ids.length, ready, held, skipped: 0,
          sample: first ? { email: first.email, subject: `Hello ${first.name.split(' ')[0]}`, body: `Hi ${first.name.split(' ')[0]}, we are YUCG.` } : null,
          warnings: opts.warnTeams && ids.length >= 3 ? [
            ...(ids.length >= 5 ? [{ kind: 'company', company, count: ids.length, threshold: 5, contact_ids: ranked }] : []),
            { kind: 'team', company, team: 'Sales / BD', count: ids.length, threshold: 3, keep: 2, contact_ids: ranked },
          ] : [],
        };
      } else {
        builds.push(payload);
        body = { campaign_id: 42, created: ready, held, name: payload.name };
      }
    } else if (p === '/api/campaigns/42/release') {
      releases.push(p);
      body = { ok: true, status: 'releasing', counts: { pending: 2 }, allowance: { daily_limit: 100, queued: 2, days_to_drain: 1, warn_at: null } };
    } else if (p === '/api/campaigns/personalisation') {
      const payload = request.postDataJSON();
      suggested.push(payload);
      body = { company: payload.company, line: `${payload.previous ? 'Another' : 'A'} line for ${payload.company}.`, sources: ["the club's register notes"] };
    } else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/outreach/flows') body = [];
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    await route.fulfill({ status, json: body });
  });
  return { runsCreated, builds, releases, suggested };
}

const clicks = (page: Page) => page.evaluate(() => (window as unknown as { __clicks: number }).__clicks);

test('one company to a sent email in five clicks: found people need no Add, and one number is everywhere', async ({ page }) => {
  const server = await mock(page);
  await page.goto('/scraper?view=company');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');

  // Click 1: into the field. Click 2: Add. Click 3: Search.
  await rail.getByLabel('Company').click();
  await rail.getByLabel('Company').fill('A24');
  await rail.getByRole('button', { name: 'Add', exact: true }).click();
  await rail.getByTestId('search-all').click();

  // Everyone found is on the one list with nobody asked to add them: the team
  // leads ticked, the CEO there but not ticked, the one the club refused
  // listed with the reason.
  const picker = pipeline.getByTestId('recipient-picker');
  await expect(picker.getByRole('checkbox', { name: 'Write to Jean Bartik' })).toBeChecked({ timeout: 20_000 });
  await expect(picker.getByRole('checkbox', { name: 'Write to Alan Turing' })).not.toBeChecked();
  await expect(picker.getByText('held: already worked by Alice')).toBeVisible();
  await expect(picker.getByText(/not on file yet/)).toHaveCount(0);
  await expect(pipeline.getByRole('button', { name: /^Add (Jean|Alan|Grace)/ })).toHaveCount(0);
  expect(server.runsCreated).toEqual(['A24']);

  // The template is already the club's Intro, and the preview is one real recipient.
  const message = rail.getByTestId('message-panel');
  await expect(message.getByLabel('Template')).toHaveValue('11');
  await expect(message.getByTestId('message-preview')).toContainText('we are YUCG');

  // Three ticked (Jean, Klara, Ada): the server holds Klara in the preview, the
  // club refused Grace, the CEO is not ticked. One number, said everywhere.
  const header = pipeline.getByTestId('selected-count');
  await expect(header).toHaveText('2 can be emailed');
  await expect(pipeline.getByTestId('send-reasons')).toHaveText('2 held · 1 not ticked');
  await expect(pipeline.getByTestId('lanes-total')).toContainText('2 can be emailed');
  await expect(pipeline.locator('[data-lane="A24"]').getByTestId('lane-state')).toHaveText('2 can be emailed');
  await expect(rail.getByTestId('send-button')).toHaveText('Send to 2');

  // Click 4: Send. The one confirm names who is held and why.
  await rail.getByTestId('send-button').click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading')).toHaveText('Send 2 emails from your Gmail?');
  await expect(dialog.getByTestId('confirm-held')).toContainText('2 held');
  await expect(dialog.getByTestId('confirm-held')).toContainText('Grace Murray: already worked by Alice');
  await expect(dialog.getByTestId('confirm-held')).toContainText('has no company on record');
  await expect(dialog.getByTestId('confirm-left-out')).toHaveText('Not included: 1 not ticked.');

  // Click 5: confirm. Built from the template with exactly the header's people.
  await dialog.getByRole('button', { name: 'Send 2' }).click();
  await expect(rail.getByTestId('sent-summary')).toContainText('Queued 2 emails');
  await expect(rail.getByRole('link', { name: 'Open campaign #42' })).toHaveAttribute('href', '/campaigns/42');
  expect(server.builds).toHaveLength(1);
  expect(server.builds[0]).toMatchObject({ template_id: 11 });
  // Build is given exactly the two the header counted - the held one is not sent.
  expect((server.builds[0].contact_ids as number[]).length).toBe(2);
  expect(server.releases).toEqual(['/api/campaigns/42/release']);

  expect(await clicks(page)).toBeLessThanOrEqual(5);

  // What was just queued is no longer something Send would send again.
  await expect(header).toHaveText('0 can be emailed');
  await expect(rail.getByTestId('send-button')).toBeDisabled();
});

test('five companies pasted as a list are searched one after another and sent in four clicks', async ({ page }) => {
  const server = await mock(page);
  await page.goto('/scraper?view=company');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');

  // Click 1: into the field; the paste adds all five. Click 2: Search.
  const field = rail.getByLabel('Company');
  await field.click();
  await field.evaluate((el, text) => {
    const data = new DataTransfer();
    data.setData('text', text);
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  }, 'Acme\nBlue Dot\nCedar\nDelta\nEcho');
  await expect(rail.getByTestId('chosen-count')).toContainText('5 companies added');

  // Nothing is searched by adding. Search queues every company, none dropped
  // for a limit, and each waits its turn without the member being told they
  // did anything wrong.
  expect(server.runsCreated).toEqual([]);
  await rail.getByTestId('search-all').click();
  await expect(pipeline.getByRole('alert')).toHaveCount(0);
  await expect(pipeline.getByTestId('selected-count')).toHaveText('10 can be emailed', { timeout: 40_000 });
  expect(server.runsCreated.sort()).toEqual(['Acme', 'Blue Dot', 'Cedar', 'Delta', 'Echo']);
  for (const company of ['Acme', 'Blue Dot', 'Cedar', 'Delta', 'Echo']) {
    await expect(pipeline.locator(`[data-lane="${company}"]`).getByTestId('lane-state')).toHaveText('2 can be emailed');
  }
  await expect(pipeline.getByTestId('lanes-total')).toContainText('5 companies · 10 can be emailed');
  await expect(pipeline.getByRole('alert')).toHaveCount(0);

  // Click 3: Send. Click 4: confirm.
  await expect(rail.getByTestId('send-button')).toHaveText('Send to 10');
  await rail.getByTestId('send-button').click();
  await expect(page.getByRole('dialog').getByRole('heading')).toHaveText('Send 10 emails from your Gmail?');
  await page.getByRole('dialog').getByRole('button', { name: 'Send 10' }).click();
  await expect(rail.getByTestId('sent-summary')).toContainText('Queued 10 emails');
  expect((server.builds[0].contact_ids as number[]).length).toBe(10);
  expect(await clicks(page)).toBeLessThanOrEqual(4);
});

test('the filters only narrow the list: the number and what is sent do not change', async ({ page }) => {
  await mock(page);
  await page.goto('/scraper?view=company&companies=A24');
  await page.getByTestId('search-all').click();
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const header = pipeline.getByTestId('selected-count');
  await expect(header).toHaveText('2 can be emailed', { timeout: 20_000 });

  await pipeline.getByLabel('Seniority').selectOption('executive');
  await expect(pipeline.getByTestId('filter-note')).toHaveText(/^Showing \d+ of \d+$/);
  await expect(pipeline.getByText('Jean Bartik')).toHaveCount(0);
  await expect(header).toHaveText('2 can be emailed');
  await expect(pipeline.getByTestId('send-button')).toHaveText('Send to 2');
});

test('each company gets its own line, and Send waits until every line is confirmed', async ({ page }) => {
  const server = await mock(page, { clubIntro: true });
  await page.goto('/scraper?view=company&companies=Acme,Cedar');
  await page.getByTestId('search-all').click();
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  await expect(pipeline.getByTestId('selected-count')).toHaveText('4 can be emailed', { timeout: 30_000 });

  // One template for everyone: no AI-written emails and no template to pick.
  await expect(page.getByText(/write with ai/i)).toHaveCount(0);
  await expect(rail.getByLabel('Template')).toHaveCount(0);

  const lines = rail.getByTestId('company-lines');
  await expect(lines.getByTestId('company-line')).toHaveCount(2);
  await expect(rail.getByTestId('send-button')).toBeDisabled();
  await expect(rail.getByTestId('lines-pending')).toHaveText("Confirm each company's line to send: 0 of 2 confirmed.");

  // Writing a line yourself asks the model nothing.
  await lines.getByRole('textbox', { name: 'Line for Cedar' }).fill('Your new stores are a great fit for a student team.');
  await lines.getByRole('checkbox', { name: 'Confirm the line for Cedar' }).check();
  await lines.getByTestId('suggest-lines').click();
  await expect(lines.getByRole('textbox', { name: 'Line for Acme' })).toHaveValue('A line for Acme.');
  expect(server.suggested).toEqual([{ company: 'Acme', previous: '' }]);
  await expect(lines.locator('[data-company="Acme"]')).toContainText("From the club's register notes.");

  // Regenerate asks for a different line; a new line is unconfirmed until confirmed again.
  await lines.locator('[data-company="Acme"]').getByRole('button', { name: 'Regenerate' }).click();
  await expect(lines.getByRole('textbox', { name: 'Line for Acme' })).toHaveValue('Another line for Acme.');
  expect(server.suggested.at(-1)).toEqual({ company: 'Acme', previous: 'A line for Acme.' });
  await expect(rail.getByTestId('send-button')).toBeDisabled();
  await lines.getByRole('checkbox', { name: 'Confirm the line for Acme' }).check();
  // Editing a confirmed line takes the confirmation back.
  await lines.getByRole('textbox', { name: 'Line for Cedar' }).fill('');
  await expect(rail.getByTestId('send-button')).toBeDisabled();
  await expect(lines.locator('[data-company="Cedar"]')).toContainText('Confirmed: no line');
  await lines.getByRole('checkbox', { name: 'Confirm the line for Cedar' }).check();

  await expect(rail.getByTestId('send-button')).toBeEnabled();
  await rail.getByTestId('send-button').click();
  await page.getByRole('dialog').getByRole('button', { name: 'Send 4' }).click();
  await expect(rail.getByTestId('sent-summary')).toContainText('Queued 4 emails');
  expect(server.builds[0]).toMatchObject({ template_id: 11, personalisation: { Acme: 'Another line for Acme.', Cedar: '' } });
  expect(server.suggested).toHaveLength(2);
});

test('writing to one team at a company warns, and "Keep the most senior 2" unticks the rest', async ({ page }) => {
  const server = await mock(page, { heldName: 'nobody', warnTeams: true });
  await page.goto('/scraper?view=company&companies=BigTeam');
  await page.getByTestId('search-all').click();
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');
  await expect(pipeline.getByTestId('selected-count')).toHaveText('6 can be emailed', { timeout: 20_000 });

  // A warning, never a block: Send is open and the confirm says why to think twice.
  await expect(rail.getByTestId('send-button')).toBeEnabled();
  await rail.getByTestId('send-button').click();
  const dialog = page.getByRole('dialog');
  const warnings = dialog.getByTestId('confirm-warnings');
  await expect(warnings).toContainText("You're emailing 6 people on the Sales / BD team at BigTeam — they often compare notes; consider 1–2.");
  await expect(warnings).toContainText("You're emailing 6 people at BigTeam");

  await warnings.getByRole('button', { name: 'Keep the most senior 2' }).click();
  await expect(pipeline.getByTestId('selected-count')).toHaveText('2 can be emailed');
  await expect(dialog.getByRole('heading')).toHaveText('Send 2 emails from your Gmail?');
  await expect(dialog.getByTestId('confirm-warnings')).toHaveCount(0);

  // The two kept are the two the server ranked most senior.
  const picker = pipeline.getByTestId('recipient-picker');
  for (const name of ['Edsger TeammateF', 'Alan TeammateE']) {
    await expect(picker.getByRole('checkbox', { name: `Write to ${name}` })).toBeChecked();
  }
  await expect(picker.getByRole('checkbox', { name: 'Write to Jean TeammateA' })).not.toBeChecked();

  await dialog.getByRole('button', { name: 'Send 2' }).click();
  await expect(rail.getByTestId('sent-summary')).toContainText('Queued 2 emails');
  expect(server.builds[0].contact_ids as number[]).toHaveLength(2);
});
