# Outreach pipeline

This is the operational contract for finding contacts, preparing personalized mail, sending from member accounts, and reporting outcomes.

## Member path

1. **Target lists** select companies from the club prospect dataset.
2. **People** records proposed contacts, evidence, and keep/drop decisions. Kept contacts link directly into Drafts with the target-list filter preserved.
3. **Find contacts** creates a durable, member-owned company search. Website and permitted provider evidence are merged, addresses are normalized, and mail routing is checked. Import moves accepted results into the club contact catalog.
4. **Drafts** stores drafts under the current member. A bulk template must use explicit fields such as `{{first_name}}` or `{{company}}`; otherwise the open editor is applied only to its current recipient and other recipients keep their own saved drafts.
5. **Campaigns** copies one subject and body per recipient. The owner previews, repairs, or removes each recipient before release.
6. **Release** freezes the sender, recipient, content, signature, and follow-up steps. Subsequent contact edits cannot retarget a released message.
7. **Dispatch** claims a small batch transactionally and sends through the campaign owner's Gmail OAuth account. A provider timeout remains quarantined for reconciliation. A rejection before submission is safe to retry after the member fixes Gmail access.
8. **Results** attaches opens, replies, delivery delays, and bounces to the exact outbound message. Open pixels are approximate; replies and Gmail delivery notifications are stronger evidence.

## Address confidence

The application can prove address syntax and whether a domain has a mail route without sending mail. Optional SMTP `RCPT TO` probing never issues `DATA`, but many corporate servers block probes or accept every recipient. Those outcomes remain **mailbox unconfirmed**. Only a later delivery, bounce, or reply supplies operational mailbox evidence.

The UI and API must not label MX, inferred patterns, AI output, or an accepted SMTP probe as inbox verified.

This matches the boundary in the SMTP standard: receiving systems may disable `VRFY` for privacy and return `252` when they cannot verify a user. YAMM likewise learns its useful states after a campaign is sent: its official tracking report covers opens, clicks, replies, bounces, and unsubscribes. The club implementation therefore uses local syntax, DNS/MX, source evidence, and optional no-`DATA` probing before send, then uses the tracking pixel and the member's Gmail metadata after send. See [RFC 5321](https://www.rfc-editor.org/info/rfc5321/), [YAMM tracking documentation](https://yamm.com/help/), and [Google's Gmail sending guidance](https://support.google.com/mail/answer/81126).

## Scale and ownership

- Contacts without an owner are shared club catalog records. An assigned contact is visible to its member and administrators.
- Drafts, discovery runs, campaigns, Gmail credentials, immutable dispatches, and reconciliation are member-bound.
- Duplicate additions resolve to one campaign recipient. A recipient can produce only one initial dispatch key.
- One failed or inactive sender cannot stop other members' scheduled campaign batches.
- Initial sends reserve against a configurable member-wide daily ceiling (`CAMPAIGN_DAILY_SEND_LIMIT`, default 100), including uncertain sends. Parallel campaigns cannot multiply that allowance.
- The club-wide Bedrock reservation ceiling also covers discovery ranking, so contact research cannot bypass the inference budget used by Studio.
- Discovery runs use the SQLite-backed queue. One active run per member and a bounded club queue protect the single EC2 host. Expiring fenced leases prevent overlapping workers from writing the same run; inactive members are rejected before provider work. One automatic recovery is allowed after interruption, after which a member must start a new search.
- The current one-process EC2 deployment is deliberate. S3 stores documents and backups, while transactional outreach state remains on the encrypted application volume. Measure queue age, database lock time, provider use, and host memory before adding workers or PostgreSQL.
- A Find people run treats `max_prospects` (UI "How many people", default 60) as when to stop searching, not a limit on people: every qualified person a run finds is saved, even past the target. After the first pass it runs extra rounds of new queries until the target is met, a round adds fewer than `DISCOVERY_ROUND_MIN_NEW` (2), `DISCOVERY_MAX_ROUNDS` (4) extra rounds are spent, or `DISCOVERY_RUN_BUDGET_SEC` (240) elapses. People who fail verification are replaced by more searching, never let through to reach the number. A shortfall is recorded in `research_json.shortfall` with a reason: `exhausted` (the sources had nobody more), `round_cap`, `time_budget` or `search_failed` (the search stopped, not the company). Worst case is about 43 TinyFish searches per run (`DISCOVERY_ROUND_QUERIES` per extra round). Discovery searches are not metered by `reserve_tinyfish_call`; only TinyFish's shared upstream limit bounds them.
- A person found through web search is kept only when their own headline names the company, or one of its group units, as their employer; company-site and SEC/Companies House roster people keep their own evidence. Everyday-word brands (Amazon, Apple, Meta, Target) must be the employer exactly; short acronym firms may lead a division ("IBM Consulting", "KPMG Advisory", "EY-Parthenon"). Group-unit people are tagged `evidence_json.group_unit`. Shared mailboxes (info@, sales@, careers@, …) are rejected by `contact_scraper.is_role_based_local_part` everywhere.
- The **catalog builder** (`services/catalog_builder.py`, Admin → Operations, off by default; club setting `builder_enabled`) works down the company register in the background: SEC-listed companies in market-cap order (the first 500 approximate the S&P 500; the register has no exchange, so "then Nasdaq" is the rest of SEC-listed by rank), then Companies House GROUP and FULL filers, then everything else in the register's value order. A 60-second tick queues one company at a time as an ordinary discovery run (`origin='builder'`, target `BUILDER_TARGET_PEOPLE`, 60) owned by a `system`-role account that no session, API key or Google sign-in resolves to. It skips companies anyone searched within `BUILDER_RECRAWL_DAYS` (30) and pauses while the club has made `BUILDER_MAX_SEARCHES_PER_HOUR` (250) or `BUILDER_MAX_SEARCHES_PER_DAY` (6000) real upstream searches, or has reserved more than `BUILDER_MAX_AI_SHARE` (0.5) of the hourly Bedrock budget. The drain claims member runs before any builder run and never takes a builder run while a member run waits. The run's own verification and AI review trim what it finds; people stay in `yucgoutreach_prospects`, shown on the Find page to every member who opens that company (the newest run per company wins), and reach contacts only when a member keeps them. Progress is kept in `catalog_builder_companies`.

## Required regression evidence

`backend/tests/test_outreach_pipeline.py` proves duplicate-add idempotency, legacy duplicate quarantine, private-contact and target-list isolation, terminal bounce preservation, reconnect-safe retries, per-campaign scheduler isolation, one-active-search admission, active-member enforcement, fenced job claiming, and bounded restart recovery without calling Gmail or discovery providers.

The broader gates run the isolated backend suite, frontend lint/build and Playwright checks, infrastructure tests, security scans, and image verification before branch promotion.
