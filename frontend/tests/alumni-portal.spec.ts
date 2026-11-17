import { expect, test, type Page, type Route } from '@playwright/test';

/**
 * The alumni portal (/alumni): news, events, the opt-in directory, the profile,
 * contacting the board and the board inbox; and Admin → Alumni, where a club
 * admin marks board members. Every /api call is mocked with synthetic data.
 */

const ME = { email: 'sam.alumnus@example.org', name: 'Sam Alumnus', board: false };
const BOARD_ME = { email: 'robin.board@example.org', name: 'Robin Board', board: true };

type Post = {
  id: number; title: string; body_html: string; status: 'draft' | 'published'; pinned: boolean; author: string;
  created_at: number; updated_at: number; published_at: number | null; cover: { id: number; url: string } | null;
};
type EventRow = {
  id: number; title: string; starts_at: number; timezone: string; starts_local: string; place: string | null;
  online_url: string | null; description_html: string; created_at: number; updated_at: number;
};
type Message = { id: number; subject: string; body: string; created_at: number; read: boolean; archived: boolean; sender: { name: string | null; email: string } | null };
type Entry = {
  id: number; display_name: string; class_year: number | null; company: string | null; role: string | null; city: string | null;
  linkedin_url: string | null; note: string | null; coffee_chats: boolean; email: string | null;
};
type Profile = {
  display_name: string; class_year: number | null; company: string | null; role: string | null; city: string | null;
  linkedin_url: string | null; note: string | null; coffee_chats: boolean; listed: boolean; show_email: boolean; updated_at: number;
};

const NOW = 1_760_000_000;
const post = (id: number, title: string, extra: Partial<Post> = {}): Post => ({
  id, title, body_html: `<p>Body of ${title}</p>`, status: 'published', pinned: false, author: 'Robin Board',
  created_at: NOW, updated_at: NOW, published_at: NOW, cover: null, ...extra,
});
// 2030-12-03 23:00 UTC = 6:00 PM in New York.
const UPCOMING_AT = Date.UTC(2030, 11, 3, 23, 0) / 1000;
// 2024-06-01 03:30 UTC = 12:30 PM in Tokyo.
const PAST_AT = Date.UTC(2024, 5, 1, 3, 30) / 1000;
const EVENTS = {
  upcoming: [{
    id: 1, title: 'Winter mixer', starts_at: UPCOMING_AT, timezone: 'America/New_York', starts_local: '2030-12-03T18:00',
    place: 'The Study at Yale', online_url: 'https://meet.example.org/winter', description_html: '<p>Drinks and <strong>talks</strong>.</p>',
    created_at: NOW, updated_at: NOW,
  }] as EventRow[],
  past: [{
    id: 2, title: 'Tokyo alumni lunch', starts_at: PAST_AT, timezone: 'Asia/Tokyo', starts_local: '2024-06-01T12:30',
    place: 'Shibuya', online_url: null, description_html: '', created_at: NOW, updated_at: NOW,
  }] as EventRow[],
};
const DIRECTORY: Entry[] = [
  { id: 1, display_name: 'Ada Visible', class_year: 2016, company: 'Analytical Engines', role: 'Principal', city: 'Boston', linkedin_url: 'https://www.linkedin.com/in/ada-visible', note: 'Happy to talk about case interviews.', coffee_chats: true, email: 'ada@example.org' },
  { id: 2, display_name: 'Ben Private', class_year: 2019, company: null, role: null, city: 'Austin', linkedin_url: null, note: null, coffee_chats: false, email: null },
];

type Options = {
  me?: typeof ME;
  profile?: Profile | null;
  posts?: Post[];
  events?: typeof EVENTS;
  directory?: Entry[];
  inbox?: Message[];
  contactStatus?: number;
};

type Call = { method: string; path: string; search: string; body: unknown };

const PROFILE: Profile = {
  display_name: 'Sam Alumnus', class_year: 2018, company: null, role: null, city: null, linkedin_url: null, note: null,
  coffee_chats: false, listed: true, show_email: false, updated_at: NOW,
};

