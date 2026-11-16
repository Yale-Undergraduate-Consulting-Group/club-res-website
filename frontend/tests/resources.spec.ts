import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/** Resources browser contract. All names and links are synthetic. */
const MEMBER = { id: 2, email: 'ana@yale.edu', name: 'Ana Lee', role: 'standard', is_active: 1 };
const ADMIN = { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1 };
const SHAREPOINT = 'https://club-example.sharepoint.com/:f:/r/sites/Club/Shared%20Documents/Lantern';
const LONG_DESCRIPTION = 'Sized the regional market for outpatient scheduling software and tested three pricing models with clinic managers. '.repeat(6).trim();
const LONG_FILENAME = 'Final-presentation-template-with-a-very-long-descriptive-name-that-goes-on-and-on-v12-reviewed-by-the-board-of-directors-2026.pptx';

const PROJECT = {
  id: 1, kind: 'past_project', title: 'Northwind Clinic (Lantern)', client: 'Northwind Clinic', client_hidden: false,
  semester: 'Fall 2025', industry: 'Healthcare; Technology', industries: ['Healthcare', 'Technology'], areas: 'Market sizing',
  description: LONG_DESCRIPTION, code_name: 'Lantern', team_size: 4, disclosure: 'public', status: 'Complete', files_open: true, can_manage: false,
  files: [
    { id: 11, filename: 'final-deck.pptx', byte_size: 2_621_440, content_type: 'application/vnd.ms-powerpoint', admin_only: false },
    { id: 12, filename: 'engagement-notes.docx', byte_size: 18_000, content_type: 'application/msword', admin_only: true },
  ], files_hidden_count: 0,
  team: [
    { name: 'Dana Fox', role: 'Engagement director', member: true },
    { name: 'Eli Park', role: 'Project manager', member: false },
    { name: 'Ana Lee', role: 'Associate', member: true },
  ],
  created_at: 1_760_000_000,
  link: { url: SHAREPOINT, provider: 'sharepoint' }, stored_link: '/:f:/r/sites/Club/Shared%20Documents/Lantern',
};
const RESTRICTED = {
  ...PROJECT, id: 2, title: 'Supply chain review', client: 'Southridge Foods', semester: 'Spring 2025', industry: 'Consumer Goods',
  industries: ['Consumer Goods'], areas: 'Operations', description: 'Reviewed the supplier base.', code_name: 'Sprout', disclosure: 'name_only',
  status: 'In Progress', files_open: false, files: [], files_hidden_count: 2, team: [], link: null, stored_link: '/:f:/r/sites/Club/Sprout',
};
const TEMPLATE = {
  ...PROJECT, id: 3, kind: 'template', title: 'Final presentation template', client: null, semester: null, industry: null,
  industries: [], areas: null, description: null, code_name: null, team_size: null, disclosure: 'public', status: null,
  files: [{ id: 31, filename: LONG_FILENAME, byte_size: 900, content_type: 'application/vnd.ms-powerpoint', admin_only: false }],
  team: [], link: null, stored_link: null,
};
const ADMIN_FIELDS = { revenue: 250_000, contacts: [{ name: 'Robin Vale', email: 'robin.vale@northwind.example' }] };
const FACETS = {
  kinds: [{ value: 'past_project', count: 2 }, { value: 'template', count: 1 }, { value: 'survey', count: 0 }],
  semesters: [{ value: 'Fall 2025', count: 1 }, { value: 'Spring 2025', count: 1 }],
  industries: [{ value: 'Consumer Goods', count: 1 }, { value: 'Healthcare', count: 1 }, { value: 'Technology', count: 1 }],
  disclosures: [{ value: 'public', count: 1 }, { value: 'name_only', count: 1 }],
};
type Item = typeof PROJECT;

