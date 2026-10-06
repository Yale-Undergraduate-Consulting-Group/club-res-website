# AI contact intelligence and outreach plan

## Purpose

Build an AI-native research and outreach workspace that helps a club member move from a project brief to a small, defensible set of people worth contacting. The system should discover current companies, find real employees, form plausible professional email addresses, assess whether each address can receive mail, explain the evidence, and carry accepted contacts into drafting and outreach without changing the responsible sender.

The product should feel simple even when the background process is complex. A member describes the kind of organization and person they need. The application performs bounded research, returns useful candidates, explains uncertainty, and asks the member to decide only when judgment is material.

This document is an implementation plan. It distinguishes existing behavior from proposed work and does not assert that a provider, branch, or AWS change is deployed.

## Product principles

1. **Research the person before the address.** A plausible mailbox is useful only when independent evidence connects it to a current employee and relevant role.
2. **Preserve evidence, not just conclusions.** Every recommendation records source URLs, observed text, observation time, extraction method, and any conflicting evidence.
3. **Call uncertainty what it is.** Syntax, DNS, an inferred pattern, an SMTP response, an AI review, delivery, and a reply are different facts.
4. **Use AI for interpretation.** Deterministic code handles identity, permissions, limits, deduplication, and delivery. A model extracts, compares, summarizes, ranks, and drafts within those controls.
5. **Spend only where it changes a decision.** Free local checks run first. Limited external checks apply only to the best unresolved candidate addresses.
6. **Keep people accountable.** Discovery may be shared where appropriate. Drafts, campaigns, Gmail credentials, and sending authority remain bound to their owner.
7. **Optimize for useful coverage.** The objective is enough strong contacts for the project, rather than the largest possible pile of names.
8. **Make every state recoverable.** Long-running research survives navigation, refresh, provider delays, and application restarts.

## Current baseline

The repository already contains several parts of the intended system:

- Find contacts exposes company discovery, quick scrape, person lookup, and file import.
- Company discovery uses a durable, member-owned SQLite queue with bounded club admission, leases, restart recovery, and per-member ownership.
- Discovery can combine a company website crawl, Tavily web discovery, and conditional LinkedIn/Apify results.
- The verification pipeline checks names, address structure, company alignment, email syntax, DNS/MX routing, duplicates, common junk, and model-assisted plausibility.
- SMTP recipient probing exists as a best-effort option, but the production-friendly default is MX-only and leaves mailbox existence unconfirmed.
- Verifalia has an opt-in free-tier integration in the outreach-release review path. It is not yet the shared verifier for all acquisition paths.
- Accepted contacts can enter the contact catalog, Drafts, Campaigns, Gmail delivery, and message-level tracking.
- Opens, replies, delivery delays, and failures are stored against individual outbound messages. Open pixels are approximate; Gmail replies and structured delivery notices are stronger evidence.
- The current deployment is intentionally small: one application process on EC2, transactional state in SQLite on encrypted storage, and private S3 for documents and backups.

Important gaps remain:

- Quick scrape, company discovery, person lookup, spreadsheet import, and release review do not all use one evidence contract.
- Some UI labels still collapse domain validity, mailbox confidence, person plausibility, and project fit into words such as “verified.”
- Address candidates are not represented as first-class alternatives under a person record.
- Provider results and AI conclusions need clearer source excerpts, timestamps, and conflict handling.
- Bulk company research needs one durable workflow, fair scheduling, budget reservations, resumability, and measurable stop conditions.
- Imported contacts can bypass parts of discovery verification.
- External free-tier exhaustion and provider degradation need explicit user-visible states.

## Intended member journey

### 1. Describe the research need

The member starts from a project or target list and provides a short brief:

- target industries or named companies;
- geography;
- organization size or maturity;
- desired functions and role families;
- acceptable seniority range;
- number of useful people per company;
- exclusions, such as support desks, recruiters, former employees, or agencies;
- the reason the club could credibly contact this audience.

The system converts the brief into a visible, editable research specification. It must not hide inferred filters in an AI prompt.

### 2. Recommend companies

The system searches current public information and existing club data, then recommends companies with:

