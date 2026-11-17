import type { Page } from '@playwright/test';

/** Synthetic club data behind every main page, with the longest strings each surface has to hold. Specs mock `/api` with it and need no backend. */

const admin = { id: 1, email: 'andre.h.costa@yale.edu', name: 'Andre H. Costa', role: 'admin', is_active: 1, apps: ['core', 'outreach', 'projects', 'resources'] };

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
const COSTS = {
  generated_at: '2026-11-17T03:54:00+00:00',
  aws: {
    connected: true, month: '2026-11', fetched_at: '2026-11-17T03:54:00+00:00', total_usd: 16.473, request_usd: 0.01,
    message: 'Cached daily.', by_service: [
      { name: 'Amazon Elastic Compute Cloud - Compute', usd: 9.7074 },
      { name: 'Amazon Virtual Private Cloud', usd: 2.3517 },
      { name: 'EC2 - Other', usd: 2.3779 },
    ],
    by_environment: [{ name: 'club-res-website-prod', usd: 7.1114 }, { name: 'club-res-website-dev', usd: 6.4662 }, { name: 'untagged', usd: 2.8955 }],
  },
  providers: [
    { id: 'llm', name: 'Claude (Anthropic API / Bedrock)', calls: 117, estimated_usd: 4.28, budget_or_limit: '$15.00/month', unit: 'calls', unit_cost_usd: null, note: 'Actual token usage.', trend: [{ month: '2026-09', calls: 0, usd: 0 }, { month: '2026-10', calls: 80, usd: 2.9 }, { month: '2026-11', calls: 117, usd: 4.28 }] },
    { id: 'tinyfish', name: 'Monid / TinyFish', calls: 932, estimated_usd: 4.66, budget_or_limit: '400/club/hour', unit: 'calls', unit_cost_usd: .005, note: 'A long note about a provider whose stable list price can change independently.', trend: [{ month: '2026-09', calls: 0, usd: 0 }, { month: '2026-10', calls: 300, usd: 1.5 }, { month: '2026-11', calls: 932, usd: 4.66 }] },
  ],
  recommendations: [{ rank: 1, title: 'Run dev only when it is needed instead of keeping a fixed office-hours window', saving: '$6–9/month', risk: 'Developers cannot use the environment while it is stopped.' }],
};

/** Synthetic library entries with the longest strings a card has to hold: title, filename, description, team, contact. */
const RESOURCES = [
  {
    id: 1, kind: 'past_project', title: 'Regional outpatient scheduling software pricing and packaging study for a mid-sized clinic network',
    client: 'Northwind Clinic Network of Greater Hartford Holdings', client_hidden: false, semester: 'Fall 2025',
    industry: 'Healthcare; Technology; Professional Services', industries: ['Healthcare'], areas: 'Market sizing; Customer Analysis; Pricing; Go-to-market strategy',
    description: 'Sized the regional market for outpatient scheduling software and tested three pricing models with clinic managers. '.repeat(6).trim(),
    code_name: null, team_size: 4, disclosure: 'public', status: 'In Progress', files_open: true,
    files: [
      { id: 11, filename: 'Final-presentation-with-a-very-long-descriptive-name-that-goes-on-and-on-v12-reviewed-by-the-board-2026.pptx', byte_size: 2621440, content_type: 'x', admin_only: false },
      { id: 12, filename: 'engagement-notes.docx', byte_size: 18000, content_type: 'x', admin_only: true },
    ],
    files_hidden_count: 0,
    team: [
      { name: 'Dana Fox', role: 'Engagement director', member: true }, { name: 'Eli Park-Longname', role: 'Project manager', member: false },
      { name: 'Ana Long-Surname', role: 'Associate', member: true }, { name: 'Sam Wu', role: 'Associate', member: false },
    ],
    created_at: 1760000000, link: { url: 'https://club-example.sharepoint.com/:f:/r/sites/Club/Shared%20Documents/Regional', provider: 'sharepoint' },
    stored_link: '/:f:/r/sites/Club/Shared%20Documents/Regional', revenue: 250000, contacts: [{ name: 'Robin Vale', email: 'robin.vale.with.a.long.address@northwind-clinic-network.example' }],
  },
  {
    id: 2, kind: 'past_project', title: 'Supply chain review', client: null, client_hidden: true, semester: 'Spring 2025', industry: 'Consumer Goods',
    industries: ['Consumer Goods'], areas: null, description: 'Reviewed the supplier base.', code_name: null, team_size: null, disclosure: 'name_only',
    status: 'Complete', files_open: false, files: [], files_hidden_count: 2, team: [], created_at: 1760000000, link: null,
    stored_link: null, revenue: null, contacts: [],
  },
];

