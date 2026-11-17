import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './support/clubMock';

/**
 * Dark mode end to end. The president reported "the coloration is kind of
 * mixed up": pages kept light-theme ink or white panels after the theme
 * flipped. Every main page is rendered dark and checked for
 *  (a) text a member cannot read: contrast below WCAG AA (4.5:1, or 3:1 for
 *      large text) against the colour actually painted behind it;
 *  (b) a large white or near-white surface left over from the light theme.
 * Then the top-bar theme button: Light → Dark → System, remembered, and
 * System following the operating system live.
 */

type Finding = string;

/** Runs in the page: every unreadable text run and every leftover light surface. */
function audit(): Finding[] {
  type Rgba = [number, number, number, number];
  const parse = (value: string): Rgba | null => {
    const m = value.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const parts = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
  };
  const over = (top: Rgba, under: Rgba): Rgba => {
    const a = top[3];
    return [top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a), top[2] * a + under[2] * (1 - a), 1];
  };
  const luminance = ([r, g, b]: Rgba) => {
    const lin = (c: number) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  };
  const ratio = (a: Rgba, b: Rgba) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const label = (el: Element) => {
    const cls = typeof el.className === 'string' && el.className.trim() ? `.${el.className.trim().split(/\s+/).slice(0, 3).join('.')}` : '';
    return `${el.tagName.toLowerCase()}${cls} "${(el.textContent || '').trim().slice(0, 40)}"`;
  };

  /** The colour painted behind an element: its own and its ancestors' backgrounds composited, or null over an image. */
  const backdrop = (el: Element): Rgba | null => {
    const layers: Rgba[] = [];
    for (let node: Element | null = el; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage !== 'none' || node.tagName === 'IMG' || node.tagName === 'VIDEO') return null;
      const bg = parse(style.backgroundColor);
      if (bg && bg[3] > 0) {
        layers.push(bg);
        if (bg[3] >= 1) break;
      }
    }
    let colour: Rgba = [255, 255, 255, 1];
    for (const layer of layers.reverse()) colour = over(layer, colour);
    return colour;
  };

  const findings: Finding[] = [];
  const visible = (el: Element) => {
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };

  // (a) Every element that prints its own text.
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('svg, [aria-hidden="true"], .sr-only, option')) continue;
    const ownText = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent || '').trim());
    if (!ownText || !visible(el)) continue;
    // Disabled controls are exempt from contrast (WCAG 1.4.3).
    if (el.closest(':disabled, [aria-disabled="true"]')) continue;
    let faded = false;
    for (let node: Element | null = el; node; node = node.parentElement) if (Number(getComputedStyle(node).opacity) < 1) faded = true;
    if (faded) continue;
    const style = getComputedStyle(el);
    const ink = parse(style.color);
    const behind = backdrop(el);
    if (!ink || !behind) continue;
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
    const need = large ? 3 : 4.5;
    const got = ratio(over(ink, behind), behind);
    if (got < need) findings.push(`text ${label(el)} contrast ${got.toFixed(2)} < ${need}`);
  }

  // (b) Large light surfaces left over from the light theme.
  for (const el of document.querySelectorAll('body, body *')) {
    if (!visible(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width * rect.height < 20000) continue;
    const bg = parse(getComputedStyle(el).backgroundColor);
    if (bg && bg[3] > 0.5 && luminance(bg) > 0.6) findings.push(`light surface ${label(el)} ${getComputedStyle(el).backgroundColor} ${Math.round(rect.width)}x${Math.round(rect.height)}`);
  }
  return [...new Set(findings)];
}

type Surface = { name: string; url: string; ready: string | RegExp; authenticated?: boolean; then?: (page: Page) => Promise<void> };