async function mockPortal(page: Page, options: Options = {}) {
  const me = options.me ?? ME;
  const calls: Call[] = [];
  let profile = options.profile === undefined ? PROFILE : options.profile;
  const posts = structuredClone(options.posts ?? [post(1, 'Spring mixer recap')]);
  const events = structuredClone(options.events ?? EVENTS);
  const directory = options.directory ?? DIRECTORY;
  const inbox = structuredClone(options.inbox ?? []);
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body });
  const visible = () => (me.board ? posts : posts.filter((p) => p.status === 'published'));
  const unread = () => inbox.filter((m) => !m.read && !m.archived).length;
  const summary = (m: Message) => ({ id: m.id, subject: m.subject, created_at: m.created_at, read: m.read, archived: m.archived, sender: m.sender });

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;
    const body = request.postData() ? request.postDataJSON() : null;
    if (path.startsWith('/api/alumni/') || path.startsWith('/api/admin/alumni')) calls.push({ method, path, search: url.search, body });

    if (path === '/api/auth/me') return json(route, { authenticated: false });
    if (path === '/api/alumni/me') return json(route, me);
    if (path === '/api/alumni/home') {
      return json(route, {
        me, profile: { exists: profile !== null, listed: Boolean(profile?.listed) },
        posts: posts.filter((p) => p.status === 'published').slice(0, 3), events: events.upcoming.slice(0, 3), unread: me.board ? unread() : null,
      });
    }
    if (path === '/api/alumni/posts' && method === 'GET') return json(route, { items: visible(), total: visible().length });
    if (path === '/api/alumni/posts' && method === 'POST') {
      const created = post(100 + posts.length, body.title, { body_html: body.body_html, status: body.status, pinned: body.pinned });
      posts.unshift(created);
      return json(route, created, 201);
    }
    if (path === '/api/alumni/events' && method === 'GET') return json(route, events);
    if (path === '/api/alumni/events' && method === 'POST') {
      const created: EventRow = { id: 50, ...body, starts_at: UPCOMING_AT, created_at: NOW, updated_at: NOW };
      events.upcoming.push(created);
      return json(route, created, 201);
    }
    if (path === '/api/alumni/directory') {
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      return json(route, { items: directory.filter((e) => !q || e.display_name.toLowerCase().includes(q)) });
    }
    if (path === '/api/alumni/profile' && method === 'GET') return json(route, { profile, email: me.email });
    if (path === '/api/alumni/profile' && method === 'PUT') {
      profile = { ...body, updated_at: NOW };
      return json(route, { profile, email: me.email });
    }
    if (path === '/api/alumni/profile/hide') {
      if (profile) profile = { ...profile, listed: false };
      return json(route, { ok: true });
    }
    if (path === '/api/alumni/profile' && method === 'DELETE') {
      profile = null;
      return json(route, { ok: true });
    }
    if (path === '/api/alumni/messages') {
      if (options.contactStatus && options.contactStatus !== 201) return json(route, { detail: 'You have sent too many messages. Try again in an hour.' }, options.contactStatus);
      return json(route, { ok: true }, 201);
    }
    if (path === '/api/alumni/inbox' && method === 'GET') {
      const archived = url.searchParams.get('archived') === 'true';
      return json(route, { items: inbox.filter((m) => m.archived === archived).map(summary), unread: unread() });
    }
    const message = path.match(/^\/api\/alumni\/inbox\/(\d+)(?:\/(\w+))?$/);
    if (message) {
      const found = inbox.find((m) => m.id === Number(message[1]));
      if (!found) return json(route, { detail: 'Not found' }, 404);
      if (method === 'GET') { found.read = true; return json(route, found); }
      if (method === 'DELETE') { inbox.splice(inbox.indexOf(found), 1); return json(route, { ok: true }); }
      if (message[2] === 'archive') found.archived = true;
      if (message[2] === 'restore') found.archived = false;
      if (message[2] === 'unread') found.read = false;
      return json(route, { ok: true });
    }
    return json(route, []);
  });
  return { calls, find: (method: string, path: string) => calls.filter((c) => c.method === method && c.path === path) };
}

