import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

const CONTACT = { id: 1, name: 'Jordan Rivers', email: 'jordan@acme.com', title: 'VP Marketing', company: 'Acme Corp' };

async function mockStudio(page: Page, citations: unknown) {
  const asked: string[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    let body: unknown = {};
    if (path === '/api/auth/me') body = { authenticated: true, user };
    else if (path === '/api/contacts') body = { items: [CONTACT], total: 1, limit: 100, offset: 0 };
    else if (path === '/api/settings') body = {};
    else if (path === '/api/projects/suggest-citations') {
      asked.push(new URL(request.url()).search);
      body = citations;
    } else if (/\/companies\/summary$/.test(path)) body = [];
    else if (/\/releases$|\/templates$|\/sequences$|\/generated$|\/my-projects$|\/attachments$/.test(path)) body = [];
    await route.fulfill({ json: body });
  });
  return asked;
}

test('suggest what to cite fills the value-proposition field from real projects on file', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop');
  const asked = await mockStudio(page, {
    projects: [{ id: 1, client_name: 'Google', description: 'Market entry study', semester: 'Spring 2026' }],
    team_experience: [{ user_name: 'Aaron Combs', role_in_project: 'Market Analyst', client_name: 'Adidas', semester: 'Fall 2025' }],
  });
  await page.goto('/studio');

  const button = page.getByRole('button', { name: 'Suggest what to cite' });
  // No contact selected yet: the button explains why it is unavailable instead of doing nothing.
  await expect(button).toBeDisabled();
  await expect(button).toHaveAttribute('title', /Choose a contact/);

  await page.getByText('Jordan Rivers', { exact: true }).click();
  await expect(button).toBeEnabled();
  await button.click();

  await expect.poll(() => asked.length).toBe(1);
  expect(asked[0]).toContain('company=Acme');

  const valueProp = page.getByPlaceholder('Use only verified facts, such as a relevant service or approved case study');
  await expect(valueProp).toHaveValue(/Past clients we can discuss: Google\./);
  await expect(valueProp).toHaveValue(/Team members with relevant experience: Aaron Combs \(Market Analyst, Adidas project\)\./);
});

test('suggest what to cite shows an inline message when nothing nameable is on file', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop');
  await mockStudio(page, { projects: [], team_experience: [] });
  await page.goto('/studio');

  await page.getByText('Jordan Rivers', { exact: true }).click();
  await page.getByRole('button', { name: 'Suggest what to cite' }).click();

  await expect(page.getByText('No nameable past projects on file yet.')).toBeVisible();
  const valueProp = page.getByPlaceholder('Use only verified facts, such as a relevant service or approved case study');
  await expect(valueProp).toHaveValue('');
});
