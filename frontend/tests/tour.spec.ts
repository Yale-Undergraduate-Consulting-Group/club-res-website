import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

async function mockApp(page: Page) {
  await page.route('**/api/**', async route => {
    const p = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') body = { items: [], total: 0, limit: 100, offset: 0 };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (/runs$|flows$|sequences$|formats$|generated$|releases$|templates$|attachments$|summary$|my-projects$|campaigns$/.test(p)) body = [];
    await route.fulfill({ json: body });
  });
}

test('the tour walks the whole flow across pages and can be left at any stop', async ({ page }) => {
  await mockApp(page);
  await page.goto('/');

  await page.getByRole('button', { name: 'How this page works' }).click();
  await page.getByRole('button', { name: 'Take the tour' }).click();

  const card = page.getByTestId('tour').getByRole('dialog');
  await expect(card.getByRole('heading', { name: 'How YUCG Outreach works' })).toBeVisible();
  await expect(card).toContainText('1 of 10');

  // The tour moves between the real pages, in the order the work is done.
  await card.getByRole('button', { name: 'Next' }).click();
  await expect(card.getByRole('heading', { name: '1 · Companies' })).toBeVisible();
  await expect(page).toHaveURL(/\/scraper\?view=register/);

  await card.getByRole('button', { name: 'Next' }).click();
  await expect(card.getByRole('heading', { name: '2 · Choose companies' })).toBeVisible();
  await expect(page).toHaveURL(/\/scraper\?view=company/);

  // Keyboard works, both ways.
  await page.keyboard.press('ArrowRight');
  await expect(card.getByRole('heading', { name: '3 · Choose who gets it' })).toBeVisible();
  await expect(card).toContainText('the very senior');
  await page.keyboard.press('ArrowLeft');
  await expect(card.getByRole('heading', { name: '2 · Choose companies' })).toBeVisible();

  for (let i = 0; i < 4; i += 1) await card.getByRole('button', { name: 'Next' }).click();
  await expect(card.getByRole('heading', { name: 'Drafts' })).toBeVisible();
  await expect(page).toHaveURL(/\/studio/);

  // A click on the dimmed page does not end it; Escape does.
  await page.mouse.click(5, 5);
  await expect(card).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('tour')).toHaveCount(0);
});
