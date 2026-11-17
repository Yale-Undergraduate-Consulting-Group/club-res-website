import { expect, test, type Page } from '@playwright/test';
import { mockApi } from './support/clubMock';

/**
 * Layout robustness: at every width a member might use, nothing is cut off.
 * Three failures are caught on each main page:
 *  1. content clipped by a box that hides its overflow, or by a form control
 *     too small for its own text (a select showing "Norma", a button label
 *     sliced by a fixed height). Scroll containers and ellipsis that has a
 *     title (the full value on hover) are intentional and do not count.
 *  2. an element reaching past the window edge outside a scroll container,
 *     or the page itself scrolling sideways.
 *  3. a form label whose box overlaps a neighbouring field or label.
 */

const WIDTHS = [1440, 1280, 1100, 1024, 768, 390];

type Problem = string;

/** Runs in the page: every clipping, overflow and label overlap on screen. */
function findProblems(): Problem[] {
  const out: string[] = [];
  const vw = document.documentElement.clientWidth;
  const describe = (el: Element) => {
    const text = (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 40);
    const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
    return `<${el.tagName.toLowerCase()}${cls ? '.' + cls : ''}> "${text}"`;
  };
  const visible = (el: Element) => {
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1 && el.checkVisibility({ visibilityProperty: true, opacityProperty: true });
  };
  const clips = (v: string) => v === 'hidden' || v === 'clip';
  const scrolls = (v: string) => v === 'auto' || v === 'scroll';
  const hasTitle = (el: Element) => !!el.closest('[title]') || !!el.querySelector('[title]');

  const all = Array.from(document.body.querySelectorAll('*')).filter(visible);

  // 1. Clipped content.
  for (const el of all) {
    const cs = getComputedStyle(el);
    const tag = el.tagName;
    if (tag === 'SELECT') {
      const select = el as HTMLSelectElement;
      const option = select.selectedOptions[0];
      if (!option || select.multiple || select.size > 1) continue;
      const ctx = document.createElement('canvas').getContext('2d')!;
      ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      // The arrow is drawn in the right padding: it needs about 20px clear of the text.
      const arrow = Math.max(parseFloat(cs.paddingRight), 0);
      const room = select.clientWidth - parseFloat(cs.paddingLeft) - arrow;
      if (arrow < 20) out.push(`select arrow overlaps its value (right padding ${arrow}px): ${describe(el)}`);
      else if (ctx.measureText(option.text).width > room + 1) out.push(`select cuts its value "${option.text}": ${describe(el)}`);
      continue;
    }
    if (tag === 'TEXTAREA' || tag === 'INPUT' || tag === 'BUTTON') {
      const control = el as HTMLInputElement;
      if (tag === 'INPUT' && !['text', 'email', 'search', 'url', 'tel', 'password', 'number', 'date'].includes(control.type)) continue;
      // A value longer than its field is the member's own text, scrolled by the field;
      // an empty field or a button has nothing but its own label and placeholder to show.
      if (tag !== 'BUTTON' && control.value) continue;
      if (control.scrollWidth > control.clientWidth + 1 || control.scrollHeight > control.clientHeight + 1) {
        if (cs.textOverflow === 'ellipsis' && hasTitle(el)) continue;
        out.push(`control content exceeds its box (${control.scrollWidth}x${control.scrollHeight} in ${control.clientWidth}x${control.clientHeight}): ${describe(el)}`);
      }
      continue;
    }
    if (!clips(cs.overflowX) && !clips(cs.overflowY)) continue;
    const el2 = el as HTMLElement;
    if (el2.clientWidth <= 2 || el2.clientHeight <= 2) continue; // sr-only and the like
    const tooWide = clips(cs.overflowX) && el2.scrollWidth > el2.clientWidth + 1;
    const tooTall = clips(cs.overflowY) && el2.scrollHeight > el2.clientHeight + 1;
    if (!tooWide && !tooTall) continue;
    if (cs.textOverflow === 'ellipsis' && hasTitle(el)) continue;
    out.push(`clipped (${el2.scrollWidth}x${el2.scrollHeight} in ${el2.clientWidth}x${el2.clientHeight}, overflow ${cs.overflowX}/${cs.overflowY}): ${describe(el)}`);
  }

  // 2. Reaching past the window.
  if (document.documentElement.scrollWidth > vw + 1) out.push(`page scrolls sideways (${document.documentElement.scrollWidth} > ${vw})`);
  const contained = (el: Element) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.position === 'fixed') return false;
      if (scrolls(cs.overflowX) || clips(cs.overflowX)) return true;
    }
    return false;
  };
  for (const el of all) {
    if (el.closest('svg') && el.tagName !== 'svg') continue;
    const r = el.getBoundingClientRect();
    if ((r.right > vw + 1 || r.left < -1) && !contained(el)) out.push(`past the window edge (${Math.round(r.left)}..${Math.round(r.right)} of ${vw}): ${describe(el)}`);
  }

  // 3. A label overlapping a neighbouring field or label, as drawn: a box
  //    scrolled out of a scroll container is not on screen.
  const shown = (el: Element) => {
    const r = el.getBoundingClientRect();
    let { left, top, right, bottom } = r;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue;
      const b = p.getBoundingClientRect();
      left = Math.max(left, b.left); top = Math.max(top, b.top);
      right = Math.min(right, b.right); bottom = Math.min(bottom, b.bottom);
    }
    return { left, top, right, bottom };
  };
  const fields = all.filter((el) => /^(INPUT|SELECT|TEXTAREA|LABEL)$/.test(el.tagName) && !(el as HTMLInputElement).type?.match(/^(checkbox|radio|hidden)$/));
  for (const label of all.filter((el) => el.tagName === 'LABEL')) {
    const a = shown(label);
    for (const other of fields) {
      if (other === label || label.contains(other) || other.contains(label)) continue;
      const b = shown(other);
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 1 && h > 1) out.push(`label overlaps a neighbour: ${describe(label)} / ${describe(other)}`);
    }
  }

  // 4. The message field is never covered: what is drawn at its centre and
  //    near its bottom edge is the field itself.
  const message = document.querySelector('.email-studio-body');
  if (message && visible(message)) {
    const r = message.getBoundingClientRect();
    const x = r.left + r.width / 2;
    for (const y of [r.top + r.height / 2, r.bottom - 6]) {
      // Only points on screen can be hit-tested; the rest are scrolled out of the window.
      if (y < 0 || y > window.innerHeight) continue;
      const hit = document.elementFromPoint(x, y);
      if (hit && !message.contains(hit) && !hit.contains(message)) out.push(`message field is covered at y=${Math.round(y)} by ${describe(hit)}`);
    }
  }
  return [...new Set(out)];
}