- canonical company name and domain;
- current website and relevant source pages;
- industry, location, size band, and recent signals when supported;
- a concise reason the company matches the project;
- the sources and dates behind that reason;
- duplicate, subsidiary, parent, and prior-outreach warnings;
- a recommendation state: `strong match`, `possible match`, `needs review`, or `excluded`.

The member can accept companies individually or as a batch. Rejecting a company records a reason so subsequent searches do not repeatedly recommend it without new evidence.

### 3. Find people

For every accepted company, the system seeks people matching the research specification. It should inspect permitted company pages, public professional profiles, press releases, conference biographies, association directories, and configured search providers.

A person candidate requires:

- a normalized name;
- a current employer hypothesis;
- a current role hypothesis;
- at least one dated public source;
- the source excerpt supporting name, employer, and role;
- a canonical profile URL when available;
- a freshness date;
- conflict flags for former employment, inconsistent titles, or ambiguous identity.

Navigation labels, products, departments, company names, and generic inboxes must not become people.

### 4. Form address candidates

Only after person and company identity pass their deterministic gate should the system form addresses. It should:

1. Collect independently published addresses belonging to the same company domain.
2. Derive likely domain patterns from published samples.
3. Generate bounded variants for the person, including aliases required by punctuation or compound names.
4. Reject malformed local parts, role accounts, mismatched names, consumer domains, disposable domains, and unrelated company domains.
5. Rank variants by observed company pattern, publication evidence, recency, and name alignment.
6. Retain a small candidate set under one person rather than creating multiple contact records.

An inferred address must never become evidence that validates its own pattern. Pattern confidence may increase only from independently published or subsequently observed delivery evidence.

### 5. Assess mailbox availability

Verification should run in progressively more expensive tiers:

| Tier | Check | Cost expectation | Meaning |
| --- | --- | --- | --- |
| 0 | Normalize, syntax, role-address, disposable-domain, and duplicate checks | Local/free | The address is structurally plausible |
| 1 | DNS, null MX, and mail-route lookup, cached per domain | Local/free | The domain is configured to receive mail |
| 2 | Company-pattern and public-source correlation | Local/provider search already authorized | The address is supported or inferred from company evidence |
| 3 | Conservative SMTP recipient check when the verifier has suitable network access | Infrastructure cost, no message body | The server rejected, accepted, or concealed the recipient at that moment |
| 4 | Free external validation allowance, initially Verifalia | Up to configured daily free credits | An independent provider’s mailbox-risk assessment |
| 5 | Historical club evidence | No incremental provider cost | Prior delivery, permanent failure, automatic reply, human reply, or unsubscribe |

External validation must default off until its key, privacy decision, daily cap, and data-retention review are configured. The application must stop cleanly when free credits are exhausted. It must never purchase credits, upgrade a plan, or silently fall through to a paid provider.

The first external-validation policy should be:

- validate at most one top-ranked address per person;
- validate only contacts that otherwise meet person and project-fit requirements;
- cache a successful or inconclusive response for a configurable period;
- prioritize contacts closest to campaign review;
- reserve part of the daily allowance for manual rechecks;
- expose remaining daily capacity to administrators without exposing addresses;
- allow an administrator to disable the provider immediately.

Self-hosted SMTP verification can be evaluated separately. Truemail is the preferred licensing candidate for evaluation. It should not be placed on the application EC2 instance until outbound port access, reverse DNS, abuse controls, rate limits, and the effect on the host’s reputation are proven. Network failure must return `inconclusive`, never `invalid`.

### 6. Reconcile evidence with AI

The AI review receives structured evidence, not an unrestricted web prompt. Its responsibilities are to:

- decide whether several sources refer to the same person;
- identify likely current versus former employment;
- classify the role against the project brief;
- summarize why the person is worth contacting;
- identify conflicts and missing evidence;
- recommend which address candidate deserves a limited external check;
- produce a concise explanation using citations to stored sources.

The model cannot:

- declare a mailbox proven from an address pattern;
- treat its own generated text as a source;
- change ownership or visibility;
- import a contact, create a campaign, release a campaign, or send mail;
- bypass a suppression, bounce, unsubscribe, daily quota, or provider budget;
- follow instructions embedded in scraped pages, contact names, documents, or emails.

Model output must be schema validated. Unsupported citations, missing source identifiers, invalid enums, or references to evidence outside the user’s access scope fail closed into `needs review`.

