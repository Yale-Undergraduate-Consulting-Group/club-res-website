# Platform: several apps on one base

The club's tools run as apps on one platform: one container and one SQLite
warehouse per environment, one sign-in, one look, one delivery pipeline
(`feature/<user> → integration → dev → prod`) and one AWS layout. A new tool
is a new app on this platform, not a new deployment.

## Decisions (2026-11-07)

| Question | Decision |
|---|---|
| First new app | None yet. Build the platform first; the template is proven on the first app chosen later. |
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
| Browser | an `AppModule` in `frontend/src/modules/index.tsx` | its page routes and menu entries (`adminOnly` for admin pages) |

`backend/main.py` mounts every app listed by `app.modules.all_modules()`;
`App.tsx` mounts every route in `MODULES`; `lib/navConfig.ts` takes its items
from the manifests and only decides where each sits (top level, a dropdown,
the mobile bar).

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

## Apps today

| App | Server prefixes | Pages | Jobs |
|---|---|---|---|
| `core` | `/api/auth`, `/api/settings`, `/api/workspace`, `/api/activity`, `/api/admin` (+ `/invitations`, `/operations`), `/api/attachments`, `/api/telemetry`, `/api/slack` | Home, Profile, Admin | — |
| `outreach` | `/api/contacts`, `/api/emails`, `/api/campaigns`, `/api/analytics`, `/api/outreach`, `/api/track`, `/api/yucgoutreach`, `/api/yucg` (+ `/releases`) | Find contacts, Drafts, Pipeline, Analytics, campaign detail | discovery queue, catalog builder, register ingest and derivation, roster jobs, follow-ups, digests, reply sync, campaign drain |
| `projects` | `/api/projects` | (inside Admin) | — |

`/api/health` and `/api/ai/models` belong to the server itself.

## Steps

1. **App manifests and registry** — done. The server and the browser are
   built from manifests. Verified: all 183 API paths and all 213 path/method
   pairs resolve to the same handlers as before, and the same 12 production
   and 8 beta jobs are scheduled.
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
   serving. Versions run 1, 2, 3 per app (`test_modules.py`).
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