const SURFACES: Surface[] = [
  { name: 'Home', url: '/', ready: /Outreach/ },
  { name: 'Overview', url: '/campaigns', ready: /Results/ },
  {
    name: 'Overview help', url: '/campaigns', ready: /Results/,
    then: async (page) => { await page.getByRole('button', { name: 'How this page works' }).click(); await page.getByRole('dialog').waitFor(); },
  },
  { name: 'Find contacts', url: '/scraper', ready: /Find/ },
  {
    name: 'Find people and Send panel', url: '/scraper?view=company', ready: /Company/,
    then: async (page) => {
      const rail = page.locator('[data-section="campaign-pipeline"] [data-rail]');
      await rail.getByLabel('Company', { exact: true }).fill('Acme Industrial Holdings');
      await rail.getByRole('button', { name: 'Add', exact: true }).click();
      await page.locator('[data-company-chip]').first().waitFor();
    },
  },
  {
    name: 'Drafts', url: '/studio', ready: /Writing as/,
    then: async (page) => { await page.getByText('Jordan Rivers', { exact: true }).first().click(); await page.getByTestId('company-lines').waitFor(); },
  },
  { name: 'Pipeline', url: '/outreach', ready: 'Jordan Rivers' },
  { name: 'Directory', url: '/directory', ready: 'Jean Bartik' },
  { name: 'Analytics', url: '/analytics', ready: /./ },
  { name: 'Campaign', url: '/campaigns/1', ready: 'Fall consumer outreach' },
  { name: 'Resources', url: '/resources', ready: 'Northwind Clinic Network' },
  { name: 'Profile', url: '/profile', ready: /Profile/ },
  { name: 'Profile integrations', url: '/profile?tab=integrations', ready: /Gmail/ },
  { name: 'Profile settings', url: '/profile?tab=settings', ready: /Accent/ },
  { name: 'Admin Members', url: '/admin', ready: 'Ana Long-Surname' },
  { name: 'Admin Metrics', url: '/admin?view=metrics', ready: /Members ready to send/ },
  { name: 'Admin Costs', url: '/admin?view=costs', ready: /Pay-per-call APIs/ },
  { name: 'Admin Apps', url: '/admin?view=apps', ready: /./ },
  { name: 'Admin Alumni', url: '/admin?view=alumni', ready: /Alumni email domains/ },
  {
    name: 'Admin Alumni toast', url: '/admin?view=alumni', ready: /Alumni email domains/,
    then: async (page) => { await page.getByRole('button', { name: 'Remove aya.yale.edu' }).click(); await page.getByText('aya.yale.edu removed.').waitFor(); },
  },
  {
    name: 'Admin Alumni delete dialog', url: '/admin?view=alumni', ready: /Alumni email domains/,
    then: async (page) => { await page.getByRole('button', { name: 'Delete', exact: true }).first().click(); await page.getByRole('dialog').waitFor(); },
  },
  { name: 'Admin Projects', url: '/admin?view=projects', ready: 'Project Lego' },
  { name: 'Admin Activity', url: '/admin?view=audit', ready: /./ },
  { name: 'Admin Settings', url: '/admin?view=apikeys', ready: /./ },
  { name: 'YUCG Connect sign-in', url: '/login', authenticated: false, ready: /Sign in with Google/ },
  { name: 'YUCG Connect sign-in error', url: '/login?error=revoked', authenticated: false, ready: /turned off/ },
  { name: 'Alumni Home', url: '/alumni', authenticated: false, ready: /Latest news/ },
  { name: 'Alumni News', url: '/alumni?view=news', authenticated: false, ready: /Annual reunion weekend/ },
  { name: 'Alumni Events', url: '/alumni?view=events', authenticated: false, ready: /Upcoming/ },
  { name: 'Alumni Directory', url: '/alumni?view=directory', authenticated: false, ready: 'Ben Private' },
  { name: 'Alumni My profile', url: '/alumni?view=profile', authenticated: false, ready: /Display name/ },
  { name: 'Alumni Contact the board', url: '/alumni?view=contact', authenticated: false, ready: /Send message/ },
  { name: 'Alumni Board inbox', url: '/alumni?view=inbox', authenticated: false, ready: /Question from a deleted account/ },
];

for (const surface of SURFACES) {
  test(`${surface.name} is readable in dark mode`, async ({ page }, info) => {
    test.skip(info.project.name !== 'desktop', 'colours do not depend on the viewport');
    await page.addInitScript(() => localStorage.setItem('yucg_theme', 'dark'));
    await mockApi(page, surface.authenticated ?? true);
    await page.goto(surface.url);
    await expect(page.getByText(surface.ready).locator('visible=true').first()).toBeVisible();
    if (surface.then) await surface.then(page);
    await page.evaluate(() => document.fonts.ready);
    // Let entrance animations and colour transitions settle.
    await page.waitForTimeout(500);
    await expect(page.locator('html')).toHaveClass(/\bdark\b/);
    expect(await page.evaluate(audit), surface.name).toEqual([]);
  });
}

test('the top-bar theme button cycles Light, Dark, System and remembers the choice', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await mockApi(page, true);
  await page.goto('/campaigns');
  const html = page.locator('html');
  const button = page.getByRole('button', { name: /^Theme:/ });
  // It sits after the divider, immediately left of the help button.
  const help = page.getByRole('button', { name: 'How this page works' });
  await expect(button).toBeVisible();
  const [a, b] = [await button.boundingBox(), await help.boundingBox()];
  expect(a && b && a.x < b.x && Math.round(a.width) === Math.round(b.width) && Math.round(a.height) === Math.round(b.height)).toBe(true);

  await expect(button).toHaveAttribute('aria-label', /Light.*dark/i);
  await expect(html).not.toHaveClass(/\bdark\b/);

  await button.click();
  await expect(button).toHaveAttribute('aria-label', /Dark.*system/i);
  await expect(html).toHaveClass(/\bdark\b/);

  await page.reload();
  await expect(html).toHaveClass(/\bdark\b/);
  await expect(button).toHaveAttribute('aria-label', /Dark/);

  await button.click();
  await expect(button).toHaveAttribute('aria-label', /System.*light/i);
  await expect(html).not.toHaveClass(/\bdark\b/);
  // System follows the operating system live, without a reload.
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(html).toHaveClass(/\bdark\b/);
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(html).not.toHaveClass(/\bdark\b/);

  await page.reload();
  await expect(button).toHaveAttribute('aria-label', /System/);
  await button.click();
  await expect(button).toHaveAttribute('aria-label', /Light/);
});

// The sign-in page has no top bar, so it has no theme button; it follows the stored choice.
test('the alumni portal header has the theme button too', async ({ page }) => {
  await mockApi(page, false);
  await page.goto('/alumni');
  const button = page.getByRole('button', { name: /^Theme:/ });
  await expect(button).toBeVisible();
  await button.click();
  await expect(page.locator('html')).toHaveClass(/\bdark\b/);
});
