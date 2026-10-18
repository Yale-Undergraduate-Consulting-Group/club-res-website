import { expect, test, type Page } from '@playwright/test';

const user = { id: 1, email: 'alice@yale.edu', name: 'Alice', role: 'admin', is_active: 1 };

const EXISTING_PROJECT = { id: 1, name: 'Project Lego', semester: 'Spring 2026', description: '', client_name: '', discussable: false };

async function mockAdmin(page: Page) {
  const created: Record<string, unknown>[] = [];
  const updated: { id: number; patch: Record<string, unknown> }[] = [];
  let projects: Record<string, unknown>[] = [EXISTING_PROJECT];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user } });
    if (path === '/api/admin/projects' && request.method() === 'GET') return route.fulfill({ json: projects });
    if (path === '/api/admin/projects' && request.method() === 'POST') {
      const data = request.postDataJSON();
      created.push(data);
      const project = { id: 2, ...data };
      projects = [...projects, project];
      return route.fulfill({ json: project });
    }
    if (/^\/api\/admin\/projects\/\d+$/.test(path) && request.method() === 'PATCH') {
      const id = Number(path.split('/').pop());
      const patch = request.postDataJSON();
      updated.push({ id, patch });
      projects = projects.map((p) => (p.id === id ? { ...p, ...patch } : p));
      return route.fulfill({ json: projects.find((p) => p.id === id) });
    }
    if (/\/api\/admin\/projects\/\d+\/assignments$/.test(path)) return route.fulfill({ json: [] });
    if (path === '/api/admin/users') return route.fulfill({ json: [user] });
    await route.fulfill({ json: [] });
  });
  return { created, updated };
}

test('creating a project defaults discussable to unchecked and submits client_name/discussable', async ({ page }) => {
  const { created } = await mockAdmin(page);
  await page.goto('/admin?view=projects');

  const discussableBox = page.getByLabel('Can be named in outreach (not under NDA)');
  await expect(discussableBox).not.toBeChecked();

  await page.getByPlaceholder('Project name (e.g. Project Lego)').fill('Project Falcon');
  await page.getByPlaceholder('Semester (e.g. Spring 2026)').fill('Fall 2026');
  await page.getByPlaceholder('e.g. Google — leave blank if under NDA').fill('Netflix');
  await discussableBox.check();
  await page.getByRole('button', { name: 'Add Project' }).click();

  await expect.poll(() => created.length).toBe(1);
  expect(created[0]).toMatchObject({ name: 'Project Falcon', semester: 'Fall 2026', client_name: 'Netflix', discussable: true });
});

test('editing an existing project shows its NDA status and saves tagging changes', async ({ page }) => {
  const { updated } = await mockAdmin(page);
  await page.goto('/admin?view=projects');

  await expect(page.getByText('Under NDA', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /Project Lego/ }).click();

  const editClientName = page.getByPlaceholder('Real client name (leave blank if under NDA)');
  await expect(editClientName).toHaveValue('');
  await editClientName.fill('Pepsi');
  await page.getByLabel('Can be named in outreach (not under NDA)').last().check();
  await page.getByRole('button', { name: 'Save tagging' }).click();

  await expect.poll(() => updated.length).toBe(1);
  expect(updated[0]).toEqual({ id: 1, patch: expect.objectContaining({ client_name: 'Pepsi', discussable: true }) });
  await expect(page.getByText('Discussable', { exact: true }).first()).toBeVisible();
});