### 7. Review a compact recommendation inbox

The member should see three primary queues:

- **Ready to review** — person and project fit are supported, and one address is usable with stated confidence.
- **Needs evidence** — useful person, but identity, role, employment, or mailbox evidence is incomplete or contradictory.
- **Excluded** — generic address, bad domain, wrong person, former employee, duplicate, suppression, or explicit member rejection.

Each recommendation card or table row should answer, in this order:

1. Who is this?
2. Why are they relevant to this project?
3. Where did the information come from?
4. What address is proposed?
5. Was the address published or inferred?
6. What exactly has been checked?
7. What remains uncertain?
8. Who owns the next action?

The default view should avoid raw provider terminology and model diagnostics. An evidence drawer can show the full record, history, raw status mapping, and recheck controls.

### 8. Move accepted contacts into outreach

Accepting a recommendation creates or updates one canonical contact, preserves its evidence, and records the accepting member. It should offer a direct next action to add the person to the current target list or open a member-owned draft.

Before campaign release, the system rechecks:

- contact suppression and unsubscribe state;
- permanent delivery failures;
- address freshness;
- sender ownership and Gmail connection;
- duplicate recipients within and across active campaigns;
- exact recipient, subject, body, attachment/share permissions, and signature;
- member and club sending limits.

After sending, observed events update the evidence record. A permanent recipient failure suppresses the address. A temporary delay does not. A human reply is strong evidence that the mailbox is active, but identity still depends on message and sender correlation. An open remains an approximate supporting signal.

## Truth and confidence model

Avoid one combined “verified” boolean. Store and display independent dimensions.

### Person identity

- `unreviewed`
- `plausible`
- `corroborated`
- `conflicted`
- `rejected`

### Employment

- `current_source_observed`
- `current_inferred`
- `stale`
- `former`
- `unknown`

### Address origin

- `published_by_company`
- `published_by_independent_source`
- `inferred_from_published_pattern`
- `user_supplied`
- `imported_without_evidence`

### Mailbox assessment

- `not_checked`
- `bad_syntax`
- `domain_has_no_mail_route`
- `mail_route_available`
- `provider_high_confidence`
- `provider_medium_confidence`
- `accept_all_or_risky`
- `recipient_rejected`
- `inconclusive`
- `previously_delivered`
- `human_reply_observed`
- `permanent_failure_observed`

### Project fit

- `strong`
- `possible`
- `weak`
- `excluded`

### Outreach state

- `never_contacted`
- `drafted`
- `scheduled`
- `sent`
- `delivery_delayed`
- `delivery_failed`
- `open_detected`
- `automatic_reply`
- `human_reply`
- `unsubscribed`
- `suppressed`

Every confidence result includes `checked_at`, `method`, `source_ids`, `reason`, and `expires_at` where freshness matters.

## Data model changes

Introduce or normalize the following records:

### `organizations`

Canonical company, domains, aliases, parent/subsidiary links, research metadata, current source evidence, and duplicate keys.

### `people`

Canonical name, normalized name parts, current employer hypothesis, role, location, profile references, identity state, and freshness.

### `person_evidence`

Person, source URL, source type, observed excerpt, retrieval time, content hash, facts supported, access scope, and superseded state.

### `email_candidates`

Person, company domain, address, origin, pattern identifier, rank, selected state, and creation method. Enforce a normalized unique address and prevent multiple candidates from masquerading as separate people.

### `email_checks`

Candidate, verifier, check type, normalized result, raw reason code, confidence, start/completion time, expiry, cost units, and sanitized provider request identifier. Raw provider payload retention should be minimized.

### `contact_recommendations`

Project or target-list scope, person, selected address candidate, project-fit state, explanation, evidence IDs, recommendation version, reviewer, and disposition.

### `research_jobs` and `research_tasks`

Member owner, project scope, visible research specification, status, progress counters, budget reservation, lease token, retry count, provider cursors, and error classification.

Existing contacts, discovery runs, campaigns, messages, and events should migrate incrementally. Compatibility columns may remain until every reader uses the new evidence model, then be removed deliberately.

## Scalable job architecture

Use the existing durable queue as the starting point. Split a run into bounded task types:

