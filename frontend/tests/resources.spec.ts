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
  description: LONG_DESCRIPTION, code_name: 'Lantern', team_size: 4, disclosure: 'public', status: 'Complete', files_open: true,
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

test('sidebar facets show visibility-safe counts and send each filter', async ({ page }) => {
  const { requests } = await mock(page);
  await page.goto('/resources');
  const filters = page.getByRole('complementary', { name: 'Resource filters' });
  await expect(filters.getByText('Past projects').locator('..')).toContainText('2');
  await expect(filters.getByText('Fall 2025').locator('..')).toContainText('1');
  await expect(filters.getByText('Healthcare').locator('..')).toContainText('1');
  expect(requests[0].get('limit')).toBe('200');

  await filters.getByRole('button', { name: /Templates 1/ }).click();
  await expect.poll(() => requests.at(-1)?.get('kind')).toBe('template');
  await filters.getByRole('button', { name: /Templates 1/ }).click();
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

test('semester groups are newest first and collapse without losing their count', async ({ page }) => {
  await mock(page);
  await page.goto('/resources');
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
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