// The alumni portal, with strings that never break by themselves.
const LONG_WORD = 'Supercalifragilisticexpialidocious'.repeat(3);
const LONG_TITLE = `Annual reunion weekend announcement ${LONG_WORD} and the full schedule of events`;
const LONG_EMAIL = `${'a.very.long.alumni.address.'.repeat(3)}name@subdomain.example-organisation.org`;
const LONG_LINK = `https://www.linkedin.com/in/${'a-very-long-profile-name-'.repeat(6)}`;
const PNG_PIXEL = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');
const PORTAL_ME = { email: LONG_EMAIL, name: 'Alexandria Montgomery-Featherstonehaugh', board: true };
const PORTAL_POSTS = [
  {
    id: 1, title: LONG_TITLE, status: 'published', pinned: true, author: 'Alexandria Montgomery-Featherstonehaugh',
    created_at: 1760000000, updated_at: 1760000000, published_at: 1760000000, cover: { id: 1, url: '/api/alumni/images/1?token=abc' },
    body_html: `<h2>Heading ${LONG_WORD}</h2><p>${LONG_WORD} ${LONG_WORD}</p><ul><li>${LONG_WORD}</li><li>Second</li></ul><ol><li>One</li></ol>`
      + `<blockquote>${LONG_WORD}</blockquote><pre>${'x'.repeat(240)}</pre><p><a href="https://example.org/${'segment/'.repeat(20)}">https://example.org/${'segment/'.repeat(20)}</a></p>`,
  },
  {
    id: 2, title: 'A draft the board is still writing', status: 'draft', pinned: false, author: 'YUCG Alumni Board',
    created_at: 1760000000, updated_at: 1760000000, published_at: null, cover: null, body_html: '<p>Short.</p>',
  },
];
const PORTAL_EVENTS = {
  upcoming: [{
    id: 1, title: LONG_TITLE, starts_at: 1950000000, timezone: 'America/Argentina/ComodRivadavia', starts_local: '2030-02-17T12:40',
    place: `${LONG_WORD} Conference Centre, ${'Very Long Street Name '.repeat(4)}`, online_url: `https://meet.example.org/${'room-name-'.repeat(12)}`,
    description_html: `<p>${LONG_WORD}</p>`, created_at: 1760000000, updated_at: 1760000000,
  }],
  past: [{
    id: 2, title: 'Tokyo alumni lunch', starts_at: 1700000000, timezone: 'Asia/Tokyo', starts_local: '2023-11-15T07:13',
    place: 'Shibuya', online_url: null, description_html: '', created_at: 1760000000, updated_at: 1760000000,
  }],
};
const PORTAL_DIRECTORY = [
  {
    id: 1, display_name: `${LONG_WORD} ${LONG_WORD}`, class_year: 2016, company: `${LONG_WORD} Holdings`, role: `Senior ${LONG_WORD}`,
    city: `${LONG_WORD}-on-Thames`, linkedin_url: LONG_LINK, note: `${LONG_WORD} ${'note '.repeat(60)}`, coffee_chats: true, email: LONG_EMAIL,
  },
  { id: 2, display_name: 'Ben Private', class_year: 2019, company: null, role: null, city: 'Austin', linkedin_url: null, note: null, coffee_chats: false, email: null },
];
const PORTAL_PROFILE = {
  display_name: `${LONG_WORD} ${LONG_WORD}`, class_year: 2016, company: `${LONG_WORD} Holdings`, role: `Senior ${LONG_WORD}`,
  city: `${LONG_WORD}-on-Thames`, linkedin_url: LONG_LINK, note: 'note '.repeat(90), coffee_chats: true, listed: true, show_email: true, updated_at: 1760000000,
};
const PORTAL_INBOX = [
  {
    id: 1, subject: `${LONG_TITLE} ${LONG_WORD}`, body: `${LONG_WORD} ${LONG_WORD}\n\n${'A long paragraph of the message. '.repeat(20)}`,
    created_at: 1760000000, read: false, archived: false, sender: { name: 'Alexandria Montgomery-Featherstonehaugh', email: LONG_EMAIL },
  },
  { id: 2, subject: 'Question from a deleted account', created_at: 1759990000, read: true, archived: false, sender: null },
];
const PORTAL_OVERVIEW = {
  domains: [{ domain: 'aya.yale.edu', added_at: 1760000000 }],
  allowlist: [{ email: LONG_EMAIL, note: `Class of 2016 ${LONG_WORD}`, added_at: 1760000000 }],
  accounts: [
    { id: 9, email: LONG_EMAIL, name: 'Alexandria Montgomery-Featherstonehaugh', created_at: 1760000000, last_sign_in_at: 1760000000, revoked_at: null, allowed: true, board: true },
    { id: 10, email: 'sam@example.org', name: null, created_at: 1760000000, last_sign_in_at: null, revoked_at: 1760000100, allowed: true, board: false },
  ],
  sign_ins: [{ at: 1760000000, outcome: 'signed_in', email_domain: 'example.org', email: LONG_EMAIL }],
};

