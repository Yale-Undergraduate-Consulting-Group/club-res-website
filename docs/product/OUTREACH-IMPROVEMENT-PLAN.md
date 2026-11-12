# Outreach platform audit and plan

Audited against production on 2026-09-20, not against intent. Every claim below
is either a line of code or a row in the live database.

## The single most important fact

**Nothing has ever been sent.** `campaign_contacts` holds 16 rows, all
`pending`; `sent_at` is null on every one; `outreach_dispatches` is empty;
`follow_up_sequences` has no rows; `contact_notes` and `contact_activities` are
empty. Two members exist (`andre.h.costa@`, `aaron.combs@`) and 25 contacts, all
owned by member 1.

So the first half of the funnel — find companies, find people, draft — has been
exercised hard, and the second half — send, reply, follow up, hand off — has
never run once outside tests. Every finding about the second half is a
prediction about code that has not met reality yet, and the plan below is
ordered accordingly: the cheapest way to find the real bugs is to complete one
small campaign end to end before building anything new.

## What works

- The register is now one index: about 200,000 companies (one entry per company
  under its public name, funds and vehicles hidden) across six kinds, 392k named
  officers, with the club's 620 curated rows folded in as `club_targets`.
- Selection in the register produces a target list, the object the rest of the
  app consumes (`app/routers/releases.py`).
- The mailbox-proof gate sends one address to an unproven company and holds the
  rest (`app/services/send_gating.py`).
- **Format proof no longer comes from an MX-only scrape.**
  *Resolved 2026-11-10: web-found addresses are unproven observations; only a
  no-bounce delivery after the grace period, a human reply, or a member's
  explicit format assertion proves the host. Outreach migration 3 corrects the
  old scrape-inflated counts.*
- **Successful sends now teach the format registry.**
  *Resolved 2026-11-10: successful Gmail sync records one delivery proof after
  the bounce grace period. The event is idempotent, and a later permanent
  bounce removes that address's proof, lowers confidence, and holds the host.*
- Follow-up plumbing is coherent. Release snapshots one immutable intent per
  step (`app/routers/campaigns.py:632`), the job sends when `days_after` has
  elapsed and `replied_at IS NULL` (`app/services/follow_up_job.py:70-85`), and
  a reply stops the sequence. I suspected a field-name mismatch on `delay_days`
  and there is not one: `dispatch_service.snapshot` writes that column.

## Findings

### A. Two members cannot work side by side

This is the weakest area, and it is not a rough edge; it is absent.

1. **Ownership is a silent wall.** `yucgoutreach_import.py:116-120`: when a
   contact's email already belongs to another member, the row is counted in
   `skipped` and dropped. The second member sees a smaller number and is never
   told that a teammate holds that person, or who.
2. **Nothing claims a company.** There is no lock, claim or assignment table
   (`sched`, `lock`, `assign` searched — only `user_project_assignments`, for
   projects). Both members can run Find people on the same company, build
   separate campaigns, and mail the same people on the same day. Nothing
   prevents it and nothing warns.
3. **The only cross-member guard is `candidate_suppressions`**, and it means
   "this address bounced or opted out", not "a teammate already wrote to them".
4. **Notifications are Slack-only** (`notification_digest_job.py`), and the
   `SLACK_*` variables are deliberately empty. In practice there are no
   notifications at all.

### B. Follow-ups are rigid, and can stall invisibly

5. **Content is frozen at release.** Every follow-up body is snapshotted when
   the campaign is released. A step sent three weeks later cannot mention
   anything learned since — not a reply from elsewhere in the same company, not
   a funding round, not the fact that a colleague's email to their CFO bounced.
   The immutability is right for the *initial* send (it is what makes the
   dispatch ledger trustworthy); applying it to a message that has not been
   composed yet is the wrong trade.
6. **Steps are static text.** `follow_up_steps` is `days_after`, `subject`,
   `body`. No conditions, no branching, no per-recipient timing.
7. **A campaign that needs attention silently stops following up.** The job only
   considers `campaigns.status = 'sent'` (`follow_up_job.py:25`). The new
   mailbox-proof gate moves a campaign to `needs_attention` when a probe
   bounces, which is correct — but the follow-ups for everyone already mailed in
   that campaign then stop, with nothing saying so.
   *Resolved 2026-11-10: the job follows up for `sent`, `needs_attention` and
   `releasing` campaigns and stops only for `paused`; the gate's hold concerns
   unsent rows. Follow-ups also count against the sender's own daily limit.*
8. **The only stop condition is `replied_at`.** Not stopped by: a teammate
   getting a reply at the same company, a bounce, an opt-out phrase in a reply,
   a meeting being booked, or a cap on total touches per company.
   *Resolved 2026-11-10 (bounce, opt-out reply, out of office; the rest stays open):*
   a bounce and any reply (an opt-out is a reply) stop the sequence, and an
   automatic reply defers the next step to two business days after it
   (`follow_up_job.py`). *Resolved 2026-11-12 (teammate reply, touch cap):* a reply
   from anyone at the company, by any member, stops every follow-up there and holds
   pending first sends, and `COMPANY_SEND_CAP` limits first touches per company
   across the club (`send_gating.py`). A booked meeting is still open.
9. **No send window.** The drain runs every five minutes and sends whenever it
   fires. No business hours, no recipient timezone, no "not 03:00 on Sunday".
   For a club whose account reputation is a member's personal Gmail, this is the
   cheapest deliverability win available.
   *Resolved 2026-11-10: initial sends and follow-ups now stay queued outside
   recipient-local Monday–Friday business hours. The company register supplies
   a small US-state/UK zone map, unknown companies default to Eastern, and
   `SEND_WINDOW_HOURS` configures the shared window.*