1. normalize research brief;
2. discover companies;
3. enrich one company;
4. discover people for one company;
5. extract and reconcile person evidence;
6. generate and locally check address candidates;
7. reserve and run external validations;
8. rank recommendations;
9. publish results to the member review inbox.

Operational rules:

- one active research run per member by default;
- a configurable club queue ceiling;
- fair scheduling so one member cannot occupy every worker;
- per-domain crawl serialization and request delay;
- provider-specific concurrency and daily limits;
- idempotency keys for every task and provider request;
- fenced leases and heartbeats for mutable jobs;
- bounded retries with retryable and terminal error classes;
- cancellation that stops new work while preserving completed evidence;
- resumable provider cursors;
- progress based on completed units, not invented elapsed-time estimates;
- caching by normalized company, domain, person, address, provider, and freshness policy;
- stop when the requested number of strong recommendations is reached;
- stop when marginal searches produce no new people or evidence;
- never run unlimited address variants or unlimited web queries.

Keep this on the single application host while queue age, memory, database lock time, task duration, and provider concurrency remain healthy. Add a separate worker or managed queue only after measurements show the current process is insufficient. Do not use S3 as a transactional query engine.

## AWS and storage plan

The small always-on server remains responsible for API requests, authorization, SQLite transactions, scheduling, and bounded workers. Private S3 holds larger immutable research artifacts and document versions.

For research evidence in S3:

- block all public access;
- use encryption at rest;
- issue short-lived, permission-checked downloads through the backend;
- use prefixes that include environment and opaque record identifiers rather than member email addresses;
- store source snapshots only when policy and source terms permit it;
- store a hash and short excerpt in the database for normal review;
- apply lifecycle deletion to temporary crawl artifacts;
- retain audit metadata after content expiry when required;
- prevent a browser from choosing arbitrary object keys;
- keep production and any future beta bucket, key, role, and distribution separate.

Terraform or the repository’s established infrastructure implementation must own any new bucket policy, encryption key, role, alarm, scheduled task, or environment variable. Application code must remain functional with external verification disabled.

## Privacy, security, and ownership

- Discovery runs, provider budgets, drafts, campaigns, Gmail credentials, and send controls are member-bound.
- Shared club contacts expose useful coordination state without exposing another member’s message body, OAuth token, private project evidence, or attachments.
- A campaign always sends through the campaign owner’s exact authorized Gmail account. No pooled fallback sender is allowed.
- Every mutating endpoint derives the actor from the authenticated session and checks object scope in the database query.
- Public tracking tokens are opaque, message-specific, non-sequential, and contain no address or credential.
- Provider API keys remain server-side and are never returned to the frontend or written to logs.
- Logs redact email local parts where full addresses are unnecessary.
- Scraped content, imported cells, provider summaries, contact fields, and documents are untrusted model input.
- URL fetching must reject private, loopback, link-local, metadata-service, and disallowed redirect targets.
- Imports enforce file type, size, row count, encoding, formula-injection protection, and consistent ownership.
- Retention and deletion rules cover source evidence, provider responses, exported workbooks, tracking events, and audit records.
- Rate limits apply by member, club, provider, domain, and network destination.
- Manual overrides require a reason and remain visible in the evidence history.

## Accessibility goals

WCAG 2.2 AA is the release baseline. Automated checks are necessary but do not replace keyboard and screen-reader review.

### Page structure

- One descriptive `h1` per page.
- Heading levels follow the visible hierarchy without skipped levels.
- A skip link moves focus to the primary content.
- Landmarks use `header`, `nav`, `main`, `aside`, and `footer` consistently.
- Repeated navigation has a stable accessible name.
- Page titles identify both the task and application, for example `Find contacts · YUCG Outreach`.
- Breadcrumbs appear only below list/detail navigation and use `aria-current="page"`.

### Forms and research briefs

- Every control has a visible label; placeholder text is supplementary.
- Required, optional, and format expectations are conveyed in text.
- Related controls use `fieldset` and `legend`.
- Help text is associated with `aria-describedby`.
- Validation runs on submit and, where useful, after leaving a field without erasing input.
- The error summary receives focus and links to each invalid control.
- Errors explain how to recover: `Enter a company name` rather than `Invalid value`.
- Autocomplete does not unexpectedly replace member-entered text.
- AI-inferred filters are displayed as editable controls before research begins.