/** Admin → Members → Send setup reminders: the longest names and a full list of missing items. */
export const REMINDERS = {
  slack_connected: true,
  members: [
    { user_id: 2, name: 'Ana Long-Surname', email: 'ana.long.surname@yale.edu', reminded_at: null, missing: [
      { key: 'gmail', label: 'Gmail' }, { key: 'role', label: 'Signature role' }, { key: 'linkedin', label: 'LinkedIn' },
      { key: 'phone', label: 'Phone' }, { key: 'sign_off', label: 'Sign-off name' }, { key: 'slack', label: 'Slack' }] },
    { user_id: 3, name: 'Bartholomew Featherstonehaugh-Montgomery', email: 'bartholomew.featherstonehaugh.montgomery@aya.yale.edu', reminded_at: null, missing: [
      { key: 'role', label: 'Signature role' }, { key: 'phone', label: 'Phone' }] },
    { user_id: 4, name: '', email: 'cal@yale.edu', reminded_at: '2026-11-15 09:30:00', missing: [{ key: 'linkedin', label: 'LinkedIn' }] },
  ],
};

export async function mockApi(page: Page, authenticated: boolean) {
  await page.route('**/api/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (/^\/api\/(admin\/)?alumni\/images\//.test(path)) return route.fulfill({ contentType: 'image/png', body: PNG_PIXEL });
    let body: unknown = [];
    if (path === '/api/auth/me') body = authenticated ? { authenticated: true, user: admin } : { authenticated: false };
    else if (path === '/api/analytics/dashboard') body = DASHBOARD;
    else if (path === '/api/analytics/club-metrics') body = METRICS;
    else if (path === '/api/admin/costs') body = COSTS;
    else if (path === '/api/admin/setup-reminders') body = REMINDERS;
    else if (path === '/api/analytics/due-follow-ups') body = { count: 0 };
    else if (path === '/api/contacts') body = { items: CONTACTS, total: CONTACTS.length, limit: 100, offset: 0 };
    else if (path === '/api/contacts/pipeline-metrics' || path === '/api/outreach/metrics/pipeline') body = { by_status: [{ status: 'cold', count: 1 }, { status: 'contacted', count: 1 }, { status: 'replied', count: 1 }] };
    else if (path === '/api/campaigns/1') body = CAMPAIGN;
    else if (path === '/api/resources') body = { items: RESOURCES, total: RESOURCES.length, facets: {
      kinds: [{ value: 'past_project', count: 2 }, { value: 'template', count: 1 }],
      semesters: [{ value: 'Fall 2025', count: 1 }, { value: 'Spring 2025', count: 1 }],
      industries: [{ value: 'Consumer Goods', count: 1 }, { value: 'Healthcare', count: 1 }],
      disclosures: [{ value: 'public', count: 1 }, { value: 'name_only', count: 1 }],
    } };
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
    else if (path === '/api/outreach/templates') body = [{ id: 11, name: 'Intro', subject: 'Introduction from the Yale Undergraduate Consulting Group', body: "Hi {first},\n\nI'd love to work with {company} on a project in the new year. {personalisation}\n\nBest,\n{sender_first_name}", use_case: 'intro', industry: null, scope: 'club', mine: false, editable: false }];
    else if (path === '/api/campaigns/build') body = { recipients: 1, ready: 1, held: [], skipped: 0, companies: ['Acme Industrial Holdings'], sample: { email: 'jordan.rivers@acme-industrial-holdings.com', subject: 'Introduction from the Yale Undergraduate Consulting Group', body: "Hi Jordan,\n\nI'd love to work with Acme Industrial Holdings on a project in the new year.\n\nBest,\nAlice" } };
    else if (path === '/api/settings' || path === '/api/auth/profile' || path.startsWith('/api/gmail')) body = {};
    else if (path.startsWith('/api/auth/') && path.endsWith('/status')) body = { connected: false };
    else if (path.startsWith('/api/yucg/rosters')) body = { rosters: [] };
    else if (path === '/api/contacts/companies/summary') body = [];
    else if (path === '/api/alumni/me') body = PORTAL_ME;
    else if (path === '/api/alumni/home') body = { me: PORTAL_ME, profile: { exists: false, listed: false }, posts: PORTAL_POSTS.filter((p) => p.status === 'published'), events: PORTAL_EVENTS.upcoming, unread: 1 };
    else if (path === '/api/alumni/posts' || path === '/api/admin/alumni/posts') body = { items: PORTAL_POSTS, total: PORTAL_POSTS.length };
    else if (path === '/api/alumni/events' || path === '/api/admin/alumni/events') body = PORTAL_EVENTS;
    else if (path === '/api/alumni/directory' || path === '/api/admin/alumni/directory') body = { items: PORTAL_DIRECTORY };
    else if (path === '/api/alumni/profile') body = { profile: PORTAL_PROFILE, email: PORTAL_ME.email };
    else if (path === '/api/alumni/inbox' || path === '/api/admin/alumni/inbox') body = { items: PORTAL_INBOX, unread: 1 };
    else if (/^\/api\/(admin\/)?alumni\/inbox\/1$/.test(path)) body = { ...PORTAL_INBOX[0], read: true };
    else if (path === '/api/admin/alumni') body = PORTAL_OVERVIEW;
    await route.fulfill({ json: body });
  });
}
