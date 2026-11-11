import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'member', is_active: 1 };
const year = new Date().getUTCFullYear();

type Reach = { state: string; by: string | null; by_me: boolean; at: string | null; sends: number; claimed_by: string | null };
const reach = (state: string, extra: Partial<Reach> = {}): Reach => ({
  state, by: null, by_me: false, at: null, sends: 0, claimed_by: null, ...extra,
});
const person = (name: string, email: string, proof: string, reachState: Reach, extra: Record<string, unknown> = {}) => ({
  name, email, title: 'Head of Finance', company: 'Acme', source: 'Company website', on_file: true,
  proof, reach: reachState, ready: false, ...extra,
});

const PEOPLE = [
  person('Grace Hopper', 'grace@acme.com', 'delivered', reach('contacted', { by: 'Ana', at: `${year}-11-03 10:00:00`, sends: 1 })),
  person('Alan Turing', 'alan@acme.com', 'mailbox_confirmed', reach('queued', { by: 'Ben' })),
  person('Ada Lovelace', 'ada@acme.com', 'replied', reach('replied', { by: 'Ana', at: `${year}-11-05 09:00:00` })),
  person('Edsger Dijkstra', 'edsger@held.com', 'mailbox_confirmed', reach('not_contacted', { claimed_by: 'Cara' }), { company: 'Held Co' }),
  person('Barbara Liskov', 'barbara@clean.com', 'mailbox_confirmed', reach('not_contacted'), { ready: true, company: 'Clean Co' }),
];

const SUMMARY = {
  total: 5, capped: false, ready: 1,
  proof: { replied: 1, delivered: 1, bounced: 0, mailbox_confirmed: 3, catch_all: 0, mailbox_rejected: 0, unverified: 0 },
  reach: { replied: 1, bounced: 0, contacted: 1, queued: 1, not_contacted: 2 },
  backlog: { enabled: true, credits_used_today: 18, credits_cap_today: 25, verified_total: 412, queue_remaining: 1930 },
};

/** A directory server in miniature: filters the list the way the real one does. */
async function mock(page: Page, opts: { people?: typeof PEOPLE; fail?: boolean } = {}) {
  const requests: URL[] = [];
  const people = opts.people ?? PEOPLE;
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/api/auth/me') return json({ authenticated: true, user });
    if (path === '/api/directory/summary') return json(SUMMARY);
    if (path === '/api/directory') {
      requests.push(url);
      if (opts.fail) return json({ detail: 'The directory is unavailable' }, 500);
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const state = url.searchParams.get('reach');
      const items = people.filter((p) =>
        (!q || `${p.name} ${p.email}`.toLowerCase().includes(q))
        && (!state || p.reach.state === state)
        && (!url.searchParams.get('ready') || p.ready));
      return json({ items, total: items.length, limit: 50, offset: 0, capped: false });
    }
    return json({});
  });
  return requests;
}

test('the directory shows who was reached, by whom and when, with the verification backlog', async ({ page }) => {
  await mock(page);
  await page.goto('/directory');

  await expect(page.getByRole('status', { name: 'Verification backlog' })).toHaveText(
    '412 addresses confirmed · 18 of 25 checks used today · 1,930 waiting',
  );
  const people = page.getByRole('region', { name: 'People' });
  await expect(people.getByRole('row', { name: /Grace Hopper/ })).toContainText('Contacted by Ana · Nov 3');
  await expect(people.getByRole('row', { name: /Alan Turing/ })).toContainText('Queued by Ben');
  await expect(people.getByRole('row', { name: /Ada Lovelace/ })).toContainText('Replied · Nov 5');
  await expect(people.getByRole('row', { name: /Barbara Liskov/ })).toContainText('Not contacted');
  await expect(people.getByRole('row', { name: /Grace Hopper/ })).toContainText('Delivered before');
  // Another member holds the company: the row says so.
  await expect(people.getByRole('row', { name: /Edsger Dijkstra/ })).toContainText('Held by Cara');
});

test('filter chips and search ask the server, and the export follows the current filter', async ({ page }) => {
  const requests = await mock(page);
  await page.goto('/directory');
  const people = page.getByRole('region', { name: 'People' });
  await expect(people.getByRole('row')).toHaveCount(1 + PEOPLE.length);

  await page.getByRole('button', { name: /Ready to contact/ }).click();
  await expect(people.getByRole('row')).toHaveCount(2);
  await expect(people).toContainText('Barbara Liskov');
  expect(requests.at(-1)!.searchParams.get('ready')).toBe('1');
  await expect(page.getByRole('link', { name: 'Export CSV' })).toHaveAttribute('href', /\/api\/directory\/export\?.*ready=1/);

  await page.getByRole('button', { name: /^Replied/ }).click();
  await expect(people.getByRole('row')).toHaveCount(2);
  await expect(people).toContainText('Ada Lovelace');
  expect(requests.at(-1)!.searchParams.get('reach')).toBe('replied');
  expect(requests.at(-1)!.searchParams.get('ready')).toBeNull();

  await page.getByRole('button', { name: /Everyone/ }).click();
  await page.getByLabel(/Search name/).fill('turing');
  await expect(people.getByRole('row')).toHaveCount(2);
  expect(requests.at(-1)!.searchParams.get('q')).toBe('turing');
  await expect(page.getByRole('link', { name: 'Export CSV' })).toHaveAttribute('href', /export\?.*q=turing/);
});

test('an empty result says so', async ({ page }) => {
  await mock(page);
  await page.goto('/directory');
  await page.getByLabel(/Search name/).fill('nobody by this name');
  await expect(page.getByText('No one matches')).toBeVisible();
});

test('a failed load shows the reason and can be retried', async ({ page }) => {
  await mock(page, { fail: true });
  await page.goto('/directory');
  await expect(page.getByRole('alert')).toContainText('The directory is unavailable');
  await expect(page.getByRole('button', { name: 'Try again' })).toBeVisible();
});