async function mock(page: Page, opts: { user?: typeof ADMIN; items?: Item[]; configured?: boolean; fail?: { detail: string } } = {}) {
  const user = opts.user ?? MEMBER;
  const admin = user.role === 'admin';
  let source: Item[] = opts.items ?? [PROJECT, RESTRICTED, TEMPLATE];
  const configured = opts.configured ?? true;
  const requests: URLSearchParams[] = [];
  const deleted: string[] = [];
  const downloads: string[] = [];
  const links: { id: string; url: string | null }[] = [];
  const state = { fail: opts.fail };
  await page.context().route('**/api/resources/local-file**', (route) => route.fulfill({ body: 'file' }));
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/auth/me') return json({ authenticated: true, user });
    if (path === '/api/resources' && request.method() === 'GET') {
      requests.push(url.searchParams);
      if (state.fail) return json(state.fail, 500);
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const listed = source.map((item) => ({
        ...item,
        link: configured ? item.link : null,
        ...(admin ? ADMIN_FIELDS : { stored_link: undefined }),
      }));
      const hit = listed.filter((item) =>
        (!q || `${item.title} ${item.description ?? ''}`.toLowerCase().includes(q))
        && (!url.searchParams.get('kind') || item.kind === url.searchParams.get('kind'))
        && (!url.searchParams.get('semester') || item.semester === url.searchParams.get('semester'))
        && (!url.searchParams.get('industry') || item.industries.includes(url.searchParams.get('industry')!))
        && (!url.searchParams.get('disclosure') || item.disclosure === url.searchParams.get('disclosure')));
      const offset = Number(url.searchParams.get('offset') ?? 0);
      const limit = Number(url.searchParams.get('limit') ?? 100);
      return json({
        items: hit.slice(offset, offset + limit), total: hit.length, facets: FACETS,
        ...(admin ? { sharepoint_configured: configured } : {}),
      });
    }
    if (path === '/api/resources' && request.method() === 'POST') {
      const body = request.postDataJSON();
      const added = { ...TEMPLATE, ...body, id: 99, client: null, industries: body.industry ? [body.industry] : [], files: [], link: { url: body.url, provider: 'google_drive' }, stored_link: body.url };
      source = [...source, added];
      return json({ ...added, ...ADMIN_FIELDS });
    }
    const setLink = path.match(/^\/api\/resources\/(\d+)\/link$/);
    if (setLink && request.method() === 'PUT') {
      const body = request.postDataJSON();
      links.push({ id: setLink[1], url: body.url });
      const item = source.find((entry) => String(entry.id) === setLink[1])!;
      const changed = { ...item, link: body.url ? { url: body.url, provider: body.url.includes('google') ? 'google_drive' : 'sharepoint' } : null, stored_link: body.url };
      source = source.map((entry) => (entry.id === changed.id ? changed : entry));
      return json({ ...changed, ...ADMIN_FIELDS });
    }
    const download = path.match(/^\/api\/resources\/(\d+)\/files\/(\d+)\/download$/);
    if (download) {
      downloads.push(`${download[1]}/${download[2]}`);
      return json({ url: '/api/resources/local-file?token=abc', filename: 'final-deck.pptx' });
    }
    const remove = path.match(/^\/api\/resources\/(\d+)$/);
    if (remove && request.method() === 'DELETE') {
      deleted.push(remove[1]);
      source = source.filter((entry) => String(entry.id) !== remove[1]);
      return json({ ok: true });
    }
    return json({});
  });
  return { requests, deleted, downloads, links, state };
}

const openRow = async (page: Page, name: string) => {
  const article = page.getByRole('article', { name });
  await article.getByRole('button', { expanded: false }).click();
  return article;
};

test('type tabs and sidebar facets show visibility-safe counts and send each filter', async ({ page }) => {
  const { requests } = await mock(page);
  await page.goto('/resources');
  const tabs = page.getByRole('tablist', { name: 'Resource type' });
  const filters = page.getByRole('complementary', { name: 'Resource filters' });
  await expect(tabs.getByRole('tab', { name: /Past projects 2/ })).toHaveAttribute('aria-selected', 'true');
  expect(requests[0].get('kind')).toBe('past_project');
  await expect(filters.getByText('Fall 2025').locator('..')).toContainText('1');
  await expect(filters.getByText('Healthcare').locator('..')).toContainText('1');
  expect(requests[0].get('limit')).toBe('200');

  await tabs.getByRole('tab', { name: /Templates 1/ }).click();
  await expect.poll(() => requests.at(-1)?.get('kind')).toBe('template');
  await tabs.getByRole('tab', { name: /^All/ }).click();
  await expect.poll(() => requests.at(-1)?.get('kind') ?? '').toBe('');
  await filters.getByRole('button', { name: /Spring 2025 1/ }).click();
  await expect.poll(() => requests.at(-1)?.get('semester')).toBe('Spring 2025');
  await filters.getByRole('button', { name: /Spring 2025 1/ }).click();
  await filters.getByRole('button', { name: /Healthcare 1/ }).click();
  await expect.poll(() => requests.at(-1)?.get('industry')).toBe('Healthcare');
  await filters.getByRole('button', { name: /Healthcare 1/ }).click();
  await filters.getByRole('button', { name: /Name only 1/ }).click();
  await expect.poll(() => requests.at(-1)?.get('disclosure')).toBe('name_only');
});

