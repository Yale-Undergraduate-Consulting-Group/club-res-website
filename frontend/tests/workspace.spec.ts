import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };
const campaigns = [
  { id: 1, name: 'Alice outreach', status: 'draft', owner_user_id: 1, sender_user_id: 1, contact_count: 2 },
  { id: 2, name: 'Bob outreach', status: 'draft', owner_user_id: 2, sender_user_id: 2, contact_count: 4 },
  { id: 3, name: 'Legacy outreach', status: 'draft', owner_user_id: null, sender_user_id: null, contact_count: 5 },
];
async function mockWorkspace(page: Page) {
  const mutations: string[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') mutations.push(`${request.method()} ${path}`);
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/yucgoutreach/runs') body = [];
    else if (path === '/api/admin/login-log' || path === '/api/settings/custom-formats') body = [];
    else if (path === '/api/admin/projects') body = [{ id: 1, name: 'Consulting project' }];
    else if (path === '/api/admin/invitations') body = [];
    else if (path === '/api/campaigns') body = campaigns;
    else if (path === '/api/campaigns/2') return route.fulfill({ status: 403, json: { detail: 'Only the owner may read this campaign.' } });
    else if (path === '/api/campaigns/3/ownership-evidence') body = { historical_sender_ids: [2], requires_explicit_confirmation: true };
    else if (path === '/api/admin/users') body = [user, { id: 2, email: 'bob@yale.edu', name: 'Bob', role: 'standard', is_active: 1 }];
    else if (path === '/api/auth/gmail/status') body = { connected: false };
    else if (path === '/api/auth/slack/status') body = { connected: false };
    else if (path === '/api/workspace/storage-quota') body = { quota_bytes: 1073741824, reserved_bytes: 24000, available_bytes: 1073717824 };
    else if (path === '/api/workspace/projects') body = [{ id: 1, name: 'Consulting project', semester: 'Fall 2026' }];
    else if (path === '/api/workspace/documents') body = Array.from({ length: 24 }, (_, index) => ({ id: index + 1, title: `Project report ${index + 1}`, owner_user_id: index ? 2 : 1, owner_email: index ? 'bob@yale.edu' : 'alice@yale.edu', project_id: 1, project_name: 'Consulting project', visibility: 'project', current_version: 1, revision: 1 }));
    else if (path === '/api/assistant/sources') body = [{ id: 1, title: 'Project report 1', owner_user_id: 1, project_id: 1, project_name: 'Consulting project', visibility: 'project', current_version: 1, index_state: 'ready', character_count: 4200 }];
    else if (path === '/api/assistant/threads') body = [];
    else if (path === '/api/assistant/usage') body = { member_requests: 0, club_requests: 0, member_input_tokens: 0, member_output_tokens: 0, club_input_tokens: 0, club_output_tokens: 0, member_estimated_usd: 0, club_estimated_usd: 0, pricing_note: 'Estimate' };
    else if (path === '/api/assistant/ask') body = { answer: 'Use the approved project evidence [D1-C1].', thread_id: 9, model: 'haiku', grounded: true, sources: [{ id: 'D1-C1', document_id: 1, title: 'Project report 1', project_name: 'Consulting project' }], pending_actions: [], navigations: [], lookups: [] };
    else if (path === '/api/assistant/act') body = { ok: true, answer: 'Started Find people run #12 for Acme.', navigations: [{ path: '/scraper', label: 'Open Find contacts' }] };
    else if (/\/documents\/\d+\/versions$/.test(path)) body = [{ id: 1, state: 'ready', filename: 'report.pdf', byte_size: 1000, created_at: 1788960000 }];
    else if (/\/documents\/\d+\/shares$/.test(path)) body = [];
    else if (path === '/api/contacts') body = { items: [], total: 0, limit: 100, offset: 0 };
    else if (path === '/api/ai/models') body = { groups: [] };
    else if (path === '/api/outreach/metrics/pipeline') body = { by_status: [] };
    else if (path === '/api/analytics/time-series') body = { labels: [], sent: [], opened: [], replied: [] };
    else if (path === '/api/analytics/insights') body = { insights: [] };
    else if (path === '/api/activity/outreach') body = { days: 30, items: [], by_sender: [] };
    else if (path.endsWith('/notification-preferences')) body = { admin_digest: true, campaign_summary: false };
    else if (path.startsWith('/api/research/')) body = { items: [] };
    else if (/\/releases$|\/templates$|\/sequences$|\/generated$|\/my-projects$|\/attachments$|\/companies\/summary$/.test(path)) body = [];
    await route.fulfill({ json: body });
  });
  return mutations;
}

