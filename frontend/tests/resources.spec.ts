import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/** The Resources library: search and filters, who sees which names and files,
 *  the admin-only block, deleting, and the empty and error states. Every name
 *  below is made up. */

const MEMBER = { id: 2, email: 'ana@yale.edu', name: 'Ana Lee', role: 'standard', is_active: 1 };
const ADMIN = { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1 };

const LONG_DESCRIPTION = 'Sized the regional market for outpatient scheduling software and tested three pricing models with clinic managers. '.repeat(6).trim();
const LONG_FILENAME = 'Final-presentation-template-with-a-very-long-descriptive-name-that-goes-on-and-on-v12-reviewed-by-the-board-of-directors-2026.pptx';

const PROJECT = {
  id: 1, kind: 'past_project', title: 'Regional clinic pricing study', client: 'Northwind Clinic', client_hidden: false,
  semester: 'Fall 2025', industry: 'Healthcare; Technology', industries: ['Healthcare', 'Technology'],
  areas: 'Market sizing; Customer Analysis', description: LONG_DESCRIPTION, code_name: 'Project Lantern', team_size: 4,
  disclosure: 'public', status: 'Complete', files_open: true,
  files: [
    { id: 11, filename: 'final-deck.pptx', byte_size: 2_621_440, content_type: 'application/vnd.ms-powerpoint', admin_only: false },
    { id: 12, filename: 'engagement-notes.docx', byte_size: 18_000, content_type: 'application/msword', admin_only: true },
  ],
  files_hidden_count: 0,
  team: [
    { name: 'Dana Fox', role: 'Engagement director', member: true },
    { name: 'Eli Park', role: 'Project manager', member: false },
    { name: 'Ana Lee', role: 'Associate', member: true },
    { name: 'Sam Wu', role: 'Associate', member: false },
  ],
  created_at: 1_760_000_000,
};
const RESTRICTED = {
  ...PROJECT, id: 2, title: 'Supply chain review', client: null, client_hidden: true, semester: 'Spring 2025', industry: 'Consumer Goods',
  industries: ['Consumer Goods'], areas: 'Operations', description: 'Reviewed the supplier base.', code_name: null, disclosure: 'name_only',
  status: 'In Progress', files_open: false, files: [], files_hidden_count: 2, team: [],
};
const TEMPLATE = {
  ...PROJECT, id: 3, kind: 'template', title: 'Final presentation template', client: null, client_hidden: false, semester: null, industry: null,
  industries: [], areas: null, description: null, code_name: null, team_size: null, disclosure: 'public', status: null,
  files: [{ id: 31, filename: LONG_FILENAME, byte_size: 900, content_type: 'application/vnd.ms-powerpoint', admin_only: false }], team: [],
};
const ADMIN_FIELDS = { revenue: 250_000, contacts: [{ name: 'Robin Vale', email: 'robin.vale@northwind.example' }] };
const FACETS = {
  kinds: ['past_project', 'template', 'survey'],
  semesters: ['Fall 2025', 'Spring 2025'],
  industries: ['Consumer Goods', 'Healthcare', 'Technology'],
};

type Item = Record<string, unknown>;

/** A Resources server in miniature: filters and pages the way the real one does, and leaves revenue and contacts out for members. */
async function mock(page: Page, opts: { user?: typeof ADMIN; items?: Item[]; fail?: { detail: string } } = {}) {
  const user = opts.user ?? MEMBER;
  const admin = user.role === 'admin';
  const source = opts.items ?? [PROJECT, RESTRICTED, TEMPLATE];
  const items = source.map((item) => (admin ? { ...item, ...ADMIN_FIELDS } : item));
  const requests: URLSearchParams[] = [];
  const deleted: string[] = [];
  const downloads: string[] = [];
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
      const hit = items.filter((item) =>
        (!q || `${item.title} ${item.description ?? ''}`.toLowerCase().includes(q))
        && (!url.searchParams.get('kind') || item.kind === url.searchParams.get('kind'))
        && (!url.searchParams.get('semester') || item.semester === url.searchParams.get('semester'))
        && (!url.searchParams.get('industry') || (item.industries as string[]).includes(url.searchParams.get('industry')!)));
      const offset = Number(url.searchParams.get('offset') ?? 0);
      const limit = Number(url.searchParams.get('limit') ?? 60);
      return json({ items: hit.slice(offset, offset + limit), total: hit.length, facets: FACETS });
    }
    const download = path.match(/^\/api\/resources\/(\d+)\/files\/(\d+)\/download$/);
    if (download) {
      downloads.push(`${download[1]}/${download[2]}`);
      return json({ url: '/api/resources/local-file?token=abc', filename: 'final-deck.pptx' });
    }
    const remove = path.match(/^\/api\/resources\/(\d+)$/);
    if (remove && request.method() === 'DELETE') {
      deleted.push(remove[1]);
      return json({ ok: true });
    }
    return json({});
  });
  return { requests, deleted, downloads, state };
}

