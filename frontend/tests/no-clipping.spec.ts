import { expect, test, type Page } from '@playwright/test';

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

const admin = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1, apps: ['core', 'outreach', 'projects'] };

const CONTACTS = [
  { id: 1, name: 'Jordan Rivers', email: 'jordan.rivers@acme-industrial-holdings.com', title: 'Vice President, Global Marketing and Partnerships', company: 'Acme Industrial Holdings', pipeline_status: 'cold' },
  { id: 2, name: 'Klara Dan', email: 'klara.dan@company2.com', title: 'Head of Partnerships', company: 'Company 2', pipeline_status: 'contacted' },
  { id: 3, name: 'Jean Bartik', email: 'jean.bartik@company1.com', title: 'Director of Operations', company: 'Company 1', pipeline_status: 'replied' },
];

const DASHBOARD = {
  contacts_discovered_today: 3, emails_in_queue: 5, active_campaigns: 1, total_sent: 40, opened: 12, open_rate: 30, reply_rate: 7.5,
  mine: { mailed: 12, replied: 2, bounced: 1, queued: 4, awaiting: 9, reply_rate: 16.7 },
  club: { mailed: 40, replied: 3, bounced: 2, queued: 5, awaiting: 35, reply_rate: 7.5 },
  my_sectors: [{ sector: 'Entertainment', count: 7 }, { sector: 'Biotech', count: 2 }],
  club_sectors: [{ sector: 'Manufacturing', count: 30 }, { sector: 'Entertainment', count: 9 }],
};

const CAMPAIGN = {
  id: 1, name: 'Fall consumer outreach', status: 'draft', owner_user_id: 1, sender_user_id: 1, contact_count: 3,
  readiness: { ready: true, issues: [] },
  counts: { pending: 2, sent: 0 },
  contacts: CONTACTS.map((c, i) => ({ ...c, contact_id: c.id, id: 100 + i, campaign_id: 1, status: 'pending', email_subject: 'Hello', email_body: 'Hi there' })),
};

const reach = (state: string) => ({ state, by: null, by_me: false, at: null, sends: 0, claimed_by: null });
const DIRECTORY = CONTACTS.map((c) => ({
  name: c.name, email: c.email, title: c.title, company: c.company, source: 'Company website', on_file: true,
  proof: 'mailbox_confirmed', reach: reach('not_contacted'), ready: true, contact_id: c.id, run_id: null, prospect_id: null,
}));

const METRICS = {
  days: 30, min_sample: 5,
  targets: [
    { key: 'ready_to_send', label: 'Members ready to send (Gmail connected)', count: 9, of: 10, value: 90, target: '≥ 90%', met: true },
    { key: 'bounce_rate', label: 'Bounce rate', count: 3, of: 20, value: 15, target: '< 5%', met: false },
  ],
  waiting: { contacts: 16, oldest_added_at: '2026-11-01 10:00:00' },
  reply_target: '≥ 5%',
  approaches: [{ template_id: 3, template: 'Intro', angle: 'intro', sent: 12, replied: 3, value: 25, met: true }],
  members: [{ user_id: 2, name: 'Ana', searches: 30, drafts: 4, sent: 3, replies: 1, bounces: 0, ready_to_send: true }],
  not_measured: ['Positive replies: replies are not classified yet.'],
};

async function mockApi(page: Page, authenticated: boolean) {
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === '/api/auth/me') body = authenticated ? { authenticated: true, user: admin } : { authenticated: false };
    else if (path === '/api/analytics/dashboard') body = DASHBOARD;
    else if (path === '/api/analytics/club-metrics') body = METRICS;
    else if (path === '/api/analytics/due-follow-ups') body = { count: 0 };
    else if (path === '/api/contacts') body = { items: CONTACTS, total: CONTACTS.length, limit: 100, offset: 0 };
    else if (path === '/api/contacts/pipeline-metrics' || path === '/api/outreach/metrics/pipeline') body = { by_status: [{ status: 'cold', count: 1 }, { status: 'contacted', count: 1 }, { status: 'replied', count: 1 }] };
    else if (path === '/api/campaigns/1') body = CAMPAIGN;
    else if (path === '/api/campaigns') body = [CAMPAIGN];
    else if (path === '/api/directory/summary') body = {
      total: 3, capped: false, ready: 3,
      proof: { replied: 0, delivered: 0, bounced: 0, mailbox_confirmed: 3, catch_all: 0, mailbox_rejected: 0, unverified: 0 },
      reach: { replied: 0, bounced: 0, contacted: 0, queued: 0, not_contacted: 3 },
      backlog: { enabled: true, credits_used_today: 18, credits_cap_today: 25, verified_total: 412, queue_remaining: 1930 },
    };
    else if (path === '/api/directory') body = { items: DIRECTORY, total: DIRECTORY.length, limit: 50, offset: 0, capped: false };
    else if (path === '/api/admin/users') body = [admin, { id: 2, email: 'ana.long.surname@yale.edu', name: 'Ana Long-Surname', role: 'standard', is_active: 1 }];
    else if (path === '/api/admin/projects') body = [{ id: 1, name: 'Project Lego', semester: 'Spring 2026', description: '', client_name: '', discussable: false }];
    else if (path === '/api/yucgoutreach/register/summary') body = { tiers: [], sectors: [], recent_ingests: [] };
    else if (path === '/api/yucgoutreach/register') body = { items: [], total: 0, limit: 40, offset: 0 };
    else if (path === '/api/ai/models') body = { groups: [] };
    else if (path === '/api/settings' || path === '/api/auth/profile' || path.startsWith('/api/gmail')) body = {};
    else if (path.startsWith('/api/auth/') && path.endsWith('/status')) body = { connected: false };
    else if (path.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (path === '/api/contacts/companies/summary') body = [];
    await route.fulfill({ json: body });
  });
}

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
  { name: 'Home', url: '/', ready: /Results/ },
  { name: 'Find contacts', url: '/scraper', ready: /Find/ },
  { name: 'Outreach pipeline', url: '/outreach', ready: 'Jordan Rivers' },
  {
    name: 'Drafts',
    url: '/studio',
    ready: /Writing as/,
    steps: [
      draftsStep('1. Contact'),
      async (page) => { await page.getByText('Jordan Rivers', { exact: true }).first().click(); },
      draftsStep('2. AI assistance'),
      draftsStep('3. Write'),
      async (page) => { await page.locator('.email-studio-body').evaluate((el) => el.scrollIntoView({ block: 'center' })); },
    ],
  },
  { name: 'Directory', url: '/directory', ready: 'Jean Bartik' },
  { name: 'Campaign', url: '/campaigns/1', ready: 'Fall consumer outreach' },
  { name: 'Admin Members', url: '/admin', ready: 'Ana Long-Surname' },
  { name: 'Admin Metrics', url: '/admin?view=metrics', ready: /Members ready to send/ },
  { name: 'Profile', url: '/profile', ready: /Profile|Settings/ },
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