test.describe('alumni portal', () => {
  test('home shows the latest post, the next event, a profile prompt, the directory link and a working contact form', async ({ page }) => {
    const portal = await mockPortal(page, { profile: null });
    await page.goto('/alumni');
    await expect(page.getByRole('heading', { name: 'YUCG Connect · Alumni' })).toBeVisible();
    await expect(page.getByText('Sam Alumnus')).toBeVisible();
    await expect(page.getByText('Board', { exact: true })).toHaveCount(0);

    await expect(page.getByRole('article', { name: 'Spring mixer recap' })).toContainText('Body of Spring mixer recap');
    await expect(page.getByRole('article', { name: 'Winter mixer' })).toContainText(/6:00\s*PM\s*EST/);
    await expect(page.getByRole('button', { name: 'Complete your profile' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open the directory' })).toBeVisible();

    await page.getByLabel('Subject').fill('A question about mentoring');
    await page.getByLabel('Message', { exact: true }).fill('Can the board connect me with a mentor?');
    await page.getByRole('button', { name: 'Send message' }).click();
    await expect(page.getByText('Sent to the alumni board')).toBeVisible();
    expect(portal.find('POST', '/api/alumni/messages')[0].body).toEqual({ subject: 'A question about mentoring', body: 'Can the board connect me with a mentor?' });

    await page.getByRole('button', { name: 'Open the directory' }).click();
    await expect(page).toHaveURL(/view=directory/);
    await expect(page.getByRole('heading', { name: 'Alumni directory' })).toBeVisible();
  });

  test('a profile prompt is not shown once a profile exists, and an empty home says so', async ({ page }) => {
    await mockPortal(page, { posts: [], events: { upcoming: [], past: [] } });
    await page.goto('/alumni');
    await expect(page.getByText('No news yet')).toBeVisible();
    await expect(page.getByText('No upcoming events')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Complete your profile' })).toHaveCount(0);
  });

  test('contacting the board shows the server message when the limit is reached', async ({ page }) => {
    await mockPortal(page, { contactStatus: 429 });
    await page.goto('/alumni?view=contact');
    await page.getByLabel('Subject').fill('Hello');
    await page.getByLabel('Message', { exact: true }).fill('Hello board');
    await page.getByRole('button', { name: 'Send message' }).click();
    await expect(page.getByRole('alert')).toContainText('You have sent too many messages');
    await expect(page.getByText('Sent to the alumni board')).toHaveCount(0);
  });

  test('a forbidden portal call shows the server notice instead of hiding the page', async ({ page }) => {
    await page.route('**/api/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: false } });
      if (path === '/api/alumni/me') return route.fulfill({ json: ME });
      if (path === '/api/alumni/posts') return route.fulfill({ status: 403, json: { detail: 'Only the alumni board can do this' } });
      return route.fulfill({ json: [] });
    });
    await page.goto('/alumni?view=news');
    await expect(page.getByRole('alert')).toContainText('Only the alumni board can do this');
    await expect(page).toHaveURL(/\/alumni\?view=news/);
  });


  test('a signed-out visitor is sent to YUCG Connect', async ({ page }) => {
    await page.route('**/api/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: false } });
      return route.fulfill({ status: 401, json: { detail: 'Not signed in' } });
    });
    await page.goto('/alumni');
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('heading', { name: 'YUCG Connect' })).toBeVisible();
  });

  test('a board member writes a post, sanitised, and sees Draft and Pinned badges', async ({ page }) => {
    const portal = await mockPortal(page, {
      me: BOARD_ME,
      posts: [
        post(1, 'Welcome back', { pinned: true }),
        post(2, 'Unfinished announcement', { status: 'draft', published_at: null }),
      ],
    });
    await page.goto('/alumni?view=news');
    await expect(page.getByText('Board', { exact: true })).toBeVisible();
    await expect(page.getByRole('article', { name: 'Welcome back' }).getByText('Pinned')).toBeVisible();
    await expect(page.getByRole('article', { name: 'Unfinished announcement' }).getByText('Draft')).toBeVisible();

    await page.getByRole('button', { name: 'New post' }).click();
    await page.getByLabel('Title').fill('Reunion weekend');
    const editor = page.getByRole('textbox', { name: 'Post body' });
    await expect(editor).toHaveAttribute('aria-multiline', 'true');
    await editor.evaluate((element) => {
      element.innerHTML = '<p>Join us <strong>in May</strong></p><img src=x onerror="window.pwned=1"><script>window.pwned=2</script>';
      element.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await page.getByRole('radio', { name: /Published/ }).check();
    await page.getByRole('checkbox', { name: /Pinned/ }).check();
    await page.getByRole('button', { name: 'Publish' }).click();

    await expect(page.getByText('Post published.')).toBeVisible();
    expect(portal.find('POST', '/api/alumni/posts')[0].body).toEqual({
      title: 'Reunion weekend', body_html: '<p>Join us <strong>in May</strong></p>', status: 'published', pinned: true, cover_image_id: null,
    });
    await expect(page.getByRole('article', { name: 'Reunion weekend' }).getByText('Pinned')).toBeVisible();
  });

  test('the editor toolbar offers the formatting the server keeps', async ({ page }) => {
    await mockPortal(page, { me: BOARD_ME });
    await page.goto('/alumni?view=news');
    await page.getByRole('button', { name: 'New post' }).click();
    const bar = page.getByRole('toolbar', { name: /Post body/ });
    for (const name of ['Bold', 'Italic', 'Heading', 'Bulleted list', 'Numbered list', 'Quote', 'Link', 'Clear formatting']) {
      await expect(bar.getByRole('button', { name })).toBeVisible();
    }
  });

  test('an ordinary alumnus reads news but cannot write, edit or delete', async ({ page }) => {
    await mockPortal(page, { posts: [post(1, 'Welcome back', { pinned: true }), post(2, 'Hidden draft', { status: 'draft' })] });
    await page.goto('/alumni?view=news');
    await expect(page.getByRole('article', { name: 'Welcome back' }).getByText('Pinned')).toBeVisible();
    await expect(page.getByText('Hidden draft')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'New post' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Edit/ })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Delete/ })).toHaveCount(0);
    await expect(page.getByRole('tab', { name: /Board inbox/ })).toHaveCount(0);
  });

  test('post text from the server is sanitised on render', async ({ page }) => {
    await mockPortal(page, {
      posts: [post(1, 'Tricky post', {
        body_html: '<p>Safe <em>text</em></p><img src=x onerror="window.xss=1"><script>window.xss=2</script>'
          + '<a href="javascript:window.xss=3">bad link</a><a href="https://example.org/ok">good link</a><p onclick="window.xss=4">click</p>',
      })],
    });
    await page.goto('/alumni?view=news');
    const article = page.getByRole('article', { name: 'Tricky post' });
    await expect(article).toContainText('Safe text');
    await expect(article.locator('img, script')).toHaveCount(0);
    await expect(article.locator('[onclick], [onerror]')).toHaveCount(0);
    await expect(article.getByRole('link', { name: 'good link' })).toHaveAttribute('rel', 'noopener noreferrer');
    await expect(article.locator('a[href^="javascript"]')).toHaveCount(0);
    await article.getByText('bad link').click();
    await article.getByText('click', { exact: true }).click();
    expect(await page.evaluate(() => 'xss' in window)).toBe(false);
  });

  test('the profile is saved unlisted-by-default, then hidden and deleted', async ({ page }) => {
    const portal = await mockPortal(page, { profile: null });
    await page.goto('/alumni?view=profile');
    await expect(page.getByRole('checkbox', { name: 'List me in the directory' })).not.toBeChecked();
    await expect(page.getByRole('checkbox', { name: /Show my email address/ })).not.toBeChecked();
    await expect(page.getByRole('button', { name: 'Hide from directory' })).toHaveCount(0);

    await page.getByLabel('Display name').fill('Sam A.');
    await page.getByLabel('Class year').fill('2018');
    await page.getByLabel('Company').fill('Northwind');
    await page.getByRole('checkbox', { name: 'List me in the directory' }).check();
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByText('You are listed in the directory')).toBeVisible();
    expect(portal.find('PUT', '/api/alumni/profile')[0].body).toMatchObject({
      display_name: 'Sam A.', class_year: 2018, company: 'Northwind', listed: true, show_email: false, coffee_chats: false, linkedin_url: null,
    });

    await page.getByRole('button', { name: 'Hide from directory' }).click();
    await expect(page.getByText('Your profile is hidden from the directory.')).toBeVisible();
    expect(portal.find('POST', '/api/alumni/profile/hide')).toHaveLength(1);
    await expect(page.getByRole('checkbox', { name: 'List me in the directory' })).not.toBeChecked();

    await page.getByRole('button', { name: 'Delete my profile' }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('30 days');
    await dialog.getByRole('button', { name: 'Delete my profile' }).click();
    await expect(page.getByText('Your profile was deleted.')).toBeVisible();
    expect(portal.find('DELETE', '/api/alumni/profile')).toHaveLength(1);
    await expect(page.getByRole('button', { name: 'Hide from directory' })).toHaveCount(0);
  });

  test('a LinkedIn link that is not on linkedin.com is refused before saving', async ({ page }) => {
    const portal = await mockPortal(page, { profile: null });
    await page.goto('/alumni?view=profile');
    await page.getByLabel('Display name').fill('Sam A.');
    await page.getByLabel('LinkedIn URL').fill('https://example.com/sam');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('linkedin.com');
    expect(portal.find('PUT', '/api/alumni/profile')).toHaveLength(0);
  });

  test('the directory shows listed alumni, hides an email that is not shared, and says it is opt-in when empty', async ({ page }) => {
    const portal = await mockPortal(page);
    await page.goto('/alumni?view=directory');
    const ada = page.getByRole('listitem').filter({ hasText: 'Ada Visible' });
    await expect(ada).toContainText('Class of 2016');
    await expect(ada).toContainText('Principal at Analytical Engines');
    await expect(ada.getByText('Open to coffee chats')).toBeVisible();
    await expect(ada.getByRole('link', { name: 'LinkedIn' })).toHaveAttribute('rel', /noopener/);
    await expect(ada.getByRole('link', { name: 'ada@example.org' })).toHaveAttribute('href', 'mailto:ada@example.org');
    const ben = page.getByRole('listitem').filter({ hasText: 'Ben Private' });
    await expect(ben).toContainText('Austin');
    await expect(ben.getByRole('link')).toHaveCount(0);
    await expect(ben.getByText('Open to coffee chats')).toHaveCount(0);

    await page.getByRole('searchbox', { name: 'Search the directory' }).fill('nobody');
    await expect(page.getByText('No matching alumni')).toBeVisible();
    expect(portal.calls.some((c) => c.path === '/api/alumni/directory' && c.search.includes('q=nobody'))).toBe(true);
  });

  test('an empty directory explains that it is opt-in', async ({ page }) => {
    await mockPortal(page, { directory: [] });
    await page.goto('/alumni?view=directory');
    await expect(page.getByText('Nobody is listed yet')).toBeVisible();
    await expect(page.getByText(/opt-in/)).toBeVisible();
  });

  test('the board inbox lists, opens (marking read), archives and deletes from Archived', async ({ page }) => {
    const portal = await mockPortal(page, {
      me: BOARD_ME,
      inbox: [
        { id: 1, subject: 'Mentoring question', body: 'Line one\nLine two <b>not bold</b>', created_at: NOW, read: false, archived: false, sender: { name: 'Ada Visible', email: 'ada@example.org' } },
        { id: 2, subject: 'Old thread', body: 'Done.', created_at: NOW - 86400, read: true, archived: true, sender: null },
      ],
    });
    await page.goto('/alumni?view=inbox');
    await expect(page.getByRole('tab', { name: /Board inbox/ })).toContainText('1');
    const first = page.getByRole('button', { name: /Mentoring question/ });
    await expect(first).toContainText('Unread');
    await expect(first).toContainText('Ada Visible');

    await first.click();
    const open = page.getByRole('article', { name: 'Open message' });
    await expect(open).toContainText('Line one');
    await expect(open).toContainText('<b>not bold</b>');
    expect(portal.find('GET', '/api/alumni/inbox/1')).toHaveLength(1);
    await expect(first).not.toContainText('Unread');

    await open.getByRole('button', { name: 'Archive' }).click();
    expect(portal.find('POST', '/api/alumni/inbox/1/archive')).toHaveLength(1);
    await expect(page.getByText('Inbox is empty')).toBeVisible();

    await page.getByRole('radio', { name: 'Archived' }).click();
    await expect(page.getByRole('button', { name: /Mentoring question/ })).toBeVisible();
    const old = page.getByRole('button', { name: /Old thread/ });
    await expect(old).toContainText('Deleted account');
    await old.click();
    await expect(page.getByRole('button', { name: 'Restore' })).toBeVisible();
    await page.getByRole('article', { name: 'Open message' }).getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete message' }).click();
    await expect(page.getByText('Message deleted.')).toBeVisible();
    expect(portal.find('DELETE', '/api/alumni/inbox/2')).toHaveLength(1);
  });

  test('a message can be marked unread again', async ({ page }) => {
    const portal = await mockPortal(page, {
      me: BOARD_ME,
      inbox: [{ id: 1, subject: 'Hello', body: 'Hi', created_at: NOW, read: true, archived: false, sender: { name: null, email: 'x@example.org' } }],
    });
    await page.goto('/alumni?view=inbox');
    await page.getByRole('button', { name: /Hello/ }).click();
    await page.getByRole('button', { name: 'Mark unread' }).click();
    expect(portal.find('POST', '/api/alumni/inbox/1/unread')).toHaveLength(1);
    await expect(page.getByRole('button', { name: /Hello/ })).toContainText('Unread');
  });

  test('the inbox is not reachable for an ordinary alumnus', async ({ page }) => {
    const portal = await mockPortal(page);
    await page.goto('/alumni?view=inbox');
    await expect(page.getByRole('tab', { name: /Board inbox/ })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Board inbox' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Latest news' })).toBeVisible();
    expect(portal.calls.filter((c) => c.path === '/api/alumni/inbox')).toHaveLength(0);
  });

  test.describe('events', () => {
    test.use({ timezoneId: 'Europe/Paris' });

    test('upcoming and past events show their own time zone, place and a safe online link', async ({ page }) => {
      await mockPortal(page);
      await page.goto('/alumni?view=events');
      const upcoming = page.getByRole('region', { name: 'Upcoming events' });
      const winter = upcoming.getByRole('article', { name: 'Winter mixer' });
      await expect(winter).toContainText(/Dec 3, 2030/);
      await expect(winter).toContainText(/6:00\s*PM\s*EST/);
      await expect(winter).toContainText('The Study at Yale');
      await expect(winter).toContainText('Drinks and talks.');
      await expect(winter.getByRole('link', { name: 'Join online' })).toHaveAttribute('rel', /noopener/);
      const past = page.getByRole('region', { name: 'Past events' }).getByRole('article', { name: 'Tokyo alumni lunch' });
      await expect(past).toContainText(/12:30\s*PM\s*GMT\+9/);
      await expect(past).toContainText('Shibuya');
    });

    test('a board member adds an event with its time zone', async ({ page }) => {
      const portal = await mockPortal(page, { me: BOARD_ME });
      await page.goto('/alumni?view=events');
      await page.getByRole('button', { name: 'New event' }).click();
      await page.getByLabel('Title').fill('Founders panel');
      await page.getByLabel('Date and time').fill('2030-12-04T18:00');
      await expect(page.getByLabel('Time zone')).toHaveValue('Europe/Paris');
      await page.getByLabel('Time zone').selectOption('America/New_York');
      await page.getByLabel('Place').fill('Yale Club of New York');
      await page.getByRole('textbox', { name: 'Event description' }).fill('Three founders talk.');
      await page.getByRole('button', { name: 'Add event' }).click();
      await expect(page.getByText('Event added.')).toBeVisible();
      const body = portal.find('POST', '/api/alumni/events')[0].body as Record<string, unknown>;
      expect(body).toMatchObject({ title: 'Founders panel', starts_local: '2030-12-04T18:00', timezone: 'America/New_York', place: 'Yale Club of New York', online_url: null });
      expect(String(body.description_html)).toContain('Three founders talk.');
    });

    test('an event needs a place or an online link, and the link must be https', async ({ page }) => {
      const portal = await mockPortal(page, { me: BOARD_ME });
      await page.goto('/alumni?view=events');
      await page.getByRole('button', { name: 'New event' }).click();
      await page.getByLabel('Title').fill('Founders panel');
      await page.getByLabel('Date and time').fill('2030-12-04T18:00');
      await page.getByRole('button', { name: 'Add event' }).click();
      await expect(page.getByRole('alert')).toContainText('place, an online link');
      await page.getByLabel('Online link').fill('http://insecure.example.org');
      await page.getByRole('button', { name: 'Add event' }).click();
      await expect(page.getByRole('alert')).toContainText('https://');
      expect(portal.find('POST', '/api/alumni/events')).toHaveLength(0);
    });
  });

  test('ordinary alumni get no event controls', async ({ page }) => {
    await mockPortal(page);
    await page.goto('/alumni?view=events');
    await expect(page.getByRole('article', { name: 'Winter mixer' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'New event' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Edit/ })).toHaveCount(0);
  });
});