test.beforeEach(async ({ page }) => { await mockWorkspace(page); });

test('home only lists campaigns the signed-in member can manage', async ({ page }) => {
  await page.goto('/');
  const campaignsSection = page.getByRole('region', { name: 'Your campaigns' });
  await expect(campaignsSection.getByText('Alice outreach')).toBeVisible();
  await expect(campaignsSection.getByText('Bob outreach')).toHaveCount(0);
  await expect(campaignsSection.getByText('Legacy outreach')).toHaveCount(0);
});

test('an admin can resolve an orphaned campaign’s ownership from home', async ({ page }) => {
  await page.goto('/');
  const review = page.getByRole('region', { name: 'Needs ownership review' });
  await expect(review.getByText('Legacy outreach')).toBeVisible();
  await review.getByRole('button', { name: 'Review ownership evidence' }).click();
  await expect(review.getByText('Recorded senders:', { exact: false })).toContainText('bob@yale.edu');
  await expect(review.getByRole('button', { name: 'Review assignment' })).toBeDisabled();
});

test('unauthorized detail displays an error instead of loading forever', async ({ page }) => {
  await page.goto('/campaigns/2');
  await expect(page.getByText('Only the owner may read this campaign.')).toBeVisible();
  await expect(page.getByText('Loading campaign…')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Review & release' })).toHaveCount(0);
});

for (const [path, title] of [['/', 'Home'], ['/profile?tab=integrations', 'Profile & preferences'], ['/studio', 'Drafts'], ['/scraper', 'Find contacts'], ['/outreach', 'Pipeline'], ['/analytics', 'Full breakdown'], ['/admin', 'Admin']]) {
  test(`accessible page: ${title}`, async ({ page }, testInfo) => {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(title);
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    expect(results.violations.map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) })), path).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath('page.png'), fullPage: false });
  });
}

test('checking an uncertain dispatch never calls a send endpoint', async ({ page }) => {
  const mutations: string[] = [];
  page.on('request', request => { if (request.method() === 'POST' && !new URL(request.url()).pathname.startsWith('/api/telemetry/')) mutations.push(new URL(request.url()).pathname); });
  await page.route('**/api/campaigns/1', route => route.fulfill({ json: { ...campaigns[0], readiness: { ready: false, issues: ['Review uncertain delivery'] }, contacts: [] } }));
  await page.route('**/api/campaigns/1/dispatches', route => route.fulfill({ json: [{ dispatch_key: 'initial:1', recipient: 'client@example.com', sender_user_id: 1, state: 'ambiguous' }] }));
  await page.route('**/api/campaigns/1/dispatches/reconcile', route => route.fulfill({ json: { reconciled: false, state: 'ambiguous', reason: 'No confirmed match. Still quarantined.' } }));
  await page.goto('/campaigns/1');
  await page.getByRole('button', { name: 'Check Sent mail', exact: true }).click();
  await expect(page.getByText('No confirmed match. Still quarantined.')).toBeVisible();
  expect(mutations).toEqual(['/api/campaigns/1/dispatches/reconcile']);
});