test('search stays above the library and is debounced or immediate on Enter', async ({ page }) => {
  const { requests } = await mock(page);
  await page.goto('/resources');
  await page.getByLabel('Search resources').fill('pricing');
  await expect.poll(() => requests.at(-1)?.get('q')).toBe('pricing');
  await expect(page.getByRole('article', { name: 'Supply chain review' })).toHaveCount(0);
  await page.getByLabel('Search resources').fill('zeppelin');
  await page.getByLabel('Search resources').press('Enter');
  await expect(page.getByRole('heading', { name: 'No resources match' })).toBeVisible();
});

test('the search field and its button share one height and one top edge, at every width', async ({ page }) => {
  await mock(page);
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/resources');
    const field = await page.getByLabel('Search resources').boundingBox();
    const button = await page.getByRole('search').getByRole('button', { name: 'Search' }).boundingBox();
    expect(field && button, `${width}px`).toBeTruthy();
    expect(Math.abs(field!.height - button!.height), `${width}px height`).toBeLessThan(1);
    expect(Math.abs(field!.y - button!.y), `${width}px top`).toBeLessThan(1);
  }
});

test('changing type or filter keeps the current list on screen, dimmed, until the next arrives', async ({ page }) => {
  await mock(page);
  await page.goto('/resources');
  await expect(page.getByRole('article', { name: 'Northwind Clinic (Lantern)' })).toBeVisible();
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  await page.route('**/api/resources?*kind=template*', async (route) => { await held; await route.fallback(); });
  await page.getByRole('tablist', { name: 'Resource type' }).getByRole('tab', { name: /Templates/ }).click();
  const results = page.getByRole('region', { name: 'Results' });
  await expect(results).toHaveAttribute('aria-busy', 'true');
  await expect(page.getByRole('article', { name: 'Northwind Clinic (Lantern)' })).toBeVisible();
  release();
  await expect(page.getByRole('article', { name: 'Final presentation template' })).toBeVisible();
  await expect(results).not.toHaveAttribute('aria-busy', 'true');
});

test('semester groups are newest first and collapse without losing their count', async ({ page }) => {
  await mock(page);
  await page.goto('/resources');
  await page.getByRole('tablist', { name: 'Resource type' }).getByRole('tab', { name: /^All/ }).click();
  const groups = page.locator('.resource-group__toggle');
  await expect(groups.nth(0)).toContainText('Fall 2025');
  await expect(groups.nth(1)).toContainText('Spring 2025');
  await expect(groups.nth(2)).toContainText('Templates');
  const fall = page.locator('.resource-group__toggle').filter({ hasText: 'Fall 2025' });
  await expect(fall).toHaveAttribute('aria-expanded', 'true');
  await fall.click();
  await expect(fall).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('article', { name: 'Northwind Clinic (Lantern)' })).toHaveCount(0);
  await fall.click();
  await expect(page.getByRole('article', { name: 'Northwind Clinic (Lantern)' })).toBeVisible();
});

test('one compact row expands inline to description, files and team', async ({ page }) => {
  await mock(page);
  await page.goto('/resources');
  const article = page.getByRole('article', { name: 'Northwind Clinic (Lantern)' });
  await expect(article).toContainText('Northwind Clinic');
  await expect(article).toContainText('Lantern');
  await expect(article).toContainText('Healthcare; Technology');
  await expect(article).toContainText('2 files');
  await expect(article).not.toContainText('Sized the regional market');
  await openRow(page, 'Northwind Clinic (Lantern)');
  await expect(article).toContainText('Sized the regional market');
  await expect(article).toContainText('Dana Fox');
  await expect(article.getByRole('button', { name: 'Open final-deck.pptx' })).toBeVisible();
});