test('Admin → Alumni: a club admin makes an account a board member', async ({ page }) => {
  const admin = { id: 1, email: 'admin@yale.edu', name: 'Admin', role: 'admin', is_active: 1, apps: ['core'] };
  let board = false;
  const bodies: unknown[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { authenticated: true, user: admin } });
    if (path === '/api/admin/users') return route.fulfill({ json: [admin] });
    if (path === '/api/admin/alumni') {
      return route.fulfill({ json: {
        domains: [{ domain: 'aya.yale.edu', added_at: NOW }], allowlist: [],
        accounts: [{ id: 9, email: 'robin.board@example.org', name: 'Robin Board', created_at: NOW, last_sign_in_at: NOW, revoked_at: null, allowed: true, board }],
        sign_ins: [],
      } });
    }
    if (path === '/api/admin/alumni/accounts/9/board' && request.method() === 'PUT') {
      const payload = request.postDataJSON();
      bodies.push(payload);
      board = payload.board;
      return route.fulfill({ json: { board } });
    }
    if (path === '/api/admin/alumni/posts') return route.fulfill({ json: { items: [], total: 0 } });
    return route.fulfill({ json: [] });
  });
  await page.goto('/admin?view=alumni');
  await expect(page.getByText(/Alumni see no club data/)).toBeVisible();
  await expect(page.getByText('Board member', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Make board member' }).click();
  await expect.poll(() => bodies).toEqual([{ board: true }]);
  await expect(page.getByText('Board member', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Remove from board' })).toBeVisible();

  await page.getByRole('tab', { name: 'News' }).click();
  await expect(page).toHaveURL(/section=news/);
  await expect(page.getByRole('button', { name: 'New post' })).toBeVisible();
});
