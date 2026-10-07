# Implementation status

## Product architecture and first design slice — 2026-09-12

The current product behavior, ownership boundaries, integration status, target information architecture, language, visual tokens, components and staged experience gates are recorded in [the product system map](PRODUCT-SYSTEM-MAP.md) and [the product design system](PRODUCT-DESIGN-SYSTEM.md).

The first implementation slice groups desktop navigation into Home, Projects, Contacts, Outreach and Admin; gives mobile members direct access to Home, Projects, Contacts and Drafts; replaces misleading target-list and address-verification language; removes member-facing operator diagnostics; and consolidates the canonical surface/ink/status/radius tokens. Studio now preserves the current draft when generation fails, asks for verified facts, and sends Claude a structured untrusted brief under a grounded YUCG system instruction.

New security regressions prevent another member from manually marking a campaign recipient as replied, prevent an in-flight Gmail refresh from restoring a disconnected credential, and restrict the legacy global-token OneDrive importer to administrators and direct files returned from the configured club folder. Fresh CDK bootstrap now makes the retained data mount writable for container UID/GID `10001` and explicitly configures beta versus production delivery behavior.

Verification for this slice: backend suite passes at **34%** overall branch coverage; the draft-generation module is **94%**; all existing critical coverage thresholds pass. Frontend lint/build and Playwright pass (**45 passed, one desktop-only assertion skipped on mobile**). Infrastructure/controller/workflow tests pass (**62**). No live provider, Gmail, AWS, Terraform apply, deployment or email operation was performed.

## Current repository reconciliation — 2026-09-11

The three-workflow consolidation is committed on `develop` at `f380bc7`. The existing application work below is already in source; it is not a new implementation backlog. Eight composite actions provide scope/checks/shipping without appearing as extra workflows. Promotion invokes the trusted script directly from the default branch, without a GitHub App or new sync PRs.

The consolidation now checks out before loading every local action, includes valid action metadata, limits write tokens to the jobs that need them, and compares dispatched runs to the correct target/parent. A regression with real Git history confirms that a workflow-only dispatch does not deploy existing runtime files. Beta uses its explicit separate instance binding; main-only maintenance operations are serialized with delivery. See [current delivery behavior and guardrails](DELIVERY-GUARDRAILS.md).

Current verification: **48 infrastructure/controller/workflow tests pass**, actionlint passes, Terraform mock tests are **3/3**, and Terraform validation succeeds. The full backend suite passes with **34%** overall branch coverage; telemetry is now an 84%-covered critical module. Frontend lint/build and the browser suite pass (**45 passed, one desktop-only assertion skipped on mobile**). Frontend, infrastructure and locked Python dependency audits report no known vulnerabilities; Bandit reports no high-severity finding. Three existing SQLite test-fixture ResourceWarnings remain. Docker is unavailable locally, so the hosted image build/Trivy/runtime checks remain required. Current GitHub settings, hosted completion and the live AWS revision still require fresh verification. No AWS operation or email was performed in this continuation.

Browser telemetry now requires an authenticated active member, accepts only the bounded `page_view` event, and rejects server-reserved quota/activity names. Direct API regressions cover anonymous access, forged quota events, invalid resources, oversized details and oversized batches.

## Historical implementation and deployment evidence

The sections below record earlier implementation/tests and the September 10 deployment. Their revision IDs and live observations are historical, not a fresh inventory. Superseded App prerequisites and old branch names are not the current activation plan.

The six stages below are implemented and verified. Independent backend, frontend and infrastructure subagents reviewed and revised their respective changes. The application was deployed through the protected GitHub Actions path on 2026-09-10. No Terraform apply/import or real email delivery was performed.

## 1. Sender isolation and reliable outreach

Campaign ownership and authorized sender are persisted separately. Mutations and detailed reads enforce ownership; shared reporting cannot initiate another member’s mail. Release captures immutable recipient, content, signature and follow-up snapshots. Transactional SQLite claims prevent concurrent workers from claiming the same send. Ambiguous Gmail outcomes remain blocked until exact-message reconciliation proves delivery; they are never blindly retried. Historical ownership requires evidence and explicit administrator confirmation.

Follow-ups retain the original sender. Disabled/disconnected accounts cannot fall back to another member’s credentials. All Gmail send boundaries reject recipient lists and header-control characters. Tracking uses individual message identity, reply/bounce matching and visible sync status. An open is an observed pixel request, not proof a human read the message. Shared activity reports confirmed messages and their actual sender/recipient, without exposing message bodies or credentials.

## 2. Consistent frontend

Removed the fixed backdrop, competing main scroll container and route-entry motion. Grouped navigation, consistent titles and shared page headers now connect outreach, projects, documents and administration. Shared campaigns expose read-only summaries. Responsive document cards, loading/error states and recovery controls are present. Imported/generated rich HTML, previews, signatures and paste/drop content are sanitized with a strict DOMPurify policy.

## 3. Invitations and account admission

Persistent invitations record creator, exact email, assigned projects, expiry, revocation and delivery state. Only the creating member can send the invitation through their connected Gmail account. Uncertain delivery remains visible. Google admission requires verified identity and an existing roster entry or matching valid invitation. Browser-bound, single-use OAuth challenges prevent replay; ordinary identity login is separate from Gmail authorization. Access logs redact OAuth/invitation query parameters and shared-link tokens.