test('searching and each filter send the right query', async ({ page }) => {
  const { requests } = await mock(page);
  await page.goto('/resources');
  await expect(page.getByRole('heading', { name: 'Regional clinic pricing study' })).toBeVisible();
  expect(requests[0].get('q')).toBeNull();
  expect(requests[0].get('limit')).toBe('30');

  // Typing searches after a pause.
  await page.getByLabel('Search resources').fill('pricing');
  await expect.poll(() => requests.at(-1)?.get('q')).toBe('pricing');
  await expect(page.getByRole('heading', { name: 'Supply chain review' })).toHaveCount(0);

  // Enter searches without waiting for the pause.
  await page.getByLabel('Search resources').fill('zeppelin');
  await page.getByLabel('Search resources').press('Enter');
  await expect(page.getByRole('heading', { name: 'No resources match' })).toBeVisible();
  expect(requests.at(-1)?.get('q')).toBe('zeppelin');
  await page.getByLabel('Search resources').fill('');
  await page.getByLabel('Search resources').press('Enter');
  await expect(page.getByRole('heading', { name: 'Regional clinic pricing study' })).toBeVisible();

  await page.getByLabel('Type', { exact: true }).selectOption({ label: 'Slide templates' });
  await expect.poll(() => requests.at(-1)?.get('kind')).toBe('template');
  await expect(page.getByRole('heading', { name: 'Final presentation template' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Regional clinic pricing study' })).toHaveCount(0);

  await page.getByLabel('Type', { exact: true }).selectOption('');
  await page.getByLabel('Semester', { exact: true }).selectOption('Spring 2025');
  await expect.poll(() => requests.at(-1)?.get('semester')).toBe('Spring 2025');
  await expect(page.getByRole('heading', { name: 'Supply chain review' })).toBeVisible();

  await page.getByLabel('Semester', { exact: true }).selectOption('');
  await page.getByLabel('Industry', { exact: true }).selectOption('Healthcare');
  await expect.poll(() => requests.at(-1)?.get('industry')).toBe('Healthcare');
  await expect(page.getByRole('heading', { name: 'Regional clinic pricing study' })).toBeVisible();
  expect(requests.at(-1)?.get('kind')).toBeNull();
  expect(requests.at(-1)?.get('semester')).toBeNull();
});

test('a restricted project says Confidential client and counts the files it keeps back', async ({ page }) => {
  await mock(page);
  await page.goto('/resources');
  const restricted = page.getByRole('article', { name: 'Supply chain review' });
  await expect(restricted.getByText('Confidential client')).toBeVisible();
  await expect(restricted.getByText('2 files available to the project team')).toBeVisible();
  await expect(restricted.getByRole('button', { name: /Open/ })).toHaveCount(0);

  const open = page.getByRole('article', { name: 'Regional clinic pricing study' });
  await expect(open.getByText('Northwind Clinic')).toBeVisible();
  await expect(open.getByText('Confidential client')).toHaveCount(0);
  await expect(open.getByText(/available to the project team/)).toHaveCount(0);
  await expect(open.getByText('Fall 2025')).toBeVisible();
  await expect(open.getByText('Healthcare; Technology')).toBeVisible();
  await expect(open.getByText('Market sizing; Customer Analysis')).toBeVisible();
  await expect(open.getByText('Engagement director')).toBeVisible();
  await expect(open.getByText('Dana Fox')).toBeVisible();

  // A resource with no client shows no client line at all.
  const template = page.getByRole('article', { name: 'Final presentation template' });
  await expect(template.getByText('Confidential client')).toHaveCount(0);
});

test('a member sees no revenue, contacts, admin-only block or Delete', async ({ page }) => {
  await mock(page);
  await page.goto('/resources');
  await expect(page.getByRole('heading', { name: 'Regional clinic pricing study' })).toBeVisible();
  const body = page.locator('main, body').first();
  await expect(body).not.toContainText('Revenue');
  await expect(body).not.toContainText('Client contacts');
  await expect(body).not.toContainText('robin.vale@northwind.example');
  await expect(page.getByRole('group', { name: 'Admin only' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^Delete/ })).toHaveCount(0);
});

test('an admin sees revenue and client contacts, and the admin-only files are tagged', async ({ page }) => {
  await mock(page, { user: ADMIN });
  await page.goto('/resources');
  const card = page.getByRole('article', { name: 'Regional clinic pricing study' });
  const block = card.getByRole('group', { name: 'Admin only' });
  await expect(block.getByText('$250,000')).toBeVisible();
  await expect(block.getByText('Robin Vale')).toBeVisible();
  await expect(block.getByRole('link', { name: 'robin.vale@northwind.example' })).toHaveAttribute('href', 'mailto:robin.vale@northwind.example');
  await expect(card.getByText('Admin only', { exact: true })).toHaveCount(2); // the block heading and the one admin-only file
  await expect(card.getByRole('button', { name: 'Delete Regional clinic pricing study' })).toBeVisible();
});

test('an admin deletes a resource after the dialog says its files go too', async ({ page }) => {
  const { deleted } = await mock(page, { user: ADMIN });
  await page.goto('/resources');
  await page.getByRole('button', { name: 'Delete Supply chain review' }).click();
  const dialog = page.getByRole('dialog', { name: 'Delete this resource?' });
  await expect(dialog).toContainText('Supply chain review');
  await expect(dialog).toContainText('all of its files are removed');

  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect(deleted).toEqual([]);
  await expect(page.getByRole('heading', { name: 'Supply chain review' })).toBeVisible();

  await page.getByRole('button', { name: 'Delete Supply chain review' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete resource' }).click();
  await expect(page.getByRole('heading', { name: 'Supply chain review' })).toHaveCount(0);
  expect(deleted).toEqual(['2']);
  await expect(page.getByText('Showing 2 of 2 resources')).toBeVisible();
});

test('Open fetches a download address and opens it in a new tab', async ({ page }) => {
  const { downloads } = await mock(page);
  await page.goto('/resources');
  const popup = page.context().waitForEvent('page');
  await page.getByRole('button', { name: 'Open final-deck.pptx' }).click();
  expect(await popup).toBeTruthy();
  expect(downloads).toEqual(['1/11']);
  expect((await popup).url()).toContain('/api/resources/local-file?token=abc');
});

test('Show more loads the next page with an offset', async ({ page }) => {
  const many = Array.from({ length: 35 }, (_, i) => ({ ...TEMPLATE, id: 100 + i, title: `Guide number ${i + 1}`, files: [] }));
  const { requests } = await mock(page, { items: many });
  await page.goto('/resources');
  await expect(page.getByText('Showing 30 of 35 resources')).toBeVisible();
  await page.getByRole('button', { name: 'Show more' }).click();
  await expect(page.getByText('Showing 35 of 35 resources')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Show more' })).toHaveCount(0);
  expect(requests.at(-1)?.get('offset')).toBe('30');
  await expect(page.getByRole('heading', { name: 'Guide number 35' })).toBeVisible();
});

test('an empty library and a failed load each say so, and Retry loads again', async ({ page }) => {
  const { state } = await mock(page, { items: [], fail: { detail: 'The library is unavailable' } });
  await page.goto('/resources');
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('The library is unavailable');
  await expect(page.getByRole('heading', { name: 'No resources match' })).toHaveCount(0);
  state.fail = undefined;
  await alert.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('heading', { name: 'No resources match' })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('Resources opens from Home and has no accessibility violations', async ({ page }) => {
  await mock(page, { user: ADMIN });
  await page.goto('/');
  await page.getByRole('region', { name: 'Your tools' }).getByRole('link', { name: /Resources/ }).click();
  await expect(page).toHaveURL(/\/resources$/);
  const bar = page.getByRole('navigation', { name: 'Main navigation' }).first();
  await expect(bar.getByRole('link', { name: 'Resources' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Regional clinic pricing study' })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
