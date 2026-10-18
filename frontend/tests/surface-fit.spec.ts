import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

const PEOPLE = [
  { id: 1, name: 'Jean Bartik', email: 'jean.bartik@company1.com', title: 'Director of Operations', company: 'Company 1', person_level: 'working' },
  { id: 2, name: 'Klara Dan', email: 'klara.dan@company2.com', title: 'Head of Partnerships', company: 'Company 2', person_level: 'working' },
];

/** A preview long enough to push anything below it off a 720px screen. */
const LONG_BODY = Array.from({ length: 60 }, (_, i) => `Line ${i + 1} of the rendered message.`).join('\n');

async function mockSurface(page: Page) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    let body: unknown = {};
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') body = { items: PEOPLE, total: PEOPLE.length, limit: 800, offset: 0 };
    else if (p === '/api/campaigns/build') body = {
      recipients: 2, ready: 2, held: [],
      sample: { email: 'jean.bartik@company1.com', subject: 'A long one', body: LONG_BODY },
    };
    else if (p === '/api/yucgoutreach/role-suggestions') body = { company: url.searchParams.get('company'), roles: [], equivalents: [], sources: {} };
    else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/yucgoutreach/runs') body = [];
    else if (p === '/api/outreach/flows') body = [];
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (/\/sequences$|\/custom-formats$/.test(p)) body = [];
    await route.fulfill({ json: body });
  });
}

/** Whether an element has any part on screen right now: inside the window
 *  and inside every ancestor that clips its overflow. */
async function inViewport(page: Page, selector: string): Promise<boolean> {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    let box = el.getBoundingClientRect();
    if (box.height === 0) return false;
    let top = Math.max(box.top, 0);
    let bottom = Math.min(box.bottom, window.innerHeight);
    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
      if (getComputedStyle(parent).overflowY === 'visible') continue;
      box = parent.getBoundingClientRect();
      top = Math.max(top, box.top);
      bottom = Math.min(bottom, box.bottom);
    }
    return bottom > top;
  }, selector);
}

const LANES = '[data-section="campaign-pipeline"] [data-testid="company-lanes"]';
const SHEET_HEADER = '[data-section="campaign-pipeline"] [data-testid="sheet-header"]';

for (const count of [4, 20, 300]) {
  test(`lanes and sheet share a 1280×720 screen with ${count} companies at steps 1 to 3`, async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await mockSurface(page);
    const companies = Array.from({ length: count }, (_, i) => `Company ${i + 1}`);
    await page.goto(`/scraper?view=company&companies=${encodeURIComponent(companies.join(','))}`);

    const pipeline = page.locator('[data-section="campaign-pipeline"]');
    const rail = pipeline.locator('[data-rail]');
    await expect(pipeline.getByText('Jean Bartik')).toBeVisible();

    // Step 2, as arrived.
    expect(await inViewport(page, LANES)).toBe(true);
    expect(await inViewport(page, SHEET_HEADER)).toBe(true);

    // Step 1.
    await rail.getByRole('button', { name: /Add companies/ }).click();
    await expect(pipeline.getByTestId('recipient-picker')).toHaveAttribute('data-mode', 'preview');
    expect(await inViewport(page, LANES)).toBe(true);
    expect(await inViewport(page, SHEET_HEADER)).toBe(true);

    // Step 3, the hand-off to Drafts.
    await rail.getByRole('button', { name: 'Next: tick who gets it' }).click();
    await rail.getByRole('button', { name: /^Write to these/ }).click();
    await expect(pipeline.getByTestId('recipient-picker')).toHaveAttribute('data-mode', 'review');
    expect(await inViewport(page, LANES)).toBe(true);
    expect(await inViewport(page, SHEET_HEADER)).toBe(true);

    // The aside is the only thing that scrolls: the page does not, and the
    // sheet does not fight it with a scrollbar of its own.
    const scrolls = await page.evaluate(() => ({
      page: document.documentElement.scrollHeight - document.documentElement.clientHeight,
      sheet: getComputedStyle(document.querySelector('[data-testid="sheet"]')!).overflowY,
    }));
    expect(scrolls.page).toBeLessThanOrEqual(0);
    expect(scrolls.sheet).toBe('visible');
  });
}

test('on a phone the rail stacks above the surface', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockSurface(page);
  await page.goto('/scraper?view=company&companies=Company%201,Company%202');

  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  await expect(pipeline.getByText('Jean Bartik')).toBeVisible();
  const boxes = await page.evaluate(() => {
    const rail = document.querySelector('[data-section="campaign-pipeline"] [data-rail]')!.getBoundingClientRect();
    const surface = document.querySelector('[data-section="campaign-pipeline"] [data-surface]')!.getBoundingClientRect();
    return { railBottom: rail.bottom, surfaceTop: surface.top, railLeft: rail.left, surfaceLeft: surface.left };
  });
  expect(boxes.surfaceTop).toBeGreaterThanOrEqual(boxes.railBottom);
  expect(Math.abs(boxes.surfaceLeft - boxes.railLeft)).toBeLessThan(2);
});
