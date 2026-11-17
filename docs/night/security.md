# Night report: alumni sign-in and security review (2026-11-16/17)

Branch `night/security`. Nothing was pushed, deployed, or applied; no AWS or GitHub
state changed. Monthly $ impact of everything committed: **$0**.

## 1. Alumni sign-in: design and data-store spec

Written before the code, as AGENTS.md requires for a new data store.

### Who may sign in

An alumnus signs in on a separate page, `/alumni/login`, with Google. No password
is stored. The server accepts the Google account only if:

1. Google reports the address as verified (`verified_email: true`) and gives an
   account id; and
2. the address is on the admin-managed **allow-list**, or its domain (exact match,
   no subdomains) is on the admin-managed **domain list**; and
3. the alumni account for that address is not revoked, and if it has signed in
   before, the Google account id is the same one.

Both lists start **empty**: the feature lets no one in until an admin adds a domain
or an address (decision for the president, below). `yale.edu` cannot be added as an
alumni domain: it would make every student an "alumnus". A single `@yale.edu`
address can be allow-listed (recent graduates keep it for a year).

**Yale alumni domain.** Yale gives alumni a lifelong YaleMail address at
`@aya.yale.edu`, hosted on Google Apps for Education, so those accounts sign in with
Google directly. Sources: Yale Alumni Association, "Connect Online"
(<https://alumni.yale.edu/connect/connect-online>): "All alumni, post-doctoral
fellows, and designated affiliates are eligible for YaleMail ... your YaleMail
@aya.yale.edu account"; Yale Student Technology Collaborative, "Graduating Students"
(<https://studenttechnology.yale.edu/graduatingstudents>): "These accounts offer
alumni a life-long @aya.yale.edu email address", and the `@yale.edu` account is
deactivated about one year after graduation. Caveat: `aya.yale.edu` is not only
alumni of this club, nor only alumni (post-docs and designated affiliates qualify
too). Adding the domain therefore opens the alumni area to every YaleMail holder;
the allow-list is the narrow option.

**Magic link: not built.** It needs the server to send mail on its own behalf. The
app has no sending identity of its own (it sends only through members' own Gmail),
no domain to verify with SES, and SES would be a new paid service plus sandbox exit
and bounce handling. A magic link also puts a bearer credential in a mailbox.
Every YaleMail account is a Google account, and any other address can be made a
Google account, so Google sign-in covers the eligible people at $0.

### What an alumnus may do (least privilege)

| Surface | Alumni |
|---|---|
| `/alumni` area: own name/email, sign out | yes |
| Resources: past projects whose disclosure is `public` (the client agreed), their non-admin-only files | yes, read and download only |
| Resources: guides, templates, `name_only`/`private` projects, statements of work, revenue, client contacts, team names, SharePoint/Drive links, Ask about this file (AI) | no |
| Every member API (outreach, contacts, Directory, campaigns, analytics, surveys, sentiment, projects, workspace, attachments, profile, team, settings, admin, Slack) | no: the alumni session is not a member session anywhere |
| Admin | no |

If an admin restricts the Resources app to listed members (Admin → Apps), alumni
see no resources either.

### Enforcement (server side, at the shared boundary)

- Alumni are **not rows in `users`**. Every member path (`auth_deps._resolve_user`,
  jobs, digests, Slack, Directory, `/api/auth/team`, exports) reads `users`, so none
  of them can ever see or act for an alumnus.
- The alumni session is a separate cookie, `yucg_alumni` (HttpOnly, SameSite=Lax,
  Secure on https, path `/api/alumni`, 24 hours), holding a JWT with audience
  `yucg-alumni`. The member decoder (`jwt_utils.decode_token`) rejects any token with
  an audience, so the alumni token is refused as a member session even if presented
  as `yucg_session` or `Authorization: Bearer` — also by the previous release during
  a blue/green overlap, because PyJWT rejects an unexpected `aud` there too.
- `/api/alumni/*` reads only that cookie and re-checks the account on **every
  request**: not revoked, not deleted, and still allowed by the current lists.
  Removing a domain or address, or revoking the account, ends access at the next
  request; no session list to purge.
- A test builds the route list from the app's OpenAPI schema and asserts, for every
  method and path, that an alumni token (as alumni cookie, as member cookie, as
  Bearer) gets exactly what an anonymous request gets, except the `/api/alumni`
  routes; and that member sessions get nothing from `/api/alumni`.

### Data stores (migration `core:4`, additive)

| Table | Holds | Owner | Readers | Retention and deletion |
|---|---|---|---|---|
| `alumni_domains` | domain, added_by (member id), added_at | club admins | admins (Admin → Alumni) | until an admin removes it |
| `alumni_allowlist` | email, optional note (≤200 chars, e.g. "Class of 2022, ex-president"), added_by, added_at | club admins | admins | until an admin removes it |
| `alumni_accounts` | email, Google account id, Google display name, created_at, last_sign_in_at, revoked_at, revoked_by | club admins | admins; the alumnus reads only their own email and name | until an admin deletes it. Revoke keeps the row (so the person stays out even if their domain is allowed); delete removes the row and its sign-in records |
| `alumni_sign_ins` | time, account id (when known), **domain only** of the address, outcome (`signed_in`, `not_eligible`, `revoked`, `identity_mismatch`) | club admins | admins | rows older than 365 days are deleted at each new sign-in; deleted with the account |

Not stored: Google access or refresh tokens, profile pictures, passwords, IP
addresses, the full address of a refused sign-in (only its domain, so strangers who
try are not recorded by name).

- **Backups.** The tables live in the same SQLite file, so they are in the daily
  encrypted snapshot to the backups bucket (30-day expiry) and the seven daily EBS
  snapshots. A deleted row can remain in those copies for up to 30 days; that is the
  deletion limit to tell an alumnus who asks to be forgotten.
- **Audit evidence.** Sign-ins and refusals: `alumni_sign_ins` (Admin → Alumni shows
  the latest 200). Admin changes (domain/address added or removed, account revoked,
  restored, deleted) and every alumni file download (`alumni_resource_download`,
  with the alumni account id) go to the existing `audit_log`, shown in Admin →
  Activity.
- **Client data.** Alumni read only resources a client agreed to disclose; the
  resource rules stay in `services/resources.py`, the one place they live.
- **Cost.** No new service: one SQLite migration and pages in the existing
  container. $0/month.

### Decisions for the president (alumni)

1. Add `aya.yale.edu` to the domain list (every YaleMail holder: alumni, post-docs,
   affiliates), or allow-list named alumni only? Default shipped: neither, the
   feature is dormant.
2. Alumni see only `public` past projects. Widen to guides and templates? (They are
   internal club material; not done by default.)
3. Alumni sessions last 24 hours (members: 7 days). Fine as is?

### What was built (commit on `night/security`)

| Piece | Where |
|---|---|
| Tables, sign-in rule, per-request check, alumni API, admin API | `backend/app/routers/alumni.py`, migration `core:4` in `app/modules/core.py` |
| Google start `/api/auth/alumni/google`; the shared callback branches on the challenge's purpose before the `@yale.edu` rule | `backend/app/routers/auth.py` |
| Alumni token, cookie, audience | `backend/app/jwt_utils.py` |
| What alumni may read (`is_alumni`; keys absent, not empty) | `backend/app/services/resources.py` |
| Pages `/alumni/login`, `/alumni`; Admin → Alumni | `frontend/src/pages/AlumniLogin.tsx`, `AlumniHome.tsx`, `components/admin/AlumniAccess.tsx` |
| Tests: sign-in rules, read rules with an admin positive control, revocation, denial matrix | `backend/tests/test_alumni.py` |

Before an admin adds anything, nothing changes for anyone: members' sign-in, pages
and APIs are untouched (the alumni 401 never signs a member out of the UI).

## 2. Security review of the whole app

Read-only analysis first (two read-only scouts over `backend/`, `frontend/`,
`terraform/`, `ops/`, `docker/`, `.github/`, then my own checks), then fixes for what
was clearly safe. Every fix has a regression test with the forbidden and the allowed
case (`backend/tests/test_security_fixes.py`, `tests/test_backup_restore.py`). No
fix adds a step to a member's normal use.

### Fixed in code ($0/month each)

| # | Finding (severity) | Fix |
|---|---|---|
| F1 | **API keys ignored their scope (high).** A key is created with scope `contacts:read`, but `auth_deps._resolve_user` returned the owner's full role, so an admin's "read-only" key could call every admin and write route, and it skips two-factor. | `auth_deps.key_allows`: a key opens only what its scope names (`contacts:read` = GET/HEAD under `/api/contacts`), else 403; `get_current_admin` never accepts a key. Browser sessions unchanged. |
| F2 | **Development sign-in was guarded only by the client's `Host` header (high if `DEV_LOGIN_EMAIL` were ever set on a server).** It creates an admin. | `_dev_login_email` returns nothing when `APP_ENV=production`; local and dev-box use unchanged. |
| F3 | **Two-factor code could be guessed without limit (medium)** for 15 minutes per pending sign-in, and a pending token could be replayed. | Pending token carries a `jti`; five wrong codes end it, a success consumes it (`two_factor_attempts`, migration `core:5`). A typo still works. |
| F4 | **Attachment upload read the whole body before the 25 MB check (medium)**: nginx allows 100 MB, so a few concurrent uploads could exhaust the 1 GiB container. | Reads at most limit + 1 byte; oversize answers 413. |
| F5 | **Slack's unauthenticated routes read the whole body before checking the signature (medium, found in my pass).** Anyone could make the server hold up to 100 MB per request. | Bodies over 256 KB are refused with 413 before reading; signature check unchanged. |
| F6 | **Blind SSRF in Find people's website crawl (medium).** A member-supplied domain was crawled with redirects followed and no address check, so a site could redirect into the VPC or the metadata service. | `research_providers.refuse_private_destination` runs on every hop (redirects included) and refuses non-public URLs and hosts that resolve to private, loopback or link-local addresses. Limit, stated in code: it resolves before httpx connects, so DNS rebinding remains possible; the page-text fetcher already pins addresses. |
| F7 | Google token-exchange failures logged up to 500 bytes of Google's response (low). | Logs status and Google's error code only. |
| F8 | Slack "not configured" error showed every member which Slack secrets the server has (low). | Generic message. |
| F9 | **`ops/restore-drill.sh` reported success when the restored app never became healthy (medium, process).** | Exits 1 with the container's last log lines. |

Restore test: `tests/test_backup_restore.py` runs `ops/backup_sqlite.py` offline on a
synthetic database while a WAL writer holds an un-checkpointed commit, and proves the
snapshot matches its checksum, is mode 0600, passes `integrity_check`, restores every
committed row, and never overwrites an existing file. The S3 half
(`ops/restore-drill.sh`) needs the box; last run on prod 2026-11-09 (docs/CI_CD.md).

### Checked and found sound (no change)

- Ownership on every id route sampled (campaigns, contacts, drafts, releases, workspace
  documents and shares, attachments, sentiment runs, resources); roles are re-read from
  SQLite on every request, inactive and `system` accounts never resolve.
- Session cookie: HttpOnly, SameSite=Lax, Secure on https, HS256 pinned. CORS: explicit
  origin list with credentials. CSRF: SameSite=Lax plus JSON bodies; the GET routes that
  write only refresh a cache, record an audit row or complete OAuth with a
  browser-bound state.
- OAuth state is single-use, browser-bound and expires in 10 minutes; Slack requests
  are HMAC-verified with a timestamp window.
- Access log drops query strings and redacts share tokens; OAuth tokens are encrypted
  at rest.
- No SQL built from user values; local file paths are contained.
- Response headers (`terraform/cloudfront.tf`): HSTS (1 year), `X-Frame-Options: DENY`,
  nosniff, referrer policy and an enforced CSP (`script-src 'self'`, no inline
  scripts in the build).
- S3 (`terraform/storage.tf`, `main.tf`): every bucket blocks public access, enforces
  bucket-owner ownership, versioning and TLS-only; data buckets use the per-environment
  KMS key; the site bucket is OAC-only. Backups: SQLite online-backup API, integrity
  check, SHA-256 manifest, KMS-encrypted bucket, 30-day expiry; plus seven daily EBS
  snapshots.
- Dependency gates: CI runs Trivy fs (secret, misconfig), Trivy image, pip-audit,
  Bandit and the npm audit gate; actions are SHA-pinned.

### Needs approval (infra or process), not changed

| # | Finding | Proposal | $/month |
|---|---|---|---|
| P1 | No `Permissions-Policy` or `Cross-Origin-Opener-Policy`; CSP allows images from any https host. | Add the two headers to the existing response-headers policy (snippet below). Separately, narrow `img-src` to `'self' data: blob: https://lh3.googleusercontent.com` once a browser pass over every app shows nothing else loads. | $0 (response-headers policies are free) |
| P2 | The box role's `ReadWriteDataObjects` statement (`terraform/compute.tf`) grants `s3:DeleteObject` on every data bucket, backups included, so a compromised app could hide backups behind delete markers. | Split the statement so the backups bucket gets Get/Put/Abort only; `ops/backup-to-s3.sh` only runs `aws s3 cp` (PutObject) and lifecycle expires old sets. | $0 |
| P3 | Container runs non-root with a memory cap but a writable root filesystem and full default capabilities (`ops/restart-yucg.sh`). | `--read-only --tmpfs /tmp:size=256m --cap-drop ALL --security-opt no-new-privileges --pids-limit 256`, after a smoke test of imports and uploads (they spool to `/tmp`). | $0 |
| P4 | `JWT_SECRET` signs sessions, derives the key for stored Gmail/Slack tokens and signs file links; any length is accepted. | Separate `TOKEN_ENCRYPTION_KEY` with re-encryption; a maintainer checks the prod secret is ≥ 32 random bytes before a startup check is added (a short one would stop the deploy). | $0 |
| P5 | Logout only deletes the cookie: a copied session works for its 7 days. | Add a session id and a revocation list checked per request, or "sign out everywhere" via a per-user version. Choose which. | $0 |
| P6 | `npm_audit_gate.sh` passes when the registry is unreachable. | Fail closed after retries, with a maintainer override. Can block a release during an npm outage. | $0 |
| P7 | The legacy `/api/track/open/{id}` pixel takes sequential ids, so opens can be forged (analytics only). | Remove once mail sent before the token pixel is older than the follow-up window. | $0 |
| P8 | `scripts/bootstrap-account.sh` skips its controls when a bucket already exists. | Always re-apply public-access block, encryption, versioning; operator reruns it. | $0 |
| P9 | No object-level CloudTrail for the documents and backups buckets; WAF has metrics only. | S3 data events for those two buckets (a few hundred events a month); WAF logs with header redaction, 14-day retention. | about $0.05 [INFERENCE: club traffic] |
| P10 | Leftover secret `test/yucg-outreach` (listed in the facts file); nothing in this repository names it. | Delete after confirming no consumer outside the repo. | −$0.40 |

Proposed header addition (P1), for `aws_cloudfront_response_headers_policy.site`; not applied:

```hcl
  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
      override = true
    }
    items {
      header   = "Cross-Origin-Opener-Policy"
      value    = "same-origin-allow-popups"
      override = true
    }
  }
```

## 3. Verification (run 2026-11-16/17, local only)

| Check | Result |
|---|---|
| Backend: all 101 `backend/tests/test_*.py` scripts, 6 parallel batches, `/tmp/club-venv` | 100 pass. `test_llm_provider.py` fails only because this fresh worktree has no default database file (`generation_policy` opens it `mode=rw`); it passes against an initialised temporary database. Not related to this change. |
| `test_alumni.py` denial matrix | 267 method/path pairs × 3 ways of presenting the alumni token = 801 checks, no gaps. Mutation check: with the audience check disabled it reports 504 gaps. |
| Root `python3 -m unittest discover -s tests` | 45 pass (includes `test_backup_restore.py`) |
| Frontend `npm run lint`, `npm run test:unit` (211), `npm run build` | pass |
| `scripts/npm_audit_gate.sh` (from `frontend/`) | no high or critical advisories |
| `trivy fs` (secret, misconfig, vuln; HIGH/CRITICAL) v0.75.0 | clean |
| `pip-audit -r backend/requirements.lock` 2.9.0, `bandit -lll -iii` 1.8.3 (temporary venv, removed) | no known vulnerabilities; no findings |
| Live run: API on :8104 and Vite on :5204, throwaway database with synthetic resources | alumni cookie: `/api/alumni/me` and `/resources` 200, local file 200 (401 without); the same token as member cookie or on `/api/auth/team`, `/api/contacts`, `/api/directory`, `/api/resources`, `/api/admin/alumni`: 401. Screens checked desktop and 390 px: sign-in with error, alumni area, Admin → Alumni (accounts list rewritten after the first mobile screenshot showed a squeezed table); an alumni session on `/` lands on member sign-in |
| Terraform fmt/validate/test | not run: `terraform` is not installed here; no Terraform file changed |

Not exercised: a real Google sign-in (Google redirects only to the registered prod
callback; the flow is tested with a faked Google), and the S3 restore drill.

## 4. Decisions for the president

1. Alumni: which domain(s) or names to allow (section 1); widen beyond public past projects?
2. API-key scope (F1) is enforced now. Any integration that used a key for more than
   reading contacts will get 403 after deploy; create a key per need once scopes are chosen.
3. Approve or decline P1–P10; all are $0 except P9 (about $0.05) and P10 (saves $0.40).

Remote state: none changed. Nothing pushed, deployed, planned or applied.