type Surface = {
  name: string;
  url: string;
  authenticated?: boolean;
  ready: string | RegExp;
  /** Each step moves the page somewhere new (a phone shows one Drafts step at a time); the page is checked after each. */
  steps?: ((page: Page) => Promise<void>)[];
};

/** The Drafts page on a phone shows one step at a time behind these buttons; wider screens show all of them. */
const draftsStep = (label: string) => async (page: Page) => {
  const step = page.locator('.studio-mobile-steps').getByRole('button', { name: label });
  if (await step.isVisible()) await step.click();
};

const SURFACES: Surface[] = [
  { name: 'Outreach overview', url: '/campaigns', ready: /Results/ },
  { name: 'Find contacts', url: '/scraper', ready: /Find/ },
  { name: 'Outreach pipeline', url: '/outreach', ready: 'Jordan Rivers' },
  {
    name: 'Drafts',
    url: '/studio',
    ready: /Writing as/,
    steps: [
      draftsStep('1. Contact'),
      async (page) => { await page.getByText('Jordan Rivers', { exact: true }).first().click(); },
      draftsStep('2. Write'),
      async (page) => { await page.getByTestId('company-lines').waitFor(); },
      async (page) => { await page.locator('.email-studio-body').evaluate((el) => el.scrollIntoView({ block: 'center' })); },
    ],
  },
  { name: 'Directory', url: '/directory', ready: 'Jean Bartik' },
  {
    name: 'Resources', url: '/resources', ready: 'Northwind Clinic Network',
    steps: [
      async (page) => { await page.getByRole('article', { name: /Regional outpatient/ }).getByRole('button', { expanded: false }).click(); },
      async (page) => {
        await page.getByRole('button', { name: /Spring 2025/, expanded: false }).click();
        await page.getByText('Supply chain review').waitFor();
      },
    ],
  },
  { name: 'Campaign', url: '/campaigns/1', ready: 'Fall consumer outreach' },
  { name: 'Admin Members', url: '/admin', ready: 'Ana Long-Surname' },
  { name: 'Admin Metrics', url: '/admin?view=metrics', ready: /Members ready to send/ },
  { name: 'Admin Costs', url: '/admin?view=costs', ready: /Pay-per-call APIs/ },
  { name: 'Profile', url: '/profile', ready: /Profile|Settings/ },
  { name: 'Alumni portal Home', url: '/alumni', authenticated: false, ready: /Latest news/ },
  {
    name: 'Alumni News', url: '/alumni?view=news', authenticated: false, ready: /Annual reunion weekend/,
    steps: [
      async () => {},
      async (page) => { await page.getByRole('button', { name: 'New post' }).click(); await page.getByRole('textbox', { name: 'Post body' }).waitFor(); },
    ],
  },
  {
    name: 'Alumni Events', url: '/alumni?view=events', authenticated: false, ready: /Upcoming/,
    steps: [
      async () => {},
      async (page) => { await page.getByRole('button', { name: 'New event' }).click(); await page.getByLabel('Time zone').waitFor(); },
    ],
  },
  { name: 'Alumni Directory', url: '/alumni?view=directory', authenticated: false, ready: 'Ben Private' },
  { name: 'Alumni My profile', url: '/alumni?view=profile', authenticated: false, ready: /Display name/ },
  { name: 'Alumni Contact the board', url: '/alumni?view=contact', authenticated: false, ready: /Send message/ },
  {
    name: 'Alumni Board inbox', url: '/alumni?view=inbox', authenticated: false, ready: /Question from a deleted account/,
    steps: [
      async () => {},
      async (page) => { await page.getByRole('button', { name: /Annual reunion/ }).click(); await page.getByRole('article', { name: 'Open message' }).waitFor(); },
    ],
  },
  { name: 'Admin Alumni Access', url: '/admin?view=alumni', ready: /Alumni email domains/ },
  { name: 'Admin Alumni News', url: '/admin?view=alumni&section=news', ready: /Annual reunion weekend/ },
  { name: 'Login', url: '/login', authenticated: false, ready: /Sign in with Google/ },
];

for (const surface of SURFACES) {
  for (const width of WIDTHS) {
    test(`${surface.name} at ${width}px has no clipped or overlapping content`, async ({ page }, info) => {
      test.skip(info.project.name !== 'desktop', 'sets its own viewport widths');
      const errors: string[] = [];
      page.on('pageerror', (e) => errors.push(e.message));
      await page.setViewportSize({ width: width, height: 900 });
      await mockApi(page, surface.authenticated ?? true);
      await page.goto(surface.url);
      await expect(page.getByText(surface.ready).locator('visible=true').first()).toBeVisible();
      for (const step of surface.steps ?? [async () => {}]) {
        await step(page);
        await page.evaluate(() => document.fonts.ready);
        // Let entrance animations finish so boxes are at rest.
        await page.waitForTimeout(500);
        expect(errors, 'the page crashed').toEqual([]);
        const problems = await page.evaluate(findProblems);
        expect(problems, `${surface.name} at ${width}px`).toEqual([]);
      }
    });
  }
}
