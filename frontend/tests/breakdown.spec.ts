import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'member', is_active: 1 };

const BREAKDOWN = {
  sections: [
    {
      id: 'funnel', title: 'From contact to reply', chart: 'bar',
      note: 'Each step counts people.',
      rows: [
        { label: 'known', value: 26, secondary: null },
        { label: 'mailed', value: 1, secondary: null },
      ],
    },
    {
      id: 'members', title: 'By member', chart: 'bar',
      rows: [{ label: 'Andre H. Costa', value: 4, secondary: 1 }],
    },
    {
      id: 'sectors', title: 'By sector', chart: 'pie',
      rows: [
        { label: 'Entertainment', value: 20, secondary: null },
        { label: 'Biotech', value: 6, secondary: null },
      ],
    },
  ],
  mine: { mailed: 12, replied: 2, bounced: 1, queued: 4, awaiting: 9, reply_rate: 16.7 },
  club: { mailed: 40, replied: 3, bounced: 2, queued: 5, awaiting: 35, reply_rate: 7.5 },
};

async function mock(page: Page, breakdown: unknown = BREAKDOWN) {
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/analytics/breakdown') body = breakdown;
    else if (path === '/api/analytics/insights') body = { insights: [] };
    else if (path === '/api/campaigns') body = [];
    else if (path === '/api/activity/outreach') body = { days: 30, items: [], by_sender: [] };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
}

test('the breakdown renders whatever sections the server measured', async ({ page }) => {
  await mock(page);
  await page.goto('/analytics');

  // The page does not know the section list: a new measurement on the server
  // appears here with no frontend change.
  await expect(page.getByRole('region', { name: 'From contact to reply' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'By member' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'By sector' })).toBeVisible();

  // A section carrying a second measure shows both, not just the total.
  await expect(page.getByRole('region', { name: 'By member' })).toContainText('4');
  await expect(page.getByRole('region', { name: 'By member' })).toContainText('1 replied');
});

test('an unknown section type still renders rather than blanking the page', async ({ page }) => {
  await mock(page, {
    ...BREAKDOWN,
    sections: [
      ...BREAKDOWN.sections,
      { id: 'brand_new', title: 'Something measured later', chart: 'bar', rows: [{ label: 'x', value: 3, secondary: null }] },
    ],
  });
  await page.goto('/analytics');
  await expect(page.getByRole('region', { name: 'Something measured later' })).toContainText('3');
});

test('outcome charts are scoped to the member or the club, on demand', async ({ page }) => {
  await mock(page);
  await page.goto('/analytics');

  const outcomes = page.getByRole('region', { name: 'Outcomes' });
  // Mine is the default: a member opens this to see their own work. The
  // headline figures fill the space the lone chart used to leave blank.
  await expect(outcomes).toContainText('People mailed12');
  await expect(outcomes).toContainText('Reply rate16.7%');

  await outcomes.getByRole('button', { name: 'The club' }).click();
  await expect(outcomes).toContainText('People mailed40');
  await expect(outcomes).toContainText('Reply rate7.5%');
});
