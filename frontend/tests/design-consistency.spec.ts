import { expect, test, type Page } from '@playwright/test';

/**
 * The club's maintainer reported buttons of differing sizes, mixed fonts and
 * chart colours from outside the palette. These assertions hold the line: they
 * fail if a new control is hand-rolled instead of using the shared system.
 */

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

async function mockAll(page: Page) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/analytics/dashboard') {
      body = {
        contacts_discovered_today: 0, emails_in_queue: 0, active_campaigns: 0,
        total_sent: 5, opened: 0, open_rate: 0, reply_rate: 20,
        mine: { mailed: 4, replied: 1, bounced: 1, queued: 1, awaiting: 2, reply_rate: 25 },
        club: { mailed: 5, replied: 2, bounced: 1, queued: 1, awaiting: 2, reply_rate: 40 },
        my_sectors: [], club_sectors: [],
      };
    } else if (Array.isArray(body)) body = [];
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(Array.isArray(body) ? body : body),
    });
  });
}

// Every real action button is one of the two canonical heights. Chips, tab
// strips, toolbar icons and multi-line list rows have their own systems and
// are excluded by not carrying `ui-button`.
test('canonical buttons render at exactly one of the two system sizes', async ({ page }) => {
  await mockAll(page);
  await page.goto('/');
  await page.waitForSelector('button.ui-button', { timeout: 10000 });

  const measured = await page.evaluate(() =>
    [...document.querySelectorAll('button.ui-button')].map((b) => {
      const style = getComputedStyle(b);
      return {
        text: (b.textContent || '').trim().slice(0, 30),
        height: Math.round(b.getBoundingClientRect().height),
        font: style.fontFamily.split(',')[0].replace(/["']/g, ''),
        radius: style.borderTopLeftRadius,
      };
    }),
  );

  for (const button of measured) {
    // 36px is --sm, 44px is the default. A button that stretches to a taller
    // flex row is allowed; a button with its own invented padding is not.
    expect([36, 44], `"${button.text}" height`).toContain(
      button.height > 44 ? 44 : button.height,
    );
    expect(button.font, `"${button.text}" font`).toBe('Lato');
    expect(button.radius, `"${button.text}" radius`).toBe('8px');
  }
});

test('the whole page renders in Lato, including form controls', async ({ page }) => {
  await mockAll(page);
  await page.goto('/');

  const fonts = await page.evaluate(() => {
    const seen = new Set<string>();
    for (const el of document.querySelectorAll('button, input, select, textarea, table, h1, h2, p')) {
      seen.add(getComputedStyle(el).fontFamily.split(',')[0].replace(/["']/g, ''));
    }
    return [...seen];
  });

  // Controls do not inherit the page font by default, which is exactly how
  // buttons and selects ended up in the system face.
  expect(fonts).toEqual(['Lato']);
});

test('charts stay inside the club palette: blues and greys, no stock green or red', async ({ page }) => {
  await mockAll(page);
  await page.goto('/');
  // Wait for the data the chart is drawn from, or this measures an empty card.
  await page.waitForSelector('section[aria-label="Results"] svg g path', { timeout: 10000 });

  const fills = await page.evaluate(() =>
    [...document.querySelectorAll('section[aria-label="Results"] svg g path, section[aria-label="Results"] svg g circle')]
      .map((p) => getComputedStyle(p).fill),
  );

  expect(fills.length).toBeGreaterThan(0);
  for (const fill of fills) {
    const [r, g, b] = fill.match(/\d+/g)!.map(Number);
    // Blue or neutral: the blue channel is never the weakest, which rules out
    // a green or red slice while still allowing greys.
    expect(b, `fill ${fill}`).toBeGreaterThanOrEqual(Math.max(r, g));
  }
});
