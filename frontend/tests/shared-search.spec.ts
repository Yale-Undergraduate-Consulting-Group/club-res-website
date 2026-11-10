import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1 };

// Ana searched NVIDIA a few days ago; nobody on file is at NVIDIA yet.
const TEAMMATE_RUN = {
  id: 5, company_name: 'NVIDIA', status: 'completed', origin: 'member', prospects_count: 1, progress_pct: 100,
  progress_message: 'Done — 1 saved', completed_at: '2026-11-05 12:00:00',
  shared_from: { name: 'Ana Lee', completed_at: '2026-11-05 12:00:00' },
};
const ANA_FOUND = [
  { id: 51, run_id: 5, first_name: 'Ada', last_name: 'Byron', email: 'ada.byron@nvidia.com', title: 'Engineering Manager', ai_verdict: 'verified', person_level: 'working' },
];
const OWN_FOUND_SO_FAR = [
  { id: 91, run_id: 9, first_name: 'Grace', last_name: 'Hopper', email: 'grace.hopper@nvidia.com', title: 'Platform Manager', ai_verdict: 'verified', person_level: 'working' },
];

async function mockSearch(page: Page) {
  const runsCreated: Record<string, unknown>[] = [];
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    let body: unknown = {};
    if (p === '/api/auth/me') body = { authenticated: true, user };
    else if (p === '/api/contacts') body = { items: [], total: 0, limit: 400, offset: 0 };
    else if (p === '/api/contacts/companies/summary') body = [];
    else if (p === '/api/yucgoutreach/resolve-company') {
      body = { name: 'NVIDIA', domain: 'nvidia.com', domain_verified: true, linkedin_url: null, source: 'register', alternatives: [] };
    }
    else if (p === '/api/yucgoutreach/runs' && route.request().method() === 'POST') {
      runsCreated.push(route.request().postDataJSON());
      body = { id: 9, status: 'queued' };
    }
    // The member's own search is working and has already saved one person.
    else if (p === '/api/yucgoutreach/runs/9') {
      body = { id: 9, company_name: 'NVIDIA', status: 'running', progress_pct: 45, progress_message: 'Found 1 of 60 team leads · round 1', prospects_count: 1 };
    }
    else if (p === '/api/yucgoutreach/runs/9/prospects') body = OWN_FOUND_SO_FAR;
    else if (p === '/api/yucgoutreach/runs/5/prospects') body = ANA_FOUND;
    else if (p === '/api/yucgoutreach/runs') {
      body = url.searchParams.getAll('companies').includes('nvidia') ? [TEAMMATE_RUN] : [];
    }
    else if (p === '/api/outreach/flows' || /\/sequences$|\/custom-formats$/.test(p)) body = [];
    else if (p === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (p === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (p === '/api/ai/models') body = { groups: [] };
    else if (p.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    await route.fulfill({ json: body });
  });
  return { runsCreated };
}

test('a teammate\'s recent search shows at once with who found it, and Search again starts a fresh one', async ({ page }) => {
  const { runsCreated } = await mockSearch(page);
  await page.goto('/scraper?view=company');
  const pipeline = page.locator('[data-section="campaign-pipeline"]');
  const rail = pipeline.locator('[data-rail]');

  await rail.getByLabel('Company').fill('NVIDIA');
  await rail.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(rail.getByTestId('chosen-count')).toContainText('1 company added');
  await rail.getByRole('button', { name: 'Next: tick who gets it' }).click();

  // Ana's people are there without a search of the member's own.
  const attribution = pipeline.getByTestId('shared-run');
  await expect(attribution).toContainText('Found by Ana Lee · 2026-11-05');
  await expect(pipeline.getByText('Ada Byron')).toBeVisible();
  expect(runsCreated).toHaveLength(0);

  // Search again is the member's own search, as Find people always was.
  await attribution.getByRole('button', { name: 'Search again' }).click();
  await expect.poll(() => runsCreated.length).toBe(1);
  expect(runsCreated[0]).toMatchObject({ company_name: 'NVIDIA', company_domain: 'nvidia.com' });
  await expect(attribution).toHaveCount(0);

  // The new search's people show while it is still working, not at its end.
  await expect(pipeline.getByText('Grace Hopper')).toBeVisible();
  await expect(pipeline.getByRole('progressbar')).toBeVisible();
});