test('SharePoint button appears only when the server resolved the setting', async ({ page }) => {
  await mock(page, { configured: false });
  await page.goto('/resources');
  await expect(page.getByRole('link', { name: 'Open in SharePoint' })).toHaveCount(0);

  await page.unrouteAll({ behavior: 'wait' });
  await mock(page, { configured: true });
  await page.reload();
  const link = page.getByRole('article', { name: 'Northwind Clinic (Lantern)' }).getByRole('link', { name: 'Open in SharePoint' });
  await expect(link).toHaveAttribute('href', SHAREPOINT);
  await expect(page.getByRole('link', { name: 'Open in SharePoint' })).toHaveCount(1);
});

test('admins are told when SHAREPOINT_BASE_URL is not set; members are not', async ({ page }) => {
  await mock(page, { user: ADMIN, configured: false });
  await page.goto('/resources');
  await expect(page.getByText(/SHAREPOINT_BASE_URL is not set/)).toBeVisible();
  await expect(page.getByRole('link', { name: 'Open in SharePoint' })).toHaveCount(0);

  await page.unrouteAll({ behavior: 'wait' });
  await mock(page, { user: ADMIN, configured: true });
  await page.reload();
  await expect(page.getByRole('link', { name: 'Open in SharePoint' })).toHaveCount(1);
  await expect(page.getByText(/SHAREPOINT_BASE_URL is not set/)).toHaveCount(0);

  await page.unrouteAll({ behavior: 'wait' });
  await mock(page, { configured: false });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Fall 2025' })).toBeVisible();
  await expect(page.getByText(/SHAREPOINT_BASE_URL/)).toHaveCount(0);
});

test('member sees no admin-only fields or controls', async ({ page }) => {
  await mock(page);
  await page.goto('/resources');
  await openRow(page, 'Northwind Clinic (Lantern)');
  await expect(page.getByRole('group', { name: 'Admin only' })).toHaveCount(0);
  await expect(page.getByText('Revenue')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Delete/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Add linked resource' })).toHaveCount(0);
});

test('admin edits a link and adds a link-only resource', async ({ page }) => {
  const { links, requests } = await mock(page, { user: ADMIN });
  await page.goto('/resources');
  const article = await openRow(page, 'Northwind Clinic (Lantern)');
  const block = article.getByRole('group', { name: 'Admin only' });
  await expect(block).toContainText('$250,000');
  const drive = 'https://drive.google.com/drive/folders/synthetic';
  await block.getByLabel('SharePoint or Google Drive link').fill(drive);
  await block.getByRole('button', { name: 'Save link' }).click();
  await expect(article.getByRole('link', { name: 'Open in Google Drive' })).toHaveAttribute('href', drive);
  expect(links).toEqual([{ id: '1', url: drive }]);

  await page.getByRole('button', { name: 'Add linked resource' }).click();
  const dialog = page.getByRole('dialog', { name: 'Add linked resource' });
  await dialog.getByLabel('Title').fill('Linked survey guide');
  await dialog.getByLabel('Type').selectOption('survey');
  await dialog.getByLabel('SharePoint or Google Drive URL').fill(`${drive}-2`);
  await dialog.getByRole('button', { name: 'Add resource' }).click();
  await expect.poll(() => requests.length).toBeGreaterThan(1);
});

test('admin deletes after expanding a row; Open fetches a download address', async ({ page }) => {
  const { deleted, downloads } = await mock(page, { user: ADMIN });
  await page.goto('/resources');
  const article = await openRow(page, 'Northwind Clinic (Lantern)');
  const popup = page.context().waitForEvent('page');
  await article.getByRole('button', { name: 'Open final-deck.pptx' }).click();
  expect(await popup).toBeTruthy();
  expect(downloads).toEqual(['1/11']);

  await article.getByRole('button', { name: 'Delete Northwind Clinic (Lantern)' }).click();
  await page.getByRole('dialog', { name: 'Delete this resource?' }).getByRole('button', { name: 'Delete resource' }).click();
  await expect(article).toHaveCount(0);
  expect(deleted).toEqual(['1']);
});

test('empty and error states are explicit, and Retry loads again', async ({ page }) => {
  const { state } = await mock(page, { items: [], fail: { detail: 'The library is unavailable' } });
  await page.goto('/resources');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('The library is unavailable');
  state.fail = undefined;
  await alert.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('heading', { name: 'No resources match' })).toBeVisible();
});

