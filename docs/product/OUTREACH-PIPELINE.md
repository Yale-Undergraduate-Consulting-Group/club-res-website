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

## Required regression evidence

`backend/tests/test_outreach_pipeline.py` proves duplicate-add idempotency, legacy duplicate quarantine, private-contact and target-list isolation, terminal bounce preservation, reconnect-safe retries, per-campaign scheduler isolation, one-active-search admission, active-member enforcement, fenced job claiming, and bounded restart recovery without calling Gmail or discovery providers.

The broader gates run the isolated backend suite, frontend lint/build and Playwright checks, infrastructure tests, security scans, and image verification before branch promotion.