test('a campaign warns that an unproven company is mailed one address first', async ({ page }) => {
  await page.route('**/api/campaigns/1/dispatches', route => route.fulfill({ json: [] }));
  await page.route('**/api/campaigns/1', async route => route.fulfill({ json: {
    ...campaigns[0],
    counts: { pending: 6 },
    readiness: {
      ready: true,
      issues: [],
      unproven_companies: ['acme.com'],
      mailbox_proof_note: 'No address has been proven at acme.com. The first email to each goes alone; the rest follow about 45 minutes later unless it bounces.',
    },
    contacts: [],
  } }));
  await page.goto('/campaigns/1');

  // Release stays available: this is what will happen, not a blocker.
  await expect(page.getByText('First email proves the address.')).toBeVisible();
  await expect(page.getByText(/No address has been proven at acme\.com/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Review & release' })).toBeEnabled();
});

test('campaign review exposes the exact recipient message for repair before release', async ({ page }) => {
  let updateBody = '';
  await page.route('**/api/campaigns/1/dispatches', route => route.fulfill({ json: [] }));
  // The edit is sent as a JSON body, not query params: a recipient message can
  // exceed the CloudFront URL limit and would otherwise land in access logs.
  await page.route('**/api/campaigns/1/contact/11', route => {
    if (route.request().method() !== 'PATCH') return route.fallback();
    updateBody = (route.request().postDataJSON() || {}).body || '';
    return route.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/campaigns/1', async route => {
    return route.fulfill({ json: {
      ...campaigns[0],
      counts: { pending: 1 },
      readiness: { ready: true, issues: [] },
      contacts: [{
        id: 11, contact_id: 7, name: 'Client Person', email: 'client@example.org',
        company: 'Example', status: 'pending', email_subject: 'Original subject',
        email_body: '<p>Original <a href="https://files.example.org/proposal">proposal</a></p>', messages: [],
      }],
    } });
  });
  await page.goto('/campaigns/1');
  const recipient = page.getByRole('listitem').filter({ hasText: 'Client Person' });
  await recipient.getByRole('button', { name: 'Edit message' }).click();
  await expect(recipient.locator('textarea')).toHaveValue('Original proposal (https://files.example.org/proposal)');
  await recipient.locator('textarea').fill('Recipient-specific revision');
  await recipient.getByRole('button', { name: 'Save message' }).click();
  await expect.poll(() => updateBody).toBe('Recipient-specific revision');
});

test('campaign deletion confirmation contains keyboard focus', async ({ page }) => {
  await page.route('**/api/campaigns/1', async route => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({ json: { ...campaigns[0], readiness: { ready: true, issues: [] }, counts: {}, contacts: [] } });
  });
  await page.route('**/api/campaigns/1/dispatches', route => route.fulfill({ json: [] }));
  await page.goto('/campaigns/1');
  const deleteButton = page.getByRole('button', { name: 'Delete', exact: true });
  await deleteButton.click();
  const dialog = page.getByRole('dialog', { name: 'Delete campaign?' });
  await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', { name: 'Delete campaign', exact: true })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(dialog.getByRole('button', { name: 'Cancel', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(deleteButton).toBeFocused();
});

test('studio workbench fills the desktop viewport instead of leaving a short generator column', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop');
  await page.goto('/studio');
  await expect(page.getByRole('heading', { name: 'AI assistance' })).toBeVisible();
  const generator = page.locator('#email-generator-section');
  const editor = page.locator('#email-editor-section');
  const brief = page.getByPlaceholder('For example: ask for a 20-minute call about a spring market research project');
  const body = page.locator('.email-studio-body');
  const genBox = await generator.boundingBox();
  const editBox = await editor.boundingBox();
  const briefBox = await brief.boundingBox();
  const bodyBox = await body.boundingBox();
  expect(genBox?.height || 0).toBeGreaterThan(520);
  expect(Math.abs((genBox?.height || 0) - (editBox?.height || 0))).toBeLessThan(48);
  expect(briefBox?.height || 0).toBeGreaterThan(70);
  expect(bodyBox?.height || 0).toBeGreaterThanOrEqual(280);
  expect(editBox!.width).toBeGreaterThan(genBox!.width * 1.5);
  // The action bar is one row of one control height: the send target reads on
  // the button, so nothing wraps the row onto a second line.
  const actions = page.locator('.studio-draft-actions');
  const save = actions.getByRole('button', { name: 'Save draft', exact: true });
  await save.scrollIntoViewIfNeeded();
  await expect(save).toBeInViewport({ ratio: 1 });
  await expect(actions.getByRole('button', { name: /^Send test to .+@/ })).toBeVisible();
  const tops = await actions.evaluate(row => [...row.children].map(child => Math.round(child.getBoundingClientRect().top)));
  expect(new Set(tops).size, 'draft actions wrapped onto a second line').toBe(1);
  await page.locator('.email-studio-editor-column').evaluate(element => { element.scrollTop = 0; });
  await page.screenshot({ path: testInfo.outputPath('studio-workbench.png'), fullPage: false });
});