test('Resources opens from Home and has no accessibility violations', async ({ page }) => {
  await mock(page, { user: ADMIN });
  await page.goto('/');
  await page.getByRole('region', { name: 'Your tools' }).getByRole('link', { name: /Resources/ }).click();
  await expect(page).toHaveURL(/\/resources$/);
  await expect(page.getByRole('heading', { name: 'Resources' })).toBeVisible();
  // Colours are measured once the page has finished fading in.
  await page.locator('.app-shell-main-inner').evaluate((el) => Promise.all(el.getAnimations().map((animation) => animation.finished)));
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});

/** File storage on top of mock(): uploads land on the app itself (no bucket), as in local development. */
async function mockFiles(page: Page, base: Item) {
  let item = { ...base, can_manage: true };
  const calls: string[] = [];
  const sent: string[] = [];
  const versions: { id: number; filename: string; byte_size: number; content_type: string; created_at: number; uploaded_by_name: string; current: boolean }[] = [];
  const state = { failTransfer: false };
  await page.route('**/api/resources/local-upload**', async (route) => {
    sent.push(route.request().postDataBuffer()?.toString() ?? '');
    await route.fulfill(state.failTransfer ? { status: 500, body: 'no' } : { json: { ok: true } });
  });
  await page.route(/\/api\/resources\/1\/files\//, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/resources/1/files/', '');
    calls.push(`${request.method()} ${path}`);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === 'uploads') {
      const body = request.postDataJSON();
      const fileId = body.file_id ?? 50;
      const versionId = 500 + versions.length;
      versions.unshift({ id: versionId, filename: body.filename, byte_size: body.byte_size, content_type: body.content_type, created_at: 1_760_000_000 + versions.length, uploaded_by_name: 'Ana Lee', current: false });
      return json({ file_id: fileId, version_id: versionId, upload: { url: `/api/resources/local-upload?token=t${versionId}`, method: 'PUT', headers: { 'Content-Type': body.content_type } } });
    }
    const complete = path.match(/^(\d+)\/uploads\/(\d+)\/complete$/);
    if (complete) {
      const version = versions.find((entry) => entry.id === Number(complete[2]))!;
      versions.forEach((entry) => { entry.current = entry === version; });
      const file = { id: Number(complete[1]), filename: version.filename, byte_size: version.byte_size, content_type: version.content_type, admin_only: false, version_id: version.id };
      item = { ...item, files: [...item.files.filter((entry) => entry.id !== file.id), file] };
      return json(item);
    }
    if (/^\d+\/uploads\/\d+\/cancel$/.test(path)) return json({ ok: true });
    if (/^\d+\/versions$/.test(path)) return json(versions);
    const restore = path.match(/^(\d+)\/versions\/(\d+)\/restore$/);
    if (restore) {
      const version = versions.find((entry) => entry.id === Number(restore[2]))!;
      versions.forEach((entry) => { entry.current = entry === version; });
      item = { ...item, files: item.files.map((entry) => (entry.id === Number(restore[1]) ? { ...entry, filename: version.filename, byte_size: version.byte_size, version_id: version.id } : entry)) };
      return json(item);
    }
    if (request.method() === 'DELETE') {
      item = { ...item, files: item.files.filter((entry) => String(entry.id) !== path) };
      return json(item);
    }
    if (path.endsWith('/ask')) {
      return json({ answer: 'It recommended a tiered price, tested with clinic managers [Slide 3].', citations: [{ page: 3, label: 'Slide 3' }, { page: 7, label: 'Slide 7' }] });
    }
    return route.fallback();
  });
  return { calls, sent, state };
}

