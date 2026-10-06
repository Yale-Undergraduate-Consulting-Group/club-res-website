# Outreach tracking

Campaign emails and sequence follow-ups now have independent `outreach_messages`
records. Each includes an opaque pixel token, an RFC Message-ID, the sending user,
recipient snapshot, Gmail message/thread IDs, and the confirmed send time.
`outreach_events` retains first pixel detection and matched Gmail receipts; repeated
Gmail scans do not duplicate events. Campaign/contact columns remain compatibility
summaries for the existing UI.

## Runtime

- Pixel URLs use `API_BASE_URL`, then `BACKEND_URL`, then localhost for local work.
  The existing AWS bootstrap supplies `BACKEND_URL`; no infrastructure redeploy is
  needed. An explicit `API_BASE_URL` still takes precedence.
- New pixels use `/api/track/message/{opaque-token}`. Old numeric pixel URLs remain
  supported for previously sent mail. Pixel responses disable caching.
- Gmail synchronization runs every two minutes while the application is running.
  A per-sender lock prevents simultaneous scheduled/manual scans in this process.
- The first scan considers mail since the earliest recorded send; subsequent scans
  use the last successful scan start with a five-minute overlap. All Gmail result
  pages are processed, including spam/trash; sent/draft messages are excluded.
- Metadata is used for reply correlation. Suspected delivery reports are retrieved
  as raw MIME and parsed for recipient, action, enhanced status, and original
  Message-ID. Separate-thread reports can match their original send. Ambiguous
  reports are not attributed from the recipient address alone.
- Manual sync returns immediately and runs after the HTTP response, avoiding the
  configured CloudFront origin timeout. Progress is exposed through `/api/outreach/sync-status`.
  If the container stops mid-scan, the next scheduled scan retries from the saved
  success cursor.
- Scan failures leave the success cursor unchanged and expose an error. Campaign
  and Pipeline show the current member's last successful sync. Campaign details
  refresh every 15 seconds while visible.

## Meaning of events

- **Open detected**: the image was requested, not proof a human read the message.
  Only the first detection per message is stored, not a purported read count.
- **Replied**: incoming sender and message references/thread match a recorded send.
  A later outgoing reminder does not erase an earlier reply. Recognizable
  `Auto-Submitted`/autoreply headers produce a separate automatic-reply event.
- **Delivery delayed**: structured DSN `Action: delayed`; not a permanent bounce.
- **Delivery failed**: structured DSN `Action: failed`; the diagnostic is retained.
  This does not automatically mark the mailbox nonexistent.

No third-party tracking service, SMTP probing, or paid verification API is used.
Gmail read access is needed on the connected sender account. No recipient account
access is used.

## Limits and deployment

Deploy through the existing GitHub Actions image/SSM pipeline. Database tables are
created at application startup; existing campaign data is preserved. Old campaign
rows are backfilled during their sender's first scan. Previously overwritten
follow-up IDs and opens from before this change cannot be reconstructed.

Open collection requires the EC2 application to remain available. Unlike Gmail
receipts, pixel requests missed while the host is stopped cannot reliably be
recovered. Nonstandard/unmatched bounce notices, replies from different sender
addresses, and automatic replies lacking identifying headers may require review.
The two-minute interval is a schedule, not a guarantee when Gmail is unavailable
or a large scan is still running.

## Verification

`backend/venv/bin/python backend/tests/test_tracking.py` uses temporary SQLite and
mock Gmail transport; it sends no email. The normal backend script suite and
`npm run build --prefix frontend` cover compatibility/build checks. A live rollout
should additionally use controlled club test mailboxes to verify receipt behavior
through the public HTTPS URL; local tests do not validate deployed Google tokens
or actual mail-client image behavior.

## AWS verification performed locally

The CDK stack routes the public HTTPS distribution through a VPC origin to port 80
on EC2; Docker maps that port to FastAPI port 8000. CloudFront caching is disabled.
The bootstrap supplies BACKEND_URL and maps the persistent host data directory to
/data; the image/SSM restart path keeps the same mapping and environment file.
These tracking changes do not require a stack deployment or a new AWS service.

Regression checks exercise the public pixel route without authentication, cache
headers, database persistence through two application lifespans, and background
manual sync dispatch. Infrastructure TypeScript compilation also passes. These
checks do not establish the current live stack state, EBS mount health, outbound
Gmail access, or production deployment; AWS CLI and Docker were unavailable in the
local session. No live mail was sent.