### Keyboard and focus

- Every operation is available with keyboard alone.
- Focus order follows the visual and task order.
- Focus indicators meet contrast requirements and are never removed.
- Tabs use the ARIA tab pattern, arrow-key navigation, selected state, and associated panels.
- Dialogs and drawers trap focus, close with Escape when safe, and restore focus to their trigger.
- Route changes and completed actions move focus deliberately to the new page heading or result summary.
- Bulk-selection actions remain reachable without traversing every row.
- No drag-only interaction is permitted.

### Status and long-running work

- Starting a job confirms its name and scope without moving focus unexpectedly.
- Queued, running, paused, completed, partially completed, cancelled, and failed are distinguishable in text.
- A polite live region announces meaningful milestones, not every percentage update.
- Errors use an assertive announcement only when immediate action is required.
- Progress bars expose a label, value, and indeterminate state where total work is unknown.
- Members can leave and return to a durable job without losing progress.
- Cancellation explains what completed data will remain.
- No task relies on a short timeout; sessions warn before expiry and permit extension where appropriate.

### Results, tables, and evidence

- Desktop tables have captions, scoped headers, stable row identifiers, and a keyboard-operable details action.
- Responsive cards preserve the same field names, state, and actions as the table.
- Sorting announces column and direction.
- Filters expose active values and result count.
- Confidence is conveyed by text and structure in addition to color or icons.
- Evidence links have meaningful text such as `Company leadership page`, with source and observation date nearby.
- Tooltips are supplementary; essential evidence is available on focus and as persistent text.
- Empty, unavailable, forbidden, filtered-empty, and failed states have distinct explanations.
- Bulk actions state how many records are selected and require confirmation for consequential changes.

### Color, motion, layout, and touch

- Normal text meets 4.5:1 contrast; large text and essential graphical objects meet applicable AA thresholds.
- Status colors use the shared semantic tokens and always include labels.
- Content remains usable at 200% browser zoom and at 320 CSS pixels without two-dimensional page scrolling, except genuinely tabular content.
- Text spacing overrides do not clip or hide content.
- Target size is at least 24 by 24 CSS pixels under WCAG 2.2, with 44 by 44 as the product goal for primary touch actions.
- Animations honor `prefers-reduced-motion`.
- Loading skeletons are hidden from assistive technology and accompanied by one meaningful status.
- Sticky headers and internal scrolling do not cover focused elements.
- Dark mode preserves the same contrast and focus quality as light mode.

### Accessible writing

- Instructions do not depend on color, shape, or screen position alone.
- Abbreviations such as MX, SMTP, and DNS are expanded on first use in member-facing help.
- Dates use an unambiguous format and times include the member’s timezone.
- Counts use plain units: `12 people`, `3 addresses need review`.
- Links describe their destination; avoid `click here` and raw URLs as labels.
- Destructive and irreversible consequences are stated before confirmation.
- Provider errors are translated into a member action while preserving a support code for administrators.

### Accessibility verification

For every changed journey:

- run lint, type checks, build, and automated accessibility scans;
- test keyboard-only operation from page entry through completion;
- test VoiceOver with Safari on macOS for headings, forms, tables, live progress, dialogs, and results;
- test at least one Windows screen-reader/browser combination before production milestone completion;
- test 320px mobile width, 200% zoom, light mode, dark mode, and reduced motion;
- test loading, empty, populated, partial, failure, forbidden, stale, and credit-exhausted states;
- record known exceptions with owner, impact, workaround, and target date;
- block production for critical or serious violations in the changed path.

## Interface writing goals

### Voice

Use calm, direct, professional language. Explain what the system observed and what the member can do next. Avoid promotional claims, anthropomorphic AI narration, and internal operator language.

### Naming

- Use sentence case for headings, tabs, buttons, fields, and statuses.
- Name pages after member tasks or recognizable objects: `Find contacts`, `People`, `Drafts`, `Campaigns`, and `Results`.
- Use `Research run` instead of `agent swarm` or `pipeline execution` in ordinary UI.
- Use `Evidence review` instead of `AI verdict`.
- Use `Mail domain available` instead of `Inbox verified` for MX-only results.
- Use `Address inferred` when the system generated the address.
- Use `Mailbox confidence: high` only when a provider actually returned that level.
- Use `Open detected` instead of `Read`.
- Use `Delivery failed` with the observed reason instead of automatically asserting `Mailbox does not exist`.