## 4. Private project/document workspace

Database metadata powers permission-filtered search, project membership and frontend views; S3 holds document bytes. Private, project and club visibility are explicit. Administrators do not implicitly gain access to private documents. Uploads reserve quota transactionally and use conditional presigned PUTs. Finalized versions pin the exact bucket and S3 version. Downloads are short-lived attachments. Owner-created bearer shares expire and can be revoked; the bucket remains private. Pending upload recovery preserves reservations when completion is uncertain.

## 5. Delivery and security gates

CI is three workflow files: **Intake**, **Beta**, and **Production**. Shared check/ship steps are composite actions, not extra workflows. Intake tests human topic PRs into `develop` and never builds an image or deploys. Beta verifies `develop` → `feature` on dispatch and only rebuilds the image on feature. Production proves every boundary on dispatch from `feature` and ships the live box on `main`. Bot PRs do not start a second `pull_request` run. A skipped required job is a failure. Promotion after a green run is automatic; `release:hold`, requested changes, or closing the PR stop the candidate. Actions are pinned and Python dependencies are hash-locked. Shipping uses the scanned image artifact and resolved digest through the ECR credential helper (no plaintext `docker login`). Deployment scripts check the mounted database, create a consistent backup, wait for readiness and support application rollback. Terraform apply and optional static publishing are Production dispatch operations.

GitHub main and feature rules now require the aggregate GitHub Actions check with no administrator bypass. Production requires owner approval and allows only main. The user deferred a second maintainer, so independent review is a future requirement. Scanners and coverage do not establish that the entire legacy codebase is secure.

## 6. AWS/Terraform preparation

Terraform provides opt-in private versioned storage, constrained role policies and a guarded CloudFront/static-origin handoff. Existing CDK ownership must be relinquished safely before adoption; no competing ownership is permitted. Static publication uploads immutable assets before replacing the index. SQLite backup/restore scripts and optional daily backup systemd units are prepared. Data-bearing storage is retained, and version-pinned documents are not expired by a blanket lifecycle rule.

The current database implementation remains a single-server SQLite deployment. Durable claims cover concurrent processes on that database; this is not a completed PostgreSQL or horizontally distributed writer implementation. Storage can grow independently in S3. Quota reservation is an application control, not an AWS spending cap.

## Final local verification

Recorded on 2026-09-10 for the then-local branch `feat/staged-delivery-and-studio`:

- Backend `scripts/test_suite.py` passes, including `test_environment_security.py`. Combined coverage is **34%**. Gated modules: invitations **91%**, workspace **85%**, dispatch claims **88%**, dispatch recovery **99%**, mailbox validation **89%**, delivery policy **100%**, generation policy **100%**, Bedrock/LLM **97%**, email verifier **95%**.
- Frontend lint and production build pass. Studio Playwright: 5 passed, 1 desktop-only viewport case skipped on mobile. Accessibility title for `/studio` is **Email studio**.
- `python3 -m unittest discover -s infra/tests` : **29** tests pass. Terraform `storage.tftest.hcl`: **3** passed. `actionlint` is clean.
- At that verification point the branch was not on main, and the recorded production revision was `26969f6`. Consult current hosted evidence before identifying today's deployed version.

Earlier hosted verification (already on main):

- Hosted Linux image scanning passed on Python 3.12.14 Alpine 3.24; non-root startup and in-image backend regressions passed.
- Native systemd validation, additional browser engines, and a full re-run of the 42-test browser suite were not repeated in this pass.

## Approval-dependent production completion

1. Verify live resource ownership, database mount, existing configuration and a restorable backup; review the exact Terraform plan and cost delta before any apply/import.
2. Reconfirm GitHub required checks, owner-approved production protection and environment-scoped AWS OIDC trust before the next approved release. Earlier OIDC failures and their resolution are recorded in the cutover history; do not reapply an obsolete trust change blindly.
3. Perform the approved CDK-to-Terraform handoff and configure storage CORS, application environment and optional static origin without replacing data-bearing resources.
4. Deploy the verified artifact, exercise readiness and rollback, and enable the backup timer only after a controlled restore rehearsal.
5. Use explicitly authorized test accounts for end-to-end invitation, Google/Gmail connection, send/open/reply/bounce and S3 permission/version/share tests. Reconcile legacy campaign ownership before resuming old outreach.

The deployed revision is `26969f6`, using ECR digest `sha256:757a65e12664017b9534a1d0c4dc59df6be99e2f1eb0f80ae82afdc064c71319`. The container runs as UID/GID 10001, the retained database passes `PRAGMA quick_check`, and CloudFront `/api/health` responds successfully. The one-time ownership variable was removed immediately after job start. Aggregate post-deploy checks found zero active campaigns and zero ready dispatches.

Gross recurring AWS resource allocation remains unchanged; no resources were created or resized. The new tagged ECR image consumes incremental layer storage, partly deduplicated, and the 393,216-byte predeploy backup consumes existing EBS capacity. Future storage versions/backups, requests, transfer, logs and CI usage remain usage-based. Credits are excluded from gross estimates.
