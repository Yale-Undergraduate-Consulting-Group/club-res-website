import { expect, test, type Page } from '@playwright/test';
import type { CostReport } from '../src/api';

const ADMIN = { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1, apps: ['core'] };
const report: CostReport = {
  generated_at: '2026-11-17T03:54:00+00:00',
  aws: {
    connected: true, month: '2026-11', fetched_at: '2026-11-17T03:54:00+00:00', total_usd: 16.473,
    request_usd: .01, message: 'Cached daily.',
    by_service: [{ name: 'Amazon EC2 - Compute', usd: 9.7074 }, { name: 'AWS Key Management Service', usd: 1.0361 }],
    by_environment: [{ name: 'club-res-website-prod', usd: 7.1114 }, { name: 'club-res-website-dev', usd: 6.4662 }, { name: 'untagged', usd: 2.8955 }],
  },
  providers: [
    { id: 'llm', name: 'Claude (Anthropic API / Bedrock)', calls: 12, estimated_usd: 1.2345, budget_or_limit: '$15.00/month', unit: 'calls', unit_cost_usd: null, note: 'Actual recorded tokens.', trend: [{ month: '2026-09', calls: 0, usd: 0 }, { month: '2026-10', calls: 4, usd: .4 }, { month: '2026-11', calls: 12, usd: 1.2345 }] },
    { id: 'tinyfish', name: 'Monid / TinyFish', calls: 34, estimated_usd: null, budget_or_limit: '400/club/hour', unit: 'calls', unit_cost_usd: null, note: 'No stable public unit price.', trend: [{ month: '2026-09', calls: 0, usd: null }, { month: '2026-10', calls: 10, usd: null }, { month: '2026-11', calls: 34, usd: null }] },
  ],
  recommendations: [{ rank: 1, title: 'Run dev only when it is needed', saving: '$6–9/month', risk: 'Dev is unavailable while stopped.' }],
};

async function mock(page: Page, body: typeof report) {
  await page.route('**/api/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user: ADMIN } });
    if (path === '/api/admin/costs') return route.fulfill({ json: body });
    return route.fulfill({ json: [] });
  });
}

test('admin sees AWS, provider usage, trends and reductions', async ({ page }) => {
  await mock(page, report);
  await page.goto('/admin?view=costs');
  await expect(page.getByRole('heading', { name: 'AWS costs' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'By service' })).toBeVisible();
  await expect(page.getByText('club-res-website-dev')).toBeVisible();
  await expect(page.getByTestId('provider-cost-llm')).toContainText('12 calls');
  await expect(page.getByTestId('provider-cost-llm')).toContainText('2026-11: 12 / $1.23');
  await expect(page.getByTestId('provider-cost-tinyfish')).toContainText('Not priced');
  await expect(page.getByRole('heading', { name: 'What can be reduced' })).toBeVisible();
});

test('missing Cost Explorer permission is explicit while API costs remain', async ({ page }) => {
  await mock(page, { ...report, aws: { ...report.aws, connected: false, fetched_at: null, total_usd: null, by_service: [], by_environment: [], message: 'AWS costs not connected. A reviewed Terraform apply is required.' } });
  await page.goto('/admin?view=costs');
  await expect(page.getByText('AWS costs not connected. A reviewed Terraform apply is required.')).toBeVisible();
  await expect(page.getByTestId('provider-cost-llm')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'By service' })).toHaveCount(0);
});