### Status-copy template

Every status should answer:

1. What happened?
2. What does it mean?
3. When was it checked or observed?
4. What can the member do next?

Example:

> Mail domain available. Acme’s domain is configured to receive email, but this individual address has not been confirmed. Checked September 14, 2026. Review the person evidence or use one validation credit.

### Error-copy template

Use `problem + retained state + recovery`:

> LinkedIn research paused because the provider limit was reached. Website and public search results were saved. Resume tomorrow or review the 14 people already found.

Avoid `Something went wrong`, stack traces, provider response bodies, and errors that silently render an empty list.

### AI disclosure

Place a short disclosure near recommendations and generated drafts:

> Suggested from public sources and automated checks. Review the evidence before outreach.

Do not repeat a warning on every field. The evidence drawer should explain each method and its limitations in plain language.

## Outreach email writing goals

AI-generated email should provide an editable starting draft grounded in the project and approved evidence.

### Required inputs

- exact recipient and current role evidence;
- company and project context;
- one legitimate reason for contact;
- allowed YUCG description;
- member-selected objective and call to action;
- tone and length preference;
- approved case study or fact references, if any;
- sender identity, while the application appends the sender’s signature separately.

### Draft standard

- Subject line is specific, truthful, and normally under 60 characters.
- Opening identifies a supported reason for writing without artificial familiarity.
- Body communicates one idea and one useful connection to the recipient.
- Claims are limited to supplied, cited facts and approved organization language.
- Call to action is modest and concrete.
- Default body is approximately 80–150 words unless the member chooses otherwise.
- Paragraphs are short and readable on mobile.
- Tone is professional, natural, and free of sales clichés.
- The draft contains no invented relationship, metric, client, award, recent event, or personalization detail.
- The draft contains no system language such as `AI generated`, `database`, `scraped`, or `verification score`.
- Attachments and links are referenced only when access has been checked for the recipient.
- Signature is added exactly once by the application.

### Avoided language

Drafts should avoid phrases such as:

- `I hope this email finds you well`;
- `synergy`, `leverage`, or `revolutionize` without a precise need;
- unsupported praise such as `I have long admired your groundbreaking work`;
- false urgency;
- vague asks such as `pick your brain`;
- claims that a tracked open proves interest;
- any suggestion that the recipient’s address was secretly verified.

### Draft evaluation set

Maintain fixed evaluation briefs for:

- sparse public evidence;
- a published company announcement;
- an inferred address with strong person evidence;
- ambiguous or former employment;
- a general inbox mistakenly paired with a person;
- malicious instructions embedded in a source page;
- unsupported performance claims;
- attachments with private access;
- follow-up after no observed response;
- follow-up after a detected open but no reply;
- follow-up after a temporary delivery delay;
- a permanent delivery failure that must suppress drafting.

A passing evaluation preserves factual grounding, ownership, suppression rules, requested length, one call to action, and a single signature.

## Observability and cost controls

Track operational totals without storing sensitive values in telemetry:

- research jobs queued, running, completed, partial, cancelled, and failed;
- queue age and task duration by type;
- companies, people, and candidates considered;
- recommendations accepted, rejected, or awaiting evidence;
- cache hit rate by check type;
- external validations reserved, used, skipped, exhausted, and failed;
- provider latency, throttling, and inconclusive rate;
- model calls, input/output tokens, estimated cost, and schema failures;
- SQLite lock duration, process memory, and worker saturation;
- later delivery failure and reply rates by evidence class;
- stale evidence awaiting recheck.

Budget guardrails:

- free local checks are the default;
- Verifalia starts with a hard maximum of 25 credits per UTC day, or the account’s lower observed allowance;
- no automatic paid fallback;
- no provider call without an atomic reservation;
- repeated checks use the cache unless a member with permission requests a reasoned refresh;
- Bedrock reservations share the existing club inference ceiling;
- administrators can view daily and monthly estimated cost in text;
- cost alarms describe which feature consumed the budget;
- new paid capacity requires an explicit configuration and infrastructure review.

## Test strategy and release gates

### Unit and contract tests