const upload = (name: string, text: string) => ({ name, mimeType: 'application/pdf', buffer: Buffer.from(text) });

test('a project team member adds a file, uploads a new version, restores the first and deletes the file', async ({ page }) => {
  await mock(page, { items: [{ ...PROJECT, files: [], can_manage: true }] });
  const { calls, sent } = await mockFiles(page, { ...PROJECT, files: [] });
  await page.goto('/resources');
  const article = await openRow(page, 'Northwind Clinic (Lantern)');

  await article.getByLabel('Add a file to Northwind Clinic (Lantern)').setInputFiles(upload('brief.pdf', 'first draft'));
  // "Uploading brief.pdf…" shows first; the file's own Open button only once it is published.
  await expect(article.getByRole('button', { name: 'Open brief.pdf' })).toBeVisible();
  expect(sent).toEqual(['first draft']);
  expect(calls).toEqual(['POST uploads', 'POST 50/uploads/500/complete']);

  await article.getByLabel('New version of brief.pdf').setInputFiles(upload('brief-v2.pdf', 'second draft'));
  await expect(article.getByRole('button', { name: 'Open brief-v2.pdf' })).toBeVisible();
  expect(calls.at(-1)).toBe('POST 50/uploads/501/complete');

  await article.getByRole('button', { name: 'Versions of brief-v2.pdf' }).click();
  const history = article.getByRole('group', { name: 'Versions of brief-v2.pdf' });
  await expect(history.getByRole('listitem')).toHaveCount(2);
  await expect(history.getByRole('listitem').first()).toContainText('Current');
  await history.getByRole('button', { name: 'Restore' }).click();
  await expect(article.getByRole('button', { name: 'Open brief.pdf' })).toBeVisible();
  expect(calls).toContain('POST 50/versions/500/restore');

  await article.getByRole('button', { name: 'Delete brief.pdf' }).click();
  await page.getByRole('dialog', { name: 'Delete this file?' }).getByRole('button', { name: 'Delete file' }).click();
  await expect(article.getByRole('button', { name: 'Open brief.pdf' })).toHaveCount(0);
  expect(calls.at(-1)).toBe('DELETE 50');
});

test('a failed transfer is released and nothing is published', async ({ page }) => {
  await mock(page, { items: [{ ...PROJECT, files: [], can_manage: true }] });
  const { calls, state } = await mockFiles(page, { ...PROJECT, files: [] });
  state.failTransfer = true;
  await page.goto('/resources');
  const article = await openRow(page, 'Northwind Clinic (Lantern)');
  await article.getByLabel('Add a file to Northwind Clinic (Lantern)').setInputFiles(upload('brief.pdf', 'draft'));
  await expect(article.getByRole('alert')).toContainText('could not be sent');
  expect(calls).toEqual(['POST uploads', 'POST 50/uploads/500/cancel']);
});

test('members who do not manage a resource get no upload, version or delete controls', async ({ page }) => {
  await mock(page);
  await page.goto('/resources');
  const article = await openRow(page, 'Northwind Clinic (Lantern)');
  await expect(article.getByRole('button', { name: 'Open final-deck.pptx' })).toBeVisible();
  await expect(article.getByRole('button', { name: 'Add file' })).toHaveCount(0);
  await expect(article.getByRole('button', { name: /^Versions of/ })).toHaveCount(0);
  await expect(article.getByRole('button', { name: 'Delete final-deck.pptx' })).toHaveCount(0);
});

test('asking about a file shows the answer and the slides it came from', async ({ page }) => {
  await mock(page);
  const { calls } = await mockFiles(page, PROJECT);
  await page.goto('/resources');
  const article = await openRow(page, 'Northwind Clinic (Lantern)');
  await article.getByRole('button', { name: 'Ask about final-deck.pptx' }).click();
  const box = article.getByRole('group', { name: 'Ask about final-deck.pptx' });
  await box.getByLabel('Question about final-deck.pptx').fill('What price did they recommend?');
  await box.getByRole('button', { name: 'Ask' }).click();
  await expect(box).toContainText('tiered price');
  await expect(box).toContainText('From Slide 3, Slide 7');
  expect(calls).toEqual(['POST 11/ask']);
});