### C. Drafting does not learn

10. **Outcomes feed email formats, never wording.** Delivery and reply proof moves
    pattern confidence; nothing records which *angle* earned a reply. The app
    cannot answer "which opening works", which is the one question worth
    asking after fifty sends.
    *Resolved 2026-11-12 (recording and reporting; feeding outcomes into the
    Studio brief stays open): every campaign row carries the saved template and
    the approach (`template_id`, `angle`, outreach migration 5), and Admin →
    Metrics "What earns replies" shows people written to, replies and reply rate
    per template and approach, judged only from five cases. Reply rate per sector
    is not reported.*
11. **Rejections cost quota quietly.** `_generate_with_retry` spends one
    generation, and on validator rejection spends another. The member sees
    "did not pass the draft rules" and no reason.

### D. Ease of use

12. From a target list it is still Find people → Studio → Campaign → Release as
    four destinations. The one-click flow (`outreach_flow.py`) does all of it,
    but only for a single company, reachable only from Find people.
    *Resolved 2026-11-12 (target list and Directory; the register's selection
    bar stays open): a Worklist and ticked Directory people each open one
    "Queue outreach" panel: choose a template, read who is ready and who is held
    and why, create the campaign and release it. `POST /api/campaigns/build` now
    resolves a `target_list_id` server-side under the list's access rule. No model
    is involved; where AI drafting is offered and the model is unavailable, the
    member is told so and offered a template instead.*
13. **Find people knows level, not who the club should write to first.** A found
    person carried a title that was sometimes blank, no alumni signal anywhere,
    and no way to ask for, or narrow to, one function (finance, operations...).
    *Resolved 2026-11-12: a person tied to Yale by the text a search read (a
    school, a class year, a degree; never a lock or an appliance store) is stored
    with that evidence, listed and ticked first, and badged "Yale" with the
    evidence on hover (`yale_evidence`, `yale_signal`, migration `outreach:6`).
    Titles classify into nine functions (`classify_function`); the picker filters
    by function and a search may ask for one, swapping generic first-wave
    searches for that function's titles without adding any. A missing title is
    recovered from the headline or snippet already collected, and left blank when
    none states one. Open: pasted LinkedIn text has no entry point to feed
    `yale_evidence`, and no extra page fetch is made for a person still without a
    title.*

## Plan

Ordered so that each phase makes the next one cheaper, and so that the earliest
phases are the ones that produce evidence rather than assume it.

### Phase 1 — Send one real campaign (no new code)

Pick three companies from the register, run the flow, release, and watch what
happens. Expected outputs: the first real `outreach_dispatches` rows, the first
bounce or reply through `gmail_reply_sync`, and the first exercise of the
mailbox-proof gate against a live domain.

This is a prerequisite, not a formality. Findings 5-9 are predictions; one real
campaign converts them into facts and will almost certainly surface two or three
bugs that no amount of reading finds.

### Phase 2 — Make two members safe (findings 1-4)

- `company_claims` table: `(company_domain, member_id, claimed_at, expires_at)`.
  A claim is taken when a flow or campaign starts, expires after 30 days of no
  activity, and is visible on the register row and in Find people.
- Replace the silent import skip with an explicit outcome: "3 people here are
  worked by Aaron — ask him, or take the rest."
- A cross-member pre-send check: block, with an override, mailing an address a
  teammate has mailed in the last N days. This is the check that prevents the
  club looking disorganised to a client.
- In-app notifications, not Slack: a `notifications` table plus a header
  indicator. Slack stays an optional mirror.

Acceptance: two members working the same company see each other before sending,
and no recipient can receive two first-touch emails from the club.

### Phase 3 — Smart follow-ups (findings 5-9)

- **Compose follow-ups at send time, not at release.** Keep the immutable
  snapshot for the initial send. For step N, generate when it comes due, with
  the thread so far as context, and snapshot it at that moment. Same audit
  guarantee, current content.
- **Company-level stop conditions.** Any reply, meeting or opt-out from anyone
  at a domain stops every sequence into that domain. Add a touch cap per
  company.
- **Send windows.** Recipient-local business hours, configurable per member,
  with a jitter. Cheap and directly improves deliverability.
- **Surface a stalled sequence.** When a campaign leaves `sent`, say which
  follow-ups are paused and why.

Acceptance: a reply to any member stops every sequence at that company within
one tick; no email leaves outside the configured window.

### Phase 4 — Drafting that learns (findings 10-11)

- Record the angle on every sent message and join it to replies. One honest
  table, one view: replies per angle per sector. No model changes.
- Feed *observed* outcomes into the Studio brief: "question openings get no
  replies at hospitals" is worth more than any prompt tuning.
- Show the validator's reason to the member, with the offending sentence.

Acceptance: after fifty sends the app can rank angles by reply rate, from the
send ledger and nothing else.

### Phase 5 — One action from a target list (finding 12)

Extend the existing flow to accept a list rather than a company, so the
selection bar in the register can offer "find people and draft for all of
these". The state machine already handles discovering → importing → drafting →
ready; it needs a list-shaped input and a progress row per company.

## What I would not build

- A second database or analytics store. SQLite on the box answers every question
  above.
- Per-member mail domains or a sending service. The club's volume is tens of
  emails a week; the constraint is reputation and coordination, not throughput.
- Anything that lets a sequence send without a human having released the
  campaign it belongs to.