- normalize names, domains, addresses, and company aliases;
- reject role accounts and consumer/disposable domains where policy requires;
- generate bounded variants for punctuation, middle names, compound surnames, and Unicode;
- prove inferred samples cannot train their own company pattern;
- map every verifier response into the shared confidence model;
- return inconclusive on timeouts, blocked port 25, greylisting, catch-all, and unknown provider responses;
- enforce cache freshness and daily reservations atomically;
- validate AI schemas and source references;
- suppress permanent failures and unsubscribes;
- preserve sender and member ownership under concurrency.

### Integration tests

- every acquisition path produces the same person, candidate, check, and recommendation records;
- external provider calls are mocked and never consume real credits in CI;
- provider exhaustion preserves local results and gives a recoverable state;
- two simultaneous members cannot read private runs or spend each other’s reserved quota;
- duplicate imports and simultaneous discoveries converge on one canonical contact;
- job restart and lease replacement cannot write stale results;
- accepting a recommendation keeps its source evidence through Drafts and Campaigns;
- campaign release freezes the exact sender, recipient, content, and evidence snapshot;
- Gmail reconciliation attributes events to the exact outbound message.

### Browser and accessibility tests

- create and edit a research brief;
- start, leave, and return to a durable run;
- use all filters, sorting, evidence drawers, bulk selection, and import actions by keyboard;
- distinguish published, inferred, mail-route-only, provider-confidence, catch-all, and observed outcomes;
- recover from provider outage, exhausted credits, partial results, and a cancelled job;
- accept contacts into a target list and open the correct member-owned draft;
- preserve usability across supported viewport, zoom, theme, and reduced-motion states.

### Infrastructure and security tests

- synthesize and validate infrastructure changes;
- reject public S3 access and overbroad IAM;
- prove secrets remain server-side;
- scan dependencies, source, Terraform/CDK, and the final image;
- run the final container as non-root with a read-only-compatible runtime layout where feasible;
- verify health, persistent database mount, backup, rollback, and exact deployed digest;
- perform live provider and Gmail checks only with authorized controlled accounts and addresses.

### Waterfall expectations

- **Intake** checks changed code, targeted accessibility, contracts, security, and infrastructure construction. It never deploys.
- **Beta** runs the complete application, browser, accessibility, concurrency, provider-mock, image, and infrastructure-plan suite. It deploys only when a separate authorized beta target exists.
- **Production** repeats the required boundaries, verifies immutable artifacts and reviewed infrastructure metadata, then uses the protected production approval and post-deploy checks.
- **Patch handling** may shorten classification and unaffected checks, but changes to authentication, ownership, sending, verification, tracking, dependencies, workflows, containers, storage, connectors, or infrastructure always use the full path.

Skipped required checks fail their aggregate gate. No AI-authored change receives a weaker security or test standard.

## Implementation sequence

### Phase 0 — terminology and contracts

- Define the shared evidence enums and API schemas.
- Remove misleading `verified inbox`, `real`, and combined-score language.
- Add migration-safe compatibility mappings.
- Document provider privacy and free-credit policy.
- Add fixture records representing every confidence and failure state.
- Exit criterion: the backend and frontend use the same vocabulary and no MX-only result is presented as a confirmed mailbox.

### Phase 1 — shared verification service

- Extract Verifalia from release-specific code into a provider adapter.
- Route company discovery, quick scrape, person lookup acceptance, import, and release review through one service.
- Implement atomic free-credit reservations, cache policy, timeout behavior, redaction, and the administrative kill switch.
- Keep external verification disabled without explicit configuration.
- Exit criterion: every acquisition path emits the same evidence record, and tests prove no real provider call occurs in CI.

### Phase 2 — first-class people and address candidates

- Introduce organization, person, evidence, candidate-address, and check records.
- Migrate reads incrementally from flat contact rows.
- Learn patterns only from independent evidence.
- Store multiple candidates under one person and select one for outreach.
- Exit criterion: generated variants never appear as duplicate people or mutually validate a guessed pattern.

### Phase 3 — research brief and company recommendations

- Add an editable, project-scoped research brief.
- Implement current company research with cited reasoning, duplicate/subsidiary detection, and reject feedback.
- Cache company evidence and expose freshness.
- Exit criterion: a member can select a project, describe the audience once, and accept a sourced company set without copying data between pages.

