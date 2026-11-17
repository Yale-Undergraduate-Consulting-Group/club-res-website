# Platform: several apps on one base

The club's tools run as apps on one platform: one container and one SQLite
warehouse per environment, one sign-in, one look, one delivery pipeline
(`feature/<user> → integration → dev → prod`) and one AWS layout. A new tool
is a new app on this platform, not a new deployment.

## Decisions (2026-11-07)

| Question | Decision |
|---|---|
| First new app | Club Resources: the OneDrive-backed, access-controlled library. |
| Domain | None for now. Apps share the CloudFront address by path (`/scraper`, `/projects`, …). A domain can be added later without code changes. |
| Outside users | Clients may use an app later. A client-facing app gets its own reviewed design, likely its own deployment with a hard data boundary (AGENTS.md: client authorization server-side on every read, write, download and job). Nothing in the platform steps exposes data to clients. |
| Default app access | All active members; an admin can restrict an app to a group. |
| Rollout | One step at a time; each step is shipped to prod and verified before the next. |

## What an app is

An app has a manifest on each side. Its URL paths, page routes, menu entries
and jobs must not collide with another app's.

| Side | Manifest | Declares |
|---|---|---|
| Server | `backend/app/modules/<app>.py` → `MODULE` | its routers (`Route`: prefix, tag, `signed_in`), scheduled jobs (`Job`: id, schedule, `sends_mail`, `first_run_after`) and startup hooks |
| Browser | an `AppModule` in `frontend/src/modules/index.tsx` | its page routes, menu entries (`adminOnly` for admin pages; the first is the app's home) and `icon` |

`backend/main.py` mounts every app listed by `app.modules.all_modules()`;
`App.tsx` mounts every route in `MODULES`; `lib/navConfig.ts` takes the menu
entries from the manifests and works out which app a page belongs to, so
each app's bar holds Home and its own pages.

Rules every manifest keeps, enforced by tests:

- `backend/tests/test_modules.py`: unique app ids, path prefixes and job ids;
  every prefix under `/api/`; the URLs outside services call never move
  (`/api/track` — the open-tracking pixel in sent mail; `/api/slack` — Slack's
  signed events and commands; `/api/auth` — Google's OAuth callback); every
  members-only router answers 401 without a session on every route; jobs that
  send or read members' mail are never scheduled in beta.
- `frontend/src/lib/navConfig.test.ts`: unique app ids, paths and menu ids, and
  every menu link opens a page an app serves.

## Platform core

Shared code an app uses rather than copies. It stays where it is; moving it
into a `platform/` folder would rewrite imports in about 160 files for no
change in behaviour.

| Concern | Where |
|---|---|
| Database connections and schema | `backend/app/database.py` |
| Sign-in, roles | `backend/app/auth_deps.py` |
| One job runner across a blue/green overlap | `backend/app/services/scheduler_lease.py` |
| Settings | `backend/app/services/settings_service.py` |
| AI (Bedrock) | `backend/app/services/llm.py` |
| Gmail (member's own account and name) | `backend/app/services/gmail_api.py` |
| UI kit and styles | `frontend/src/components/ui/Primitives.tsx`; styles in `frontend/src/styles/` (below) |
| App shell and navigation | `frontend/src/components/shell/`, `frontend/src/lib/navConfig.ts` |

## Portal

The site is "YUCG Resources". Sign-in (`LoginPage`) is the logo, the name and
the sign-in button. Home (`/`, `pages/Hub.tsx`, core) fills the page with no
bar: "Welcome, <first name>" (a member whose Google name is missing or only
their address gets the first part of the address, capitalised), a centred
tile per app the member may use (every app but core) with its icon and
title, and Profile, Admin (admins) and Log out. A tile opens the app's first
menu entry; Outreach's is Overview at `/campaigns`. Every other page shows
the bar of the app it belongs to (`appForPath`, `appBarItems` in
`lib/navConfig.ts`): the logo (back to Home), the app's icon and name, Home
and that app's pages; Profile and Admin use the core bar. The bar slides in
when an app opens and stays put between one app's pages; reduced motion
turns that off. The `icon` is a line drawing in `components/AppIcons.tsx`
(24-unit grid, `currentColor`, 1.6 stroke); an app without one shows its
initial.

Join by QR code (Admin → Members; `routers/invitations.py`, join sessions, migration
`core:3`): an admin opens a session, valid at most four hours and for at most a set
number of new members, and shows its QR code. The code is the ordinary Google sign-in
link with a join token (only its hash is stored). While the session is open, a
verified @yale.edu account that signs in through it becomes a `standard` member at
once; deactivated members stay out and existing members just sign in. Closing it
stops it at once, even for a sign-in already under way. Anyone who sees the code
while it is open can join, so close it when the room is done.

Alumni (`routers/alumni.py` and `routers/alumni_portal.py`, migrations `core:4`
and `core:7`; sign-in design in [night/security.md](night/security.md), portal
data rules in [product/ALUMNI-PORTAL.md](product/ALUMNI-PORTAL.md)): sign-in on
YUCG Connect (`/login`, the members' Google sign-in), open to a verified address
an admin allow-listed or in a domain an admin listed (Admin → Alumni; both lists
start empty) when it is not a member; a member also on the lists gets both
sessions and a "Switch to alumni" link. Alumni are not members: they live in
`alumni_accounts`, never in `users`, and their session is the `yucg_alumni`
cookie (path `/api/alumni`) holding a token the member session check refuses.
`/alumni` is the alumni portal: news posts, events, an opt-in directory and a
contact form to the alumni board. Alumni get no club data (no Resources,
projects, members or clients).
Club admins mark alumni board members; the board and club admins (through
`/api/admin/alumni`) write posts and events and read the board inbox. Every
alumni request re-checks the lists, so a removal or revocation applies at once.
`backend/tests/test_alumni.py` checks every route the server serves: an alumni
token gets nothing a request without a session would not.
`backend/tests/test_alumni_portal.py` checks every portal route for anonymous,
member, club admin, alumnus and board member.

## Apps today

| App | Server prefixes | Pages | Jobs |
|---|---|---|---|
| `core` | `/api/auth`, `/api/settings`, `/api/workspace`, `/api/activity`, `/api/admin` (+ `/invitations`, `/operations`, `/apps`, `/alumni`), `/api/attachments`, `/api/telemetry`, `/api/slack`, `/api/alumni` | Home (the hub), Profile, Admin; Alumni sign-in and portal (outside the member shell) | daily Cost Explorer cache |
| `outreach` | `/api/contacts`, `/api/emails`, `/api/campaigns`, `/api/analytics`, `/api/outreach`, `/api/track`, `/api/yucgoutreach`, `/api/yucg` (+ `/releases`) | Overview (`/campaigns`), Find contacts, Drafts, Pipeline, Directory, Analytics, campaign detail | discovery queue, catalog builder, register ingest and derivation, roster jobs, follow-ups, digests, reply sync, campaign drain |
| `surveys` | `/api/surveys` | Survey builder | — |
| `sentiment` | `/api/sentiment` | Market sentiment (Interviews, Reddit, Google reviews) | stale-run reaper (fails runs left `running` by a restart) |
| `resources` | `/api/resources` | Resources | — |

`/api/health` belongs to the server itself.

### Resources (`resources`)

The club's library of past projects, slide templates, interview and survey
guides and analytics resources; data rules and decisions in
[product/RESOURCES-PLAN.md](product/RESOURCES-PLAN.md). Files are register
documents (`workspace_documents`, owned by the importing admin, private);
`routers/resources.py` and `services/resources.py` are the only readers, and
`services/resources.py` is the one place the visibility rules live: the page,
the visibility-safe facet counts, search and the Slack tool
(`search_resources`) all call it. Search is two SQLite FTS5 indexes, metadata
and file text; file text only matches for a member who may open the files. A
statement of work (file name contains SOW or "statement of work") is
admin-only, is never text-indexed, and is not mentioned to other members.
Revenue and client contacts are in `resource_sensitive`, read only on the
admin branch. Imported SharePoint links are stored from the host on
(`/:f:/r/sites/...`) and resolve only when `SHAREPOINT_BASE_URL` is set; an
admin-entered link must be https on exactly that host or on Google Drive.
Links follow the same access rule as files.

`python backend/scripts/import_resources.py <zip> [--dry-run]` imports the
OneDrive export (admin-run, idempotent), including the table's hyperlinks
without overwriting a link an admin edited. Files go to `DOCUMENTS_BUCKET`;
with no bucket and `APP_ENV` unset, local or beta they go to
`DOCUMENTS_LOCAL_DIR` (default `/tmp/yucg-documents`), and production without
a bucket refuses to import. A "Public Disclosure?" answer that is not a clear
yes is private; a caveat steps the answer down one level.

### Market sentiment (`sentiment`)

Each analysis is a queued run in `sentiment_runs`, owned by the member who
started it (other members get 404 on read, CSV and delete). The model is
`app/services/llm.py` only, in batches on the ranking model, charged to the
member (`purpose='sentiment'`); a run is capped at 1,500 scored items and a
member at two active runs. Uploaded transcripts are reduced to their sentences
when the request is made and that text is cleared when the run ends. Optional
settings, each turning one tab on (without them the tab says so and the API
answers 503):

| Setting | Turns on |
|---|---|
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | Reddit tab (official API, app-only OAuth) |
| `SERPAPI_KEY` | Google reviews tab (SerpAPI, paid per search) |

### Survey builder (`surveys`)

From the `yucg-survey-agent` prototype. Drafting runs on
the club model through `llm.py`, charged to the member. The past-survey
library and Create in Qualtrics use the club's one Qualtrics OAuth client
(`QUALTRICS_CLIENT_ID`, `QUALTRICS_CLIENT_SECRET`; optional): drafts land in
that account, and `survey_library` holds outlines (question wording and
choices, no responses) of every survey it can see. Those outlines can carry
client wording and are readable by everyone with the app, so before the
first refresh an admin lists confidential surveys in `SURVEY_LIBRARY_SKIP`
or restricts the app; a listed survey is never downloaded and is hidden at
once if already copied. A refresh replaces the library: surveys no longer
visible to the client leave it. Dev runs without the client by decision
(no client surveys copied into dev, no drafts created from dev); the page
says Qualtrics is not configured. Conversations are not stored.

## Steps

1. **App manifests and registry** — done. The server and the browser are
   built from manifests. The Cost Explorer cache adds one leader-only daily
   core job; mail-reading and mail-sending jobs remain excluded from beta.
2. **Versioned migrations** — done. `app.database._baseline_schema` is the
   frozen baseline: it still runs on every start (it also promotes the first
   member to admin when there is none), and nothing new is added to it. Every
   schema change after it is a numbered `Migration` in its app's manifest,
   applied once by `app/migrations.py` and recorded in `schema_migrations`.
   `app.database.init_db` runs the baseline and then the migrations, so the
   server, scripts and tests reach the same schema. Rules: a
   migration only adds (tables, nullable or defaulted columns, indexes) so the
   previous release keeps working during the blue/green overlap; it is quick,
   because it holds the write lock and the running release waits at most five
   seconds (big backfills are batched leader-only jobs); it uses `execute`,
   never `commit` or `executescript`. A failure applies nothing, records
   nothing, and stops the new container from starting, so the old one keeps
   serving. Versions are contiguous from 1 for each app (`test_modules.py`);
   `core:4` adds the one-row Cost Explorer cache used by Admin → Costs.
3. **Access per app** — done. Every app is open to all active members until an
   admin restricts it (Admin → Apps); a restricted app is open to the members
   listed for it and to admins. Core (sign-in, profile, admin) and the
   catalog builder's `system` account are never restricted. `app/app_access.py`
   holds the rule (tables `app_access` and `app_members`, migration `core:1`).
   The registry adds the check to every route of every app except core, so a
   signed-in member without access gets 403; a request with no session (the
   tracking pixel, Slack's signed requests) keeps its own check. Jobs that act
   for a member check it next to the inactive-member check: campaign sending
   (the campaign then needs attention), follow-ups, queued searches
   (cancelled), reply sync, and the Outreach part of the daily digest.
   `/api/auth/me` returns the member's apps; the menu shows only those.
4. **Settings each app needs** — done. Each manifest lists the environment
   variables that matter (`Setting`: name, purpose, `required`). Production
   (`APP_ENV=production`, set by `ops/restart-yucg.sh`) does not start without
   a required setting; the deploy's health check then fails and the previous
   release keeps serving. Dev (`beta`) only logs the missing ones, because it
   runs without some on purpose: its Google keys are empty (no sign-in on dev).
   A first version refused in dev too, and the 2026-11-08 dev deploy stopped
   there before prod, so a gap in prod's settings is caught only at prod's own
   deploy (safely: blue/green keeps the old release). Local development sets
   no `APP_ENV` and needs no cloud settings. Required: `JWT_SECRET`,
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `BACKEND_URL`, `FRONTEND_URL`,
   all set in prod (checked 2026-11-08). Optional settings each turn one
   feature on. Admin → Apps shows each app's settings on that server, by name
   only; values never leave the server. Also found then: dev lacks
   `MONID_API_KEY`, `FIRECRAWL_URL`, `COMPANIES_HOUSE_API_KEY` and
   `SEC_CONTACT_EMAIL`, so Find people cannot search there; `APIFY_API_TOKEN`
   is stored in both environments and read by no code.
5. **UI kit and new-app template** — done. `frontend/src/index.css` only loads
   the fonts, Tailwind and, in cascade order, `styles/tokens.css` (theme and
   colour variables), `styles/shell.css` (brand, forms, preferences,
   navigation, page chrome), `styles/kit.css` (the classes behind
   `Primitives.tsx` and the shared opening motion) and one file per app under
   `styles/apps/` (`outreach.css`: Email Studio and Find people). The split
   kept every rule; the rules whose order changed were checked pair by pair
   and none can apply to the same element. `scripts/new_module.py` creates a
   new app (below).

## Adding an app

```sh
python3 scripts/new_module.py <id> "<Title>" "<what it is for>"
```

It writes a working app, open to every member until an admin restricts it:
a manifest with a signed-in router at `/api/<id>` and migration 1 (a table of
each member's own items), the page at `/<id>` with its header entry, a smoke
test (`backend/tests/test_<id>.py`: sign-in, each member sees only their own
items, access), and its row in the table above. It refuses an id, URL or file
that is taken, and writes nothing then. Then:

1. Replace the item list with the app's real data: new tables are new
   migrations (`Migration(2, …)`), never edits to an applied one. Keep the
   smoke test's access checks.
2. Settings it reads go in the manifest's `settings`; scheduled work in
   `jobs` (mark mail jobs `sends_mail`). Styles the kit lacks go in
   `frontend/src/styles/apps/<id>.css`, imported last in `index.css`.
3. A client-facing app does not belong here: it needs its own reviewed design
   and data boundary.
4. Run `backend/tests/test_<id>.py`, `test_modules.py`, and the frontend
   unit tests and lint; `backend/tests/test_new_module.py` keeps the
   generator itself working.