test('studio supports writing and preview without invoking generation or sending', async ({ page }, testInfo) => {
  const mutations = await mockWorkspace(page);
  await page.goto('/studio');
  await page.getByLabel('Subject', { exact: true }).fill('A consulting idea');
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Hello, I would like to discuss a project.');
  await expect(page.getByRole('heading', { name: 'Email preview', exact: true })).toBeHidden();
  if (testInfo.project.name === 'desktop') {
    const before = await page.getByRole('textbox', { name: 'Message', exact: true }).boundingBox();
    await page.getByRole('button', { name: 'Focus on writing' }).click();
    await expect(page.locator('#email-generator-section')).toBeHidden();
    const after = await page.getByRole('textbox', { name: 'Message', exact: true }).boundingBox();
    expect(after!.width).toBeGreaterThan(before!.width);
  }
  await page.getByRole('button', { name: 'Preview email', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Email preview', exact: true })).toBeVisible();
  await expect(page.locator('.email-studio-gmail-body')).toContainText('A consulting idea');
  await expect(page.locator('.email-studio-gmail-body')).toContainText('alice@yale.edu');
  await expect(page.locator('.email-studio-gmail-body')).not.toContainText('you@gmail.com');
  expect(mutations.some(path => /test-send|generate/.test(path))).toBe(false);
});

const maliciousHtml = '<p><strong>Safe bold</strong> and <em>safe emphasis</em></p><img src="x" onerror="window.__xss=1"><a href="javascript:window.__xss=2">Unsafe link</a><svg onload="window.__xss=3"></svg><script>window.__xss=4</script><iframe srcdoc="<script>parent.__xss=5</script>"></iframe><img src="data:image/svg+xml;base64,PHN2Zy8+"><img alt="Allowed image" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jqysAAAAASUVORK5CYII=">';

test('untrusted draft and preview HTML cannot execute while formatting remains', async ({ page }) => {
  await page.route('**/api/emails/generated?*', route => route.fulfill({ json: [{ id: 99, user_id: 1, contact_id: 1, name: 'Example recipient', email: 'client@example.com', subject: 'Imported safety fixture', body: maliciousHtml, created_at: '2026-09-09T12:00:00Z' }] }));
  await page.goto('/studio?panel=cache');
  await page.getByText('Imported safety fixture', { exact: true }).click();
  const editor = page.locator('.email-studio-body');
  await expect(editor.locator('strong')).toHaveText('Safe bold');
  await expect(editor.locator('em')).toHaveText('safe emphasis');
  await expect(editor.locator('[onerror], [onload], script, svg, iframe, a[href^="javascript:"], img[src^="data:image/svg"]')).toHaveCount(0);
  await expect(editor.locator('img[alt="Allowed image"]')).toHaveCount(1);
  await expect(page.locator('main [onerror], main [onload], main a[href^="javascript:"]')).toHaveCount(0);
  expect(await page.evaluate(() => '__xss' in window)).toBe(false);
});

test('the studio company picker searches the server instead of holding the catalogue', async ({ page }) => {
  const asked: URL[] = [];
  await page.route('**/api/contacts/companies/summary*', async route => {
    const url = new URL(route.request().url());
    asked.push(url);
    const q = (url.searchParams.get('q') || '').toLowerCase();
    const all = Array.from({ length: 500 }, (_, i) => ({
      company: `Company ${i + 1}`, company_domain: `c${i + 1}.com`, contact_count: 3,
    }));
    const matched = q ? all.filter(row => row.company.toLowerCase().includes(q)) : all;
    await route.fulfill({ json: matched.slice(0, Number(url.searchParams.get('limit') || 200)) });
  });
  await page.goto('/studio');

  await page.getByRole('button', { name: /Add this draft to a campaign/ }).click();
  await page.getByRole('button', { name: /Companies to include/ }).click();
  const search = page.getByRole('searchbox', { name: 'Search companies' });
  await expect(search).toBeVisible();

  // The first request is bounded: a six-figure catalogue is never fetched
  // whole just to draw a picker.
  expect(asked[0].searchParams.get('limit')).toBe('200');
  await expect(page.getByText('Showing the first 200. Search to narrow.')).toBeVisible();

  await search.fill('Company 47');
  await expect.poll(() => asked.at(-1)?.searchParams.get('q')).toBe('Company 47');
  await expect(page.getByText('Company 47', { exact: true })).toBeVisible();
  await expect(page.getByText('Showing the first 200. Search to narrow.')).toBeHidden();
});

test('the composer formats like a mail client, and the preview shows the same thing', async ({ page }) => {
  await page.goto('/studio');
  const editor = page.locator('.email-studio-body');
  await editor.click();
  await page.keyboard.type('Dear Ashley,');
  await page.keyboard.press('ControlOrMeta+a');

  const bold = page.getByRole('button', { name: 'Bold', exact: true });
  await bold.click();
  await page.getByRole('button', { name: 'Align centre', exact: true }).click();
  await page.getByLabel('Font', { exact: true }).selectOption({ label: 'Serif' });

  // Inline CSS, not <font> tags: one representation the sanitizer, the
  // preview and the sent message all agree on.
  const html = await editor.innerHTML();
  expect(html).toMatch(/font-weight:\s*bold|<b>|<strong>/);
  expect(html).toContain('text-align: center');
  expect(html).toMatch(/font-family:\s*Georgia/i);

  // The bar reports the caret, so a member can see what they are typing into.
  await expect(bold).toHaveAttribute('aria-pressed', 'true');

  await page.getByRole('button', { name: 'Preview email', exact: true }).click();
  const preview = page.locator('.email-studio-gmail-body');
  await expect(preview).toContainText('Dear Ashley,');
  expect(await preview.innerHTML()).toContain('text-align: center');

  // Clear puts it back to plain text rather than leaving orphaned markup.
  await editor.click();
  await page.keyboard.press('ControlOrMeta+a');
  await page.getByRole('button', { name: 'Remove formatting', exact: true }).click();
  expect(await editor.innerHTML()).not.toContain('text-align: center');
});

test('every panel the app draws opens with the same motion', async ({ page }, testInfo) => {
  await page.goto('/studio');
  const duration = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--disclosure-duration').trim());
  expect(duration).toBe('180ms');

  // A header nav dropdown and an in-page collapsible are different mechanisms
  // (absolute panel vs animated grid row); both read as one motion. The nav
  // dropdowns exist on desktop; mobile navigates through the drawer.
  if (testInfo.project.name === 'desktop') {
    const panel = page.locator('.app-nav-group-panel').first();
    await page.locator('.app-nav-group summary').first().click();
    await expect(panel).toBeVisible();
    expect(await panel.evaluate(el => getComputedStyle(el).animationDuration)).toBe('0.18s');
  }

  const collapsible = page.locator('.ui-disclosure').first();
  expect(await collapsible.evaluate(el => getComputedStyle(el).transitionDuration)).toBe('0.18s');
  expect(await collapsible.evaluate(el => getComputedStyle(el).gridTemplateRows !== '')).toBe(true);
  const chevron = page.locator('.ui-disclosure-chevron').first();
  expect(await chevron.evaluate(el => getComputedStyle(el).transitionDuration)).toBe('0.18s');
});

test('collapsing a workspace panel eases shut instead of snapping', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'the collapsed rail exists at desktop width');
  await page.goto('/studio');
  const panel = page.locator('#email-generator-section');
  const open = (await panel.boundingBox())!.width;
  expect(open).toBeGreaterThan(200);

  // Mid-flight the panel is between its two sizes and a transition is
  // running on it; it used to jump to the 56px rail in one frame.
  const midFlight = await panel.evaluate(async (element) => {
    (element.querySelector('[aria-label="Collapse AI generator panel"]') as HTMLElement).click();
    const settled = Promise.withResolvers<void>();
    setTimeout(settled.resolve, 70);
    await settled.promise;
    return {
      width: element.getBoundingClientRect().width,
      running: element.getAnimations().length,
    };
  });
  expect(midFlight.running).toBeGreaterThan(0);
  expect(midFlight.width).toBeGreaterThan(60);
  expect(midFlight.width).toBeLessThan(open);

  await expect.poll(async () => Math.round((await panel.boundingBox())!.width)).toBe(56);
});