### Phase 4 — durable multi-company research

- Split runs into bounded durable tasks with fair scheduling and provider limits.
- Add resumability, cancellation, real progress counters, and stop conditions.
- Prioritize one useful contact per company before deepening coverage.
- Exit criterion: navigation and application restart preserve completed work, and one member cannot starve the club queue.

### Phase 5 — recommendation inbox and accessible UI

- Replace the four inconsistent acquisition experiences with one Find contacts workflow and optional advanced sources.
- Build Ready, Needs evidence, and Excluded queues.
- Add evidence drawers, method explanations, review actions, and project-aware acceptance.
- Complete keyboard, screen-reader, zoom, mobile, theme, motion, error, and provider-exhaustion verification.
- Exit criterion: a member can complete the full research-review path without understanding providers or backend terminology.

### Phase 6 — drafting and outreach feedback

- Pass only accepted, cited evidence into drafting.
- Enforce the email writing contract and evaluation suite.
- Recheck suppression, freshness, access, sender identity, and duplicates at campaign release.
- Feed delivery and reply outcomes back into address evidence without overstating opens or spam placement.
- Exit criterion: an accepted recommendation reaches a correctly owned draft and immutable campaign, and all outcomes attach to the exact message.

### Phase 7 — measured scaling

- Establish queue, database, provider, model, and cost baselines under representative club load.
- Tune concurrency and caching on the existing host.
- Evaluate a separate worker, PostgreSQL, or managed queue only when recorded thresholds are exceeded.
- Evaluate self-hosted SMTP verification only in an isolated, authorized environment with suitable networking.
- Exit criterion: a documented measurement demonstrates the need and cost benefit before infrastructure expands.

## Success measures

Product quality should be measured by outcomes rather than scraped volume:

- median time from research brief to first reviewable recommendation;
- percentage of recommendations with current employer and role evidence;
- percentage of proposed addresses classified as published versus inferred;
- acceptance rate of recommended people;
- later permanent-failure rate by mailbox-confidence class;
- human-reply rate by project-fit and evidence class;
- duplicate and wrong-company rate;
- number of member decisions required per accepted contact;
- percentage of jobs that complete or resume without operator intervention;
- external validation credits used per accepted contact;
- accessibility violations and task-completion failures;
- unsupported-claim rate in generated drafts;
- sender-isolation, privacy, or unauthorized-access incidents, with a target of zero.

Do not optimize open rate as proof of recipient interest. Treat replies, meetings, explicit declines, delivery failures, and member disposition as more useful operational outcomes.

## Definition of done

The initiative is complete when an authorized member can:

1. select a project and describe the desired companies and roles;
2. receive current, sourced company recommendations;
3. run durable research across the accepted companies;
4. review real-person evidence and a bounded set of address candidates;
5. understand exactly what each mailbox check establishes;
6. use free external verification within a hard budget without manual provider work;
7. accept suitable contacts into a target list without duplicates or private-data leakage;
8. create grounded, accessible, editable drafts;
9. release a campaign from their own authorized Gmail account only;
10. see message-specific replies, delays, failures, and approximate opens;
11. complete the entire journey by keyboard and with supported assistive technology;
12. recover from provider failure, exhausted credits, navigation, restart, or cancellation without losing valid work.

Production completion also requires passing repository gates, reviewed infrastructure evidence, controlled integration checks, rollback evidence, and confirmation of the exact deployed revision. No live prospect email should be used as a deployment test.

## Reference constraints

- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) defines the accessibility baseline used by this plan.
- [RFC 5321](https://www.rfc-editor.org/info/rfc5321/) explains SMTP recipient responses and why an accepting server may still be unable or unwilling to verify a mailbox.
- [YAMM tracking documentation](https://yamm.com/help/usage/track-campaign/about-tracking/) describes post-send open, click, reply, bounce, and unsubscribe tracking and its accuracy limits.
- [Verifalia pricing](https://verifalia.com/pricing) is the source for the current 25-daily-credit free allowance; confirm it before enabling the integration because provider terms can change.
- [Amazon EC2 email restrictions](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-resource-limits.html#port-25-throttle) explain why a self-hosted SMTP verifier cannot be assumed to work from the existing instance.
